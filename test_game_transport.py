"""Offline broker/transport tests. Child processes use FakeBackend, never Win32 input."""
import copy
import json
from pathlib import Path
import queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from computer_use import ComputerUseManager, GameClient
from game_control.schema import TOOLS, NAMES, RUNTIME_INFO


BROKER_FIXTURE = r'''
import json, time
from game_control import worker
from test_game_control import FakeBackend
class Backend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.capturing=False
        self.slow_jobs=0
        self.delay_capture=False
    def input(self,kind,value,down):
        super().input(kind,value,down)
        if kind=='key' and value=='W' and down:self.delay_capture=True
    def capture(self,rect,max_size):
        if self.delay_capture:
            self.capturing=True
            try:time.sleep(1.5)
            finally:self.capturing=False
        return super().capture(rect,max_size)
    def windows(self):
        self.slow_jobs+=1
        try:
            time.sleep(.8)
            return super().windows()
        finally:self.slow_jobs-=1
worker.WindowsBackend=Backend
original=worker.call_game_tool
def observed(controller,name,arguments):
    result=original(controller,name,arguments)
    if name=='GameControlStatus':
        data=json.loads(result['content'][0]['text'])
        data['fixture_capture_active']=controller.backend.capturing
        data['fixture_slow_jobs']=controller.backend.slow_jobs
        result['content'][0]['text']=json.dumps(data)
    return result
worker.call_game_tool=observed
worker.main()
'''

ECHO_FIXTURE = r'''
import json,sys,threading,time
lock=threading.Lock()
def send(data):
    with lock:print(json.dumps(data),flush=True)
def run(request):
    method=request['method']
    if method=='initialize':result={'capabilities':{},'serverInfo':{'name':'fixture','version':'1'}}
    elif method=='hang':return
    elif method=='wrong-id':
        send({'jsonrpc':'2.0','id':99999,'result':{}});return
    else:
        time.sleep(request.get('params',{}).get('delay',0))
        result={'value':request.get('params',{}).get('value')}
    send({'jsonrpc':'2.0','id':request['id'],'result':result})
for line in sys.stdin:
    request=json.loads(line)
    if request['method']=='control/shutdown':
        send({'jsonrpc':'2.0','method':'control/released','params':{'cleanup_confirmed':True}})
        break
    if request['method']=='drop':break
    if 'id' in request:threading.Thread(target=run,args=(request,),daemon=True).start()
'''


def tool(child, name, arguments=None, timeout=4):
    reply = child.request('tools/call', {'name': name, 'arguments': arguments or {}}, timeout=timeout)
    text = json.loads(reply['content'][0]['text'])
    if reply.get('isError'):
        raise AssertionError(text)
    return text


class DiscoveryTests(unittest.TestCase):
    def environment(self, tools=None):
        manager = ComputerUseManager(desktop=True)
        desktop, game = Mock(), Mock()
        desktop.request.return_value = {'tools': [{'name': 'Screenshot', 'inputSchema': {}}, {'name': 'shell'}]}
        game.request.return_value = {'tools': copy.deepcopy(TOOLS if tools is None else tools),
                                     '_meta': {'openbridge/game-control': dict(RUNTIME_INFO)}}
        manager.children['desktop'] = desktop
        manager.game_child = game
        return manager, desktop, game

    def discover(self, manager, desktop, game):
        with patch.object(manager, '_client', return_value=desktop), \
             patch.object(manager, '_game_client', return_value=game):
            return manager.list_tools('desktop')

    def test_schema_comes_from_running_child_not_parent_constants(self):
        manager, desktop, game = self.environment()
        act = next(t for t in game.request.return_value['tools'] if t['name'] == 'GameAct')
        act['inputSchema']['properties']['steps']['items']['properties']['duration_ms']['maximum'] = 4321
        reply = self.discover(manager, desktop, game)
        published = next(t for t in reply['tools'] if t['name'] == 'GameAct')
        self.assertEqual(published['inputSchema']['properties']['steps']['items']['properties']['duration_ms']['maximum'], 4321)
        game.request.assert_called_once_with('tools/list', timeout=10)
        self.assertEqual(reply['game_runtime'], RUNTIME_INFO)

    def test_rediscovery_refreshes_schema(self):
        manager, desktop, game = self.environment()
        self.discover(manager, desktop, game)
        game.request.return_value['tools'][0]['description'] = 'new child definition'
        reply = self.discover(manager, desktop, game)
        self.assertEqual(next(t for t in reply['tools'] if t['name'] == 'GameWindows')['description'], 'new child definition')
        self.assertEqual(game.request.call_count, 2)

    def test_unreviewed_game_tools_are_filtered(self):
        manager, desktop, game = self.environment(TOOLS + [{'name': 'GameApprove', 'inputSchema': {}}])
        reply = self.discover(manager, desktop, game)
        from background_control.schema import NAMES as BACKGROUND_NAMES
        self.assertEqual({t['name'] for t in reply['tools']}, NAMES | BACKGROUND_NAMES | {'Screenshot'})
        self.assertNotIn('GameApprove', manager.tools['desktop'])

    def test_incomplete_schema_fails_without_old_fallback(self):
        manager, desktop, game = self.environment(TOOLS[:-1])
        with self.assertRaises(RuntimeError):
            self.discover(manager, desktop, game)
        self.assertNotIn('desktop', manager.tools)

    def test_child_replacement_during_discovery_is_rejected(self):
        manager, desktop, game = self.environment()
        response = game.request.return_value
        def replace(*args, **kwargs):
            manager.game_child = Mock()
            return response
        game.request.side_effect = replace
        with self.assertRaises(PermissionError):
            self.discover(manager, desktop, game)
        self.assertNotIn('desktop', manager.tools)

    def test_hot_toggle_clears_runtime_metadata(self):
        manager, desktop, game = self.environment()
        self.discover(manager, desktop, game)
        self.assertTrue(manager.game_runtime)
        manager.set_enabled(False)
        self.assertEqual(manager.game_runtime, {})


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.child = GameClient([sys.executable, '-u', '-c', BROKER_FIXTURE],
                                Path(__file__).resolve().parent, None, lambda *args: None, self.logs.append)
        self.child.start()
        request = tool(self.child, 'GameRequestControl', {'window_id': 1})['request_id']
        # Private approval applies ONLY to FakeBackend in this test child.
        self.child.request('control/approve', {'request_id': request, 'approved': True})
        self.lease = tool(self.child, 'GameControlStatus', {'request_id': request})['lease_id']
        self.frame = tool(self.child, 'GameObserve', {'lease_id': self.lease})['metadata']['frame_id']
        self.threads = []

    def tearDown(self):
        self.child.close()
        for thread in self.threads:
            thread.join(3)
        self.assertTrue(all(not thread.is_alive() for thread in self.threads))

    def background(self, function):
        results = queue.Queue()
        def run():
            try: results.put(('ok', function()))
            except Exception as exc: results.put(('error', exc))
        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return results, thread

    def wait_status(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = tool(self.child, 'GameControlStatus')
            if predicate(value): return value
            time.sleep(.005)
        self.fail('Fake broker did not reach expected state')

    def arguments(self, duration=3000):
        return {'lease_id': self.lease, 'frame_id': self.frame, 'action_id': 'broker-test',
                'steps': [{'duration_ms': duration, 'keys': ['W']}], 'observation_settle_ms': 0}

    def test_parent_and_worker_release_bypass_long_action(self):
        manager = ComputerUseManager(desktop=True)
        manager.game_child = self.child
        with patch.object(manager, '_game_client', return_value=self.child):
            results, action = self.background(lambda: manager.call_tool('desktop', 'GameAct', self.arguments()))
            self.wait_status(lambda state: 'W' in state['held_keys'])
            started = time.monotonic()
            status = manager.call_tool('desktop', 'GameActionStatus', {'lease_id': self.lease, 'action_id': 'broker-test'})
            self.assertEqual(json.loads(status['content'][0]['text'])['state'], 'running')
            released = manager.call_tool('desktop', 'GameRelease', {'lease_id': self.lease})
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertTrue(json.loads(released['content'][0]['text'])['cleanup_confirmed'])
            action.join(2)
        self.assertFalse(action.is_alive())
        state, reply = results.get_nowait()
        self.assertEqual(state, 'ok', repr(reply))
        receipt = json.loads(reply['content'][0]['text'])
        self.assertEqual(receipt['state'], 'aborted')
        self.assertTrue(receipt['cleanup_confirmed'])
        self.assertFalse(tool(self.child, 'GameControlStatus')['held_keys'])

    def test_release_does_not_wait_for_capture_to_complete(self):
        replies, action = self.background(lambda: self.child.request('tools/call',
            {'name': 'GameAct', 'arguments': self.arguments(40)}, timeout=4))
        self.wait_status(lambda state: state['fixture_capture_active'])
        started = time.monotonic()
        released = tool(self.child, 'GameRelease', {'lease_id': self.lease})
        self.assertLess(time.monotonic() - started, .8)
        self.assertTrue(released['cleanup_confirmed'])
        self.assertTrue(action.is_alive(), 'Fixture should still be inside slow capture')
        action.join(3)
        status, reply = replies.get_nowait()
        self.assertEqual(status, 'ok', repr(reply))
        data = json.loads(reply['content'][0]['text'])
        self.assertEqual(data['state'], 'completed')
        self.assertIn('observation_error', data)
        self.assertTrue(data['do_not_replay'])

    def test_release_capacity_not_consumed_by_normal_jobs(self):
        jobs = [self.background(lambda: tool(self.child, 'GameWindows')) for _ in range(4)]
        self.wait_status(lambda state: state['fixture_slow_jobs'] == 4)
        started = time.monotonic()
        reply = tool(self.child, 'GameRelease', {'lease_id': self.lease})
        self.assertLess(time.monotonic() - started, .5)
        self.assertTrue(reply['cleanup_confirmed'])
        for results, thread in jobs:
            thread.join(2)
            state, value = results.get_nowait()
            self.assertEqual(state, 'ok', repr(value))

    def test_wrong_lease_rejected_on_control_lane(self):
        replies, action = self.background(lambda: self.child.request('tools/call',
            {'name': 'GameAct', 'arguments': self.arguments()}, timeout=4))
        self.wait_status(lambda state: 'W' in state['held_keys'])
        reply = self.child.request('tools/call', {'name': 'GameRelease', 'arguments': {'lease_id': 'wrong'}})
        self.assertTrue(reply['isError'])
        self.assertIn('W', tool(self.child, 'GameControlStatus')['held_keys'])
        tool(self.child, 'GameRelease', {'lease_id': self.lease})
        action.join(2)

    def test_http_control_lane_releases_while_action_request_is_waiting(self):
        import secrets
        import tempfile
        import urllib.request
        from bridge import McpHandler, ThreadedHTTPServer, WorkspaceTools
        manager = ComputerUseManager(desktop=True)
        manager.game_child = self.child
        old = McpHandler.tools, McpHandler.computer_use, McpHandler.secret
        with tempfile.TemporaryDirectory() as directory:
            McpHandler.tools = WorkspaceTools(directory)
            McpHandler.computer_use = manager
            McpHandler.secret = secrets.token_hex(16)
            server = ThreadedHTTPServer(('127.0.0.1', 0), McpHandler)
            listener = threading.Thread(target=server.serve_forever, daemon=True)
            listener.start()
            url = 'http://127.0.0.1:%d/mcp/%s' % (server.server_port, McpHandler.secret)
            def rpc(name, arguments):
                payload = {'jsonrpc': '2.0', 'id': name, 'method': 'tools/call',
                           'params': {'name': 'computer_use_call', 'arguments': {
                               'target': 'desktop', 'name': name, 'arguments': arguments}}}
                request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
                    'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
                with urllib.request.urlopen(request, timeout=4) as response:
                    return json.load(response)['result']
            try:
                replies, action = self.background(lambda: rpc('GameAct', self.arguments()))
                self.wait_status(lambda state: 'W' in state['held_keys'])
                started = time.monotonic()
                response = rpc('GameRelease', {'lease_id': self.lease})
                self.assertLess(time.monotonic() - started, 1.)
                self.assertTrue(json.loads(response['content'][0]['text'])['cleanup_confirmed'])
                action.join(2)
                state, result = replies.get_nowait()
                self.assertEqual(state, 'ok', repr(result))
                self.assertEqual(json.loads(result['content'][0]['text'])['state'], 'aborted')
            finally:
                server.shutdown()
                server.server_close()
                listener.join(2)
                McpHandler.tools, McpHandler.computer_use, McpHandler.secret = old

    def test_runtime_and_schema_metadata_are_consistent(self):
        listing = self.child.request('tools/list')
        runtime = listing['_meta']['openbridge/game-control']
        self.assertEqual(runtime, RUNTIME_INFO)
        self.assertEqual(tool(self.child, 'GameControlStatus')['runtime'], runtime)
        self.assertEqual(self.child.initialize_result['serverInfo']['version'], runtime['version'])
        self.assertIn('broker_round_trip_ms', listing['_meta']['openbridge/transport'])


class ManagerLaneTests(unittest.TestCase):
    def test_input_producing_calls_remain_serialized(self):
        manager = ComputerUseManager(desktop=True)
        child = Mock()
        action_entered, action_finish, focus_entered = threading.Event(), threading.Event(), threading.Event()
        def request(method, params, **kwargs):
            name = params['name']
            if name == 'GameAct':
                action_entered.set()
                action_finish.wait(2)
            elif name == 'GameFocus':
                focus_entered.set()
            return {'content': [{'type': 'text', 'text': '{}'}]}
        child.request.side_effect = request
        with patch.object(manager, '_game_client', return_value=child):
            action = threading.Thread(target=lambda: manager.call_tool('desktop', 'GameAct', {}))
            focus = threading.Thread(target=lambda: manager.call_tool('desktop', 'GameFocus', {}))
            action.start()
            self.assertTrue(action_entered.wait(1))
            focus.start()
            try:
                self.assertFalse(focus_entered.wait(.05))
                manager.call_tool('desktop', 'GameControlStatus', {})
                self.assertFalse(focus_entered.is_set())
            finally:
                action_finish.set()
                action.join(2)
                focus.join(2)
        self.assertFalse(action.is_alive())
        self.assertFalse(focus.is_alive())
        self.assertTrue(focus_entered.is_set())


class MultiplexerTests(unittest.TestCase):
    def setUp(self):
        self.child = GameClient([sys.executable, '-u', '-c', ECHO_FIXTURE],
                                Path(__file__).resolve().parent, None, lambda *args: None, lambda *args: None)
        self.child.start()

    def tearDown(self):
        self.child.close()

    def test_out_of_order_responses_match_request_ids(self):
        results, errors = {}, []
        def request(value, delay):
            try: results[value] = self.child.request('echo', {'value': value, 'delay': delay}, timeout=2)['value']
            except Exception as exc: errors.append(exc)
        threads = [threading.Thread(target=request, args=(i, (7-i)*.015)) for i in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(results, {i: i for i in range(8)})
        self.assertEqual(self.child.pending_requests, {})

    def test_timeout_stops_broker_without_retry(self):
        with self.assertRaises(TimeoutError):
            self.child.request('hang', timeout=.04)
        self.assertTrue(self.child.closed.is_set())
        self.assertEqual(self.child.pending_requests, {})

    def test_unexpected_response_id_fails_closed(self):
        with self.assertRaises(RuntimeError):
            self.child.request('wrong-id', timeout=2)
        self.assertTrue(self.child.closed.is_set())
        self.assertEqual(self.child.pending_requests, {})

    def test_eof_wakes_all_pending_requests(self):
        errors = []
        entered = threading.Event()
        def wait_for_reply():
            entered.set()
            try: self.child.request('hang', timeout=3)
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=wait_for_reply)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(RuntimeError):
                self.child.request('drop', timeout=2)
        finally:
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.child.pending_requests, {})


if __name__ == '__main__':
    unittest.main()
