"""Optional stdio MCP adapters. No third-party imports in the OpenBridge process."""
import collections
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import sys
import time
from game_control.schema import NAMES as GAME_NAMES, CONTROL_TOOLS as GAME_CONTROL_TOOLS
from background_control.schema import TOOLS as BACKGROUND_TOOLS, NAMES as BACKGROUND_NAMES
from background_control.service import BackgroundService

MAX_MESSAGE = 12 * 1024 * 1024

class ActivityMonitor:
    """Tracks in-flight Computer Use work so the local GUI can show it live.

    Local observability plus a soft pause gate. Nothing here is exposed as an
    MCP tool: a remote chat can never pause, resume, or conceal its own actions.
    Pausing refuses new work without killing children, so browser/Blender
    context and the public tunnel survive a pause/resume cycle.
    """

    def __init__(self):
        self._guard = threading.Lock()
        self._seq = 0
        self._active = {}
        self._last_finished = None
        self._paused = False
        self._counters = {'ok': 0, 'error': 0}

    def set_paused(self, paused):
        with self._guard:
            self._paused = bool(paused)
            return self._paused

    @property
    def paused(self):
        with self._guard:
            return self._paused

    def check_allowed(self, label):
        with self._guard:
            if not self._paused:
                return
        raise PermissionError(
            'Paused by the local user (OpenBridge GUI button "暂停 AI 操作"). '
            'No action was performed and the adapters stay connected. '
            'Do not retry in a loop: tell the user you are paused and wait '
            'until they press "恢复 AI 操作".')

    def begin(self, label):
        with self._guard:
            self._seq += 1
            self._active[self._seq] = {'label': label, 'started': time.monotonic()}
            return self._seq

    def end(self, seq, ok=True):
        with self._guard:
            record = self._active.pop(seq, None)
            if record is None:
                return 0.0
            elapsed = time.monotonic() - record['started']
            self._counters['ok' if ok else 'error'] += 1
            self._last_finished = {'label': record['label'], 'elapsed': elapsed,
                                   'ok': bool(ok), 'at': time.time()}
            return elapsed

    def snapshot(self):
        """Cheap non-blocking view for the GUI poller."""
        with self._guard:
            running = sorted(self._active.values(), key=lambda r: r['started'])
            now = time.monotonic()
            return {'paused': self._paused,
                    'busy': bool(running),
                    'current': running[0]['label'] if running else None,
                    'elapsed': (now - running[0]['started']) if running else 0.0,
                    'pending': len(running),
                    'last': dict(self._last_finished) if self._last_finished else None,
                    'counters': dict(self._counters)}


class StdioMCP:
    def __init__(self, command, cwd, env=None, notify=None):
        self.command, self.cwd, self.env = command, str(cwd), env
        self.process = None
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.responses = queue.Queue(maxsize=128)
        self.errors = collections.deque(maxlen=20)
        self.next_id = 0
        self.notify = notify

    def start(self):
        if self.closed.is_set():
            raise RuntimeError('Adapter was stopped; restart Bridge to enable again')
        self.process = subprocess.Popen(self.command, cwd=self.cwd, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if self.closed.is_set():
            self.close()
            raise RuntimeError('Stopped during startup')
        self.readers = [threading.Thread(target=self._read, daemon=True),
                        threading.Thread(target=self._stderr, daemon=True)]
        for reader in self.readers:
            reader.start()
        try:
            self.initialize_result = self.request('initialize', {'protocolVersion':'2024-11-05','capabilities':{},
                'clientInfo':{'name':'OpenBridge-ComputerUse','version':'1.0'}}, timeout=60)
            self._write({'jsonrpc':'2.0','method':'notifications/initialized'})
        except Exception:
            self.close()
            raise

    def _write(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8') + b'\n'
        if len(data) > MAX_MESSAGE:
            raise ValueError('MCP request too large')
        with self.write_lock:
            if self.closed.is_set() or self.process is None or self.process.poll() is not None:
                raise RuntimeError('Child MCP is not running')
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def _read(self):
        try:
            while not self.closed.is_set():
                line = self.process.stdout.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE:
                    raise RuntimeError('Child MCP response exceeds 12 MiB')
                try:
                    msg = json.loads(line)
                except (ValueError, UnicodeError):
                    self.errors.append('Ignored non-JSON stdout line')
                    continue
                if 'method' in msg:
                    if 'id' not in msg and self.notify:
                        self.notify(msg['method'], msg.get('params', {}))
                    if 'id' in msg:
                        self._write({'jsonrpc':'2.0','id':msg['id'],
                            **({'result':{}} if msg['method']=='ping' else
                               {'error':{'code':-32601,'message':'Client requests not supported'}})})
                    continue
                if 'id' in msg:
                    self._accept_response(msg)
        except Exception as exc:
            self.errors.append(str(exc))
        finally:
            self._reader_disconnected('Child MCP disconnected')

    def _accept_response(self, message):
        self.responses.put_nowait(message)

    def _reader_disconnected(self, reason):
        try:
            self.responses.put_nowait({'error': {'message': reason}})
        except queue.Full:
            pass

    def _stderr(self):
        try:
            while True:
                line = self.process.stderr.readline(4096)
                if not line:
                    return
                self.errors.append(line.decode('utf-8', errors='replace').strip())
        except (OSError, ValueError):
            pass

    def request(self, method, params=None, timeout=60):
        # Serialize calls: UI actions must not race one another.
        with self.lock:
            self.next_id += 1
            ident = self.next_id
            self._write({'jsonrpc':'2.0','id':ident,'method':method,'params':params or {}})
            try:
                msg = self.responses.get(timeout=timeout)
            except queue.Empty:
                self.close()  # Never retry UI actions: outcome may already have occurred.
                raise TimeoutError('MCP call timed out; adapter stopped. Action outcome is unknown.')
            if msg.get('id') not in (ident, None):
                self.close()
                raise RuntimeError('Unexpected MCP response id; adapter stopped')
            if 'error' in msg:
                raise RuntimeError(str(msg['error'].get('message','MCP error')))
            result = msg.get('result')
            if not isinstance(result, dict):
                raise RuntimeError('Invalid MCP result')
            return result

    def close(self):
        self.closed.set()
        p = self.process
        if p is not None and p.poll() is None:
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill','/F','/T','/PID',str(p.pid)],
                        capture_output=True, timeout=8, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                else:
                    p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except OSError:
                    pass
        if p is not None and p.poll() is not None:
            for reader in getattr(self, 'readers', []):
                if reader is not threading.current_thread():
                    reader.join(timeout=1)
            for stream in (p.stdin, p.stdout, p.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        self._reader_disconnected('Adapter stopped')

class GameClient(StdioMCP):
    """Ask broker to release before termination; notifications bypass request serialization."""
    def __init__(self, command, cwd, env, consent, log):
        self.released = threading.Event()
        self.closing = threading.Event()
        self.close_guard = threading.Lock()
        self.pending_guard = threading.Lock()
        self.pending_requests = {}
        self.transport_error = None
        self.consent, self.log = consent, log
        super().__init__(command, cwd, env, notify=self._notification)

    def _accept_response(self, message):
        # Control receipts may arrive before the long-running action receipt.
        with self.pending_guard:
            mailbox = self.pending_requests.get(message.get('id'))
        if mailbox is None:
            raise RuntimeError('Unexpected game broker response id; transport is unusable')
        mailbox.put_nowait(message)

    def _reader_disconnected(self, reason):
        with self.pending_guard:
            self.transport_error = self.transport_error or reason
            mailboxes = list(self.pending_requests.values())
        for mailbox in mailboxes:
            try:
                mailbox.put_nowait({'error': {'message': self.transport_error}})
            except queue.Full:
                pass

    def request(self, method, params=None, timeout=60):
        # Only the game broker is multiplexed. The manager still serializes all
        # input-producing calls; ordinary desktop/browser/Blender stay unchanged.
        mailbox = queue.Queue(maxsize=1)
        with self.pending_guard:
            if self.closed.is_set() or self.closing.is_set() or self.transport_error:
                raise RuntimeError(self.transport_error or 'Game broker is stopping')
            if len(self.pending_requests) >= 16:
                raise RuntimeError('Too many pending game broker requests')
            self.next_id += 1
            ident = self.next_id
            self.pending_requests[ident] = mailbox
        started = time.monotonic()
        try:
            self._write({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}})
            try:
                message = mailbox.get(timeout=timeout)
            except queue.Empty:
                self.close()
                raise TimeoutError('Game broker timed out; stopped without retry. Action outcome is unknown.')
            if message.get('id') != ident:
                self.close()
                raise RuntimeError('Game broker disconnected; inspect action status, never replay blindly')
            if 'error' in message:
                raise RuntimeError(str(message['error'].get('message', 'Game broker error')))
            result = message.get('result')
            if not isinstance(result, dict):
                self.close()
                raise RuntimeError('Invalid game broker result')
            result = dict(result)
            metadata = dict(result.get('_meta', {}))
            metadata['openbridge/transport'] = {
                # Includes execution/capture, not HTTP network or model latency.
                'broker_round_trip_ms': round((time.monotonic() - started) * 1000, 3)
            }
            result['_meta'] = metadata
            return result
        finally:
            with self.pending_guard:
                self.pending_requests.pop(ident, None)

    def _notification(self, method, params):
        if method == 'control/consent':
            self.consent(self, params)
        elif method == 'control/auto_approved':
            window = params.get('window', {}) if isinstance(params, dict) else {}
            rule = params.get('rule', {}) if isinstance(params, dict) else {}
            self.log('[GAME INPUT] 受信规则「%s」自动批准窗口: %s (PID %s, %s)' % (
                rule.get('name'), str(window.get('title', ''))[:80], window.get('pid'), rule.get('process')))
        elif method == 'control/released' and params.get('cleanup_confirmed'):
            self.released.set()

    def revoke(self):
        try:
            self._write({'jsonrpc':'2.0','method':'control/revoke'})
        except (OSError, RuntimeError, ValueError):
            pass

    def close(self):
        if self.closed.is_set() or not self.close_guard.acquire(False):
            return
        self.closing.set()
        try:
            if self.process is not None and self.process.poll() is None:
                self.released.clear()
                try:
                    self._write({'jsonrpc':'2.0','method':'control/shutdown'})
                    if not self.released.wait(2.5):
                        self.log('[GAME INPUT] WARNING: input cleanup not confirmed; inspect held keys locally.')
                except (OSError, RuntimeError, ValueError) as exc:
                    self.log('[GAME INPUT] Cleanup channel failed: ' + str(exc))
            super().close()
        finally:
            self.close_guard.release()

# Exclude general-purpose execution, filesystem and clipboard endpoints from desktop forwarding.
DESKTOP_TOOLS = {'App','Snapshot','Screenshot','Click','Type','Scroll','Move','Drag','Shortcut','Wait'}
# No browser_evaluate/run_code/file_upload: these need separate explicit review.
BROWSER_TOOLS = {'browser_navigate','browser_navigate_back','browser_snapshot','browser_take_screenshot',
    'browser_click','browser_type','browser_fill_form','browser_press_key','browser_select_option',
    'browser_hover','browser_drag','browser_tabs','browser_close','browser_resize','browser_wait_for',
    'browser_handle_dialog','browser_find','browser_console_messages','browser_network_requests'}

BLENDER_TOOLS = {'get_addon_status','get_scene_info','get_object_info',
    'get_viewport_screenshot','execute_blender_code','describe_node_type','bpy_api_lookup','export_scene'}

class ComputerUseManager:
    def __init__(self, directory=None, desktop=False, browser=False, log=None, blender=False):
        self.directory = Path(directory or Path(__file__).resolve().parent)
        self.enabled = {'desktop':bool(desktop),'browser':bool(browser),'blender':bool(blender)}
        self.children = {}
        self.tools = {}
        self.guard = threading.RLock()
        self._shutdown = False
        self.game_child = None
        self.game_runtime = {}
        self.game_consents = queue.Queue(maxsize=4)
        # Local GUI checkbox only; never settable through an MCP tool. Applies to the next broker start.
        self.game_trust = False
        self.interaction_lock = threading.RLock()
        self.log = log or (lambda _:None)
        # Local-only activity/pause state for the GUI. Never exposed as an MCP tool.
        self.activity = ActivityMonitor()
        self.background = BackgroundService(self.directory, lambda: self.enabled['desktop'] and not self._shutdown)

    def set_enabled(self, enabled):
        """Local control only. Not exposed as an MCP tool. All adapters toggle together."""
        enabled = bool(enabled)
        if not enabled:
            self.enabled['desktop'] = False
            self.background.stop()
        if not enabled and self.game_child is not None:
            self.game_child.revoke()  # Out-of-band: do not wait for an active tool call.
        if not enabled:
            # Revoke before waiting for a potentially slow child startup handshake.
            for target in self.enabled:
                self.enabled[target] = False
        with self.guard:
            if enabled and self._shutdown:
                raise RuntimeError('Bridge has stopped; cannot re-enable its adapters')
            old_children = list(self.children.values())
            if self.game_child is not None:
                old_children.append(self.game_child)
                self.game_child = None
            self.children = {}
            self.tools = {}
            self.game_runtime = {}
            for target in self.enabled:
                self.enabled[target] = enabled
        for child in old_children:
            child.close()
        self.log('[COMPUTER USE] ' + ('Enabled (lazy start).' if enabled else 'Disabled; children stopped.'))

    def _game_client(self):
        with self.guard:
            if self._shutdown or not self.enabled['desktop']:
                raise PermissionError('Game input requires the local Computer Use switch')
            if self.game_child is None:
                executable = Path(sys.executable)
                if executable.name.lower() == 'pythonw.exe':
                    executable = executable.with_name('python.exe')
                env = os.environ.copy()
                env['PYTHONIOENCODING'] = 'utf-8'
                env['OPENBRIDGE_GAME_TRUST'] = '1' if self.game_trust else '0'
                child = GameClient([str(executable), '-u', '-m', 'game_control.worker'],
                    self.directory, env, self._queue_game_consent, self.log)
                self.game_child = child
                try:
                    child.start()
                except Exception:
                    child.close()
                    raise
            if self.game_child.closed.is_set():
                raise RuntimeError('Input broker stopped; do not replay unknown actions. Inspect locally, then toggle OFF/ON.')
            if not self.enabled['desktop']:
                self.game_child.close()
                raise PermissionError('Computer Use disabled during input broker startup')
            return self.game_child

    def _queue_game_consent(self, child, request):
        try:
            self.game_consents.put_nowait((child, request))
        except queue.Full:
            child.revoke()

    def pop_game_consent(self):
        while True:
            try:
                child, request = self.game_consents.get_nowait()
            except queue.Empty:
                return None
            if child is self.game_child and self.enabled['desktop'] and not self._shutdown:
                return request

    def set_game_trust(self, enabled):
        """Local GUI only. Restarts the broker so the new policy applies; any lease is revoked first."""
        enabled = bool(enabled)
        with self.guard:
            self.game_trust = enabled
            child = self.game_child
            self.game_child = None
            self.game_runtime = {}
        if child is not None:
            child.revoke()
            child.close()
        self.log('[GAME INPUT] 受信游戏窗口自动批准已' + ('开启（规则文件: %LOCALAPPDATA%\\OpenBridge\\game_trust.json）' if enabled else '关闭；恢复逐次本机确认'))

    def approve_game_request(self, request_id, approved):
        # Only GUI invokes this private path; no tool can approve itself.
        child = self.game_child
        if child is None or not self.enabled['desktop'] or self._shutdown:
            raise PermissionError('Local permission revoked')
        return child.request('control/approve', {'request_id':request_id,'approved':bool(approved)}, timeout=5)

    def _command(self, target):
        env = os.environ.copy()
        env.update({'ANONYMIZED_TELEMETRY':'false','PYTHONIOENCODING':'utf-8',
                    'WINDOWS_MCP_SCREENSHOT_SCALE':'0.5','WINDOWS_MCP_WATCHDOG':'off'})
        if target == 'blender':
            exe = self.directory / '.computer-use/blender/Scripts/mcp-for-blender.exe'
            if not exe.is_file():
                raise RuntimeError('Blender MCP dependencies missing; run install_blender_mcp.cmd')
            env.update({'BLENDER_HOST':'127.0.0.1','BLENDER_PORT':'9876',
                        'BLENDER_MCP_SAFE_MODE':'1','BLENDER_MCP_DISABLE_TELEMETRY':'1',
                        'DISABLE_TELEMETRY':'1'})
            return [str(exe)], env
        if target == 'desktop':
            exe = self.directory / '.computer-use/windows/Scripts/windows-mcp.exe'
            if not exe.is_file():
                raise RuntimeError('Desktop dependencies missing; run install_computer_use.cmd locally')
            return [str(exe), 'serve', '--transport','stdio'], env
        node = shutil.which('node')
        cli = self.directory / '.computer-use/browser/node_modules/@playwright/mcp/cli.js'
        if not node or not cli.is_file():
            raise RuntimeError('Browser dependencies missing; run install_computer_use.cmd locally')
        # Isolated context, no CDP attachment and no existing browser profile.
        return [node,str(cli),'--isolated','--browser','msedge'], env

    def _client(self, target):
        if target not in self.enabled:
            raise ValueError('target must be desktop, browser or blender')
        with self.guard:
            if not self.enabled[target]:
                raise PermissionError('Computer Use disabled. Local owner must turn on Computer Use in the GUI (no Bridge restart needed).')
            if target not in self.children:
                command, env = self._command(target)
                child = StdioMCP(command, self.directory, env)
                self.children[target] = child
                try:
                    child.start()
                except Exception:
                    child.close()
                    raise
            child = self.children[target]
            if not self.enabled[target]:
                child.close()
                raise PermissionError('Adapter disabled during startup')
            if child.closed.is_set():
                raise RuntimeError('Adapter stopped; toggle Computer Use OFF then ON locally; inspect action outcome before retrying')
            return child

    def status(self):
        return {'targets':{target:{'enabled':enabled,
                    'running':target in self.children and self.children[target].process is not None
                              and self.children[target].process.poll() is None,
                    'version':{'desktop':'0.8.5','browser':'0.0.81','blender':'2.0.0'}[target]}
                for target,enabled in self.enabled.items()},
                'game_input':{'available':True,'local_window_approval':True,'emergency_key':'F8',
                    'running':self.game_child is not None and self.game_child.process is not None
                              and self.game_child.process.poll() is None,
                    'runtime':dict(self.game_runtime)},
                'background_input':{'available':True,'version':'0.1.0','no_foreground_fallback':True,'no_auto_retry':True,'minimized_supported':False},
                'hot_toggle':True, 'shared_state':True, 'enable_control':'local GUI only',
                'activity':self.activity.snapshot(),
                'warning':'Desktop controls the logged-in desktop, not just the workspace. GUI kill switch is not an OS sandbox.'}

    def list_tools(self, target):
        client = self._client(target)
        allowed = {'desktop':DESKTOP_TOOLS,'browser':BROWSER_TOOLS,'blender':BLENDER_TOOLS}[target]
        results, cursor = [], None
        for _ in range(10):
            reply = client.request('tools/list', {'cursor':cursor} if cursor else {})
            results.extend(t for t in reply.get('tools',[]) if t.get('name') in allowed)
            cursor = reply.get('nextCursor')
            if not cursor:
                break
        game_client, game_runtime = None, {}
        if target == 'desktop':
            game_client = self._game_client()
            reply = game_client.request('tools/list', timeout=10)
            game_tools = reply.get('tools')
            if not isinstance(game_tools, list):
                raise RuntimeError('Game broker returned invalid tool discovery; no cached schema fallback')
            reviewed = [tool for tool in game_tools if isinstance(tool, dict) and tool.get('name') in GAME_NAMES]
            if {tool['name'] for tool in reviewed} != GAME_NAMES or len(reviewed) != len(GAME_NAMES):
                raise RuntimeError('Game broker tool set changed; update/reload both sides before using it')
            if any(not isinstance(tool.get('inputSchema'), dict) for tool in reviewed):
                raise RuntimeError('Game broker tool schema missing')
            results.extend(reviewed)
            results.extend(BACKGROUND_TOOLS)
            game_runtime = reply.get('_meta', {}).get('openbridge/game-control', {})
            if not isinstance(game_runtime, dict):
                raise RuntimeError('Invalid game broker runtime metadata')
        with self.guard:
            if not self.enabled[target] or self.children.get(target) is not client:
                raise PermissionError('Adapter changed during discovery; fetch tools again')
            if game_client is not None:
                if self.game_child is not game_client:
                    raise PermissionError('Game broker changed during discovery; fetch tools again')
                self.game_runtime = dict(game_runtime)
            self.tools[target] = {t['name']:t for t in results}
        return {'target':target,'tools':results, **({'game_runtime':game_runtime} if game_client is not None else {})}

    def _call_game_tool(self, name, arguments):
        child = self._game_client()
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise ValueError('arguments must be an object')
        self.log('[GAME INPUT] ' + name)
        budget = 15
        if name == 'GameAct':
            try:
                budget += min(10, max(0, sum(int(step.get('duration_ms', 0))
                    for step in arguments.get('steps', []) if isinstance(step, dict)) / 1000))
            except (TypeError, ValueError):
                pass
        return child.request('tools/call', {'name':name,'arguments':arguments}, timeout=budget)

    def call_tool(self, target, name, arguments=None):
        # Cancellation/status do not wait behind a long action or PNG encoding.
        # The broker validates the lease; this never grants permission or focuses.
        if target == 'desktop' and name in GAME_CONTROL_TOOLS:
            # Deliberately not pause-gated: releasing or cancelling a lease must
            # stay reachable while paused, otherwise a pause could strand a lease.
            return self._call_game_tool(name, arguments)
        label = '%s/%s' % (target, name)
        self.activity.check_allowed(label)
        seq = self.activity.begin(label)
        self.log('[COMPUTER USE] > %s 开始' % label)
        ok = False
        try:
            result = self._dispatch_tool(target, name, arguments)
            ok = not (isinstance(result, dict) and result.get('isError'))
            return result
        finally:
            elapsed = self.activity.end(seq, ok)
            self.log('[COMPUTER USE] < %s %s %.1fs' % (label, '完成' if ok else '失败', elapsed))

    def _dispatch_tool(self, target, name, arguments=None):
        # Shared desktop: all input-producing calls remain one serialized stream.
        with self.interaction_lock:
            if target == 'desktop' and name in GAME_NAMES:
                return self._call_game_tool(name, arguments)
            game_child = self.game_child
            if game_child is not None and not game_child.closed.is_set():
                state = game_child.request('control/state', timeout=5)
                if state.get('state') not in ('idle',):
                    raise PermissionError('Game window is reserved. Release its lease before using other adapters.')
            if target == 'desktop' and name in BACKGROUND_NAMES:
                if name not in self.tools.get('desktop', {}):
                    raise PermissionError('Discover current desktop schemas before background calls')
                self.log('[BACKGROUND INPUT] ' + name)
                return self.background.call(name, {} if arguments is None else arguments)
            return self._call_adapter_tool(target, name, arguments)

    def _call_adapter_tool(self, target, name, arguments=None):
        client = self._client(target)
        if target not in self.tools:
            self.list_tools(target)
        if name not in self.tools[target]:
            raise PermissionError('Tool is not in the reviewed allowlist')
        if not isinstance(arguments or {}, dict):
            raise ValueError('arguments must be an object')
        if not self.enabled[target] or self.children.get(target) is not client:
            raise PermissionError('Adapter disabled or replaced; fetch tools again')
        # call_tool emits the paired start/end audit lines; typed text and
        # screenshots are still never written to the log.
        result = client.request('tools/call',{'name':name,'arguments':arguments or {}},timeout=60)
        # Return MCP content blocks intact, including images and isError.
        if not isinstance(result.get('content'),list):
            raise RuntimeError('Child returned malformed tool content')
        # Upstream Blender sometimes reports rejected code as text with isError=False.
        if target == 'blender' and any(
                block.get('type') == 'text' and block.get('text', '').lstrip().startswith(
                    ('Error:', 'Error getting scene info:', 'Error executing code:', 'Rejected by safe mode')) for block in result['content']):
            result = dict(result, isError=True)
        return result

    def shutdown(self):
        # Permanent teardown prevents a queued GUI enable from reviving a stopped Bridge.
        self._shutdown = True
        self.set_enabled(False)

    def stop(self):
        self.set_enabled(False)

COMPUTER_USE_SPEC = [
 {'name':'computer_use_status','description':'Check optional desktop/browser/Blender adapter permissions and process state.',
  'inputSchema':{'type':'object','properties':{}}},
 {'name':'computer_use_tools','description':'Get exact tool names and JSON schemas from a locally enabled desktop, browser or Blender MCP. Read these before calling.',
  'inputSchema':{'type':'object','required':['target'],'properties':{'target':{'type':'string','enum':['desktop','browser','blender']}}}},
 {'name':'computer_use_call','description':'Call an allowlisted tool of a locally enabled desktop/browser/Blender MCP. Returns original text/images. Never repeat a timed-out action without checking state. External sending, payment or destructive actions require user confirmation.',
  'inputSchema':{'type':'object','required':['target','name'],'properties':{'target':{'type':'string','enum':['desktop','browser','blender']},'name':{'type':'string'},'arguments':{'type':'object'}}}}
]
