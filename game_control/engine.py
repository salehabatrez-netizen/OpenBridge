"""Platform-independent lease, validation and cancellation state machine."""
import hashlib
import json
import math
import secrets
import threading
import time

KEYS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') | {
    'SPACE','ENTER','ESC','TAB','BACKSPACE','SHIFT','CTRL','UP','DOWN','LEFT','RIGHT','F1','F2','F3','F4'}
BUTTONS = {'left','right','middle'}

# Tunable safety limits (ms / s / count). Raised 2026-09-18 for real-game use (hand-mining needs >3 s holds).
MAX_STEP_MS = 5000
MAX_BATCH_MS = 10000
LEASE_IDLE_S = 600
LEASE_ABSOLUTE_S = 3600
MAX_ACTIONS = 1024
MAX_SETTLE_MS = 1000
INPUT_TICK_MS = 10  # Keep the existing rate; improve pacing before increasing CPU load.

class ControlError(Exception):
    def __init__(self, code, message):
        super().__init__(message); self.code = code

def require(condition, code, message):
    if not condition: raise ControlError(code, message)

def integer(value, minimum, maximum, name):
    require(type(value) is int and minimum <= value <= maximum,
            'INVALID_ARGUMENT', '%s must be an integer in [%d,%d]' % (name, minimum, maximum))
    return value

def timing_summary(samples):
    """Nearest-rank percentiles of seconds; per-batch samples are bounded by 10 s."""
    ordered = sorted(value * 1000 for value in samples)
    def percentile(fraction):
        if not ordered:return 0.0
        index=max(0, min(len(ordered)-1, math.ceil(len(ordered)*fraction)-1))
        return round(ordered[index],3)
    return {'samples':len(ordered),'p50_ms':percentile(.5),'p95_ms':percentile(.95),
            'max_ms':round(ordered[-1],3) if ordered else 0.0}

class Controller:
    def __init__(self, backend, notify=lambda *_: None, clock=time.monotonic, watchdog=True, policy=None):
        # policy: optional LOCAL owner-managed callable window_info -> rule dict or None.
        # It is never reachable through the MCP tool list; a matching rule replaces the GUI prompt.
        self.backend, self.notify, self.clock, self.policy = backend, notify, clock, policy
        self.lock = threading.RLock()
        self.io = threading.RLock()
        self.action_lock = threading.Lock()
        self.cancel = threading.Event(); self.closed = threading.Event()
        self.pending = None; self.binding = None; self.frames = {}; self.history = {}
        self.held_keys = set(); self.held_buttons = set(); self.fault = None
        self.last_stop = None; self.thread = None
        self.releasing = 0; self.last_release_ms = None
        if watchdog:
            self.thread = threading.Thread(target=self._watch, daemon=True); self.thread.start()

    def _watch(self):
        while not self.closed.wait(.02):
            try:
                if self.backend.emergency_pressed(): self.revoke('F8 emergency stop')
                with self.lock: binding = self.binding
                if binding and self.clock() > binding['expires_at']: self.revoke('Control lease expired', expected_lease=binding['lease_id'])
            except Exception as exc:
                self.revoke('Safety monitor error: '+type(exc).__name__)

    def windows(self): return {'windows': self.backend.windows(), 'local_approval_required': True}

    def request(self, window_id):
        integer(window_id, 1, 2**63-1, 'window_id')
        info = self.backend.window(window_id)
        with self.lock:
            require(not self.closed.is_set(), 'CLOSED', 'Controller stopped')
            require(not self.fault, 'CLEANUP_FAILED', 'Input cleanup needs local inspection')
            require(not self.binding, 'BUSY', 'Another control lease is active; release it first')
            require(not self.releasing and not self.action_lock.locked(), 'BUSY', 'Previous input/capture is still finishing')
            require(not self.pending or self.clock() > self.pending['deadline'], 'BUSY', 'A local consent request is pending')
            request_id = secrets.token_hex(16)
            self.pending = {'request_id':request_id, 'window':info, 'deadline':self.clock()+60}
        rule = self._trusted(info)
        if rule:
            # Same private approval path the GUI uses: identity is re-verified and
            # every lease/action/time limit stays unchanged. Only the prompt is skipped.
            self.approve(request_id, True)
            with self.lock: lease_id = self.binding['lease_id'] if self.binding else None
            self.notify('control/auto_approved', {'request_id':request_id,'window':info,'rule':rule})
            return {'request_id':request_id, 'state':'approved', 'approved_by':'local_trust_policy',
                    'rule':rule, 'lease_id':lease_id}
        self.notify('control/consent', {'request_id':request_id,'window':info})
        return {'request_id':request_id, 'state':'awaiting_local_approval', 'expires_in_seconds':60}

    def _trusted(self, info):
        if self.policy is None: return None
        try: rule = self.policy(dict(info))
        except Exception as exc:
            self.notify('control/policy_error', {'error':type(exc).__name__}); return None
        return rule if isinstance(rule, dict) and rule else None

    def approve(self, request_id, approved):
        # PRIVATE IPC only. Never exposed in the MCP tools list.
        with self.lock:
            p = self.pending
            require(p and secrets.compare_digest(p['request_id'], str(request_id)), 'UNKNOWN_REQUEST', 'Consent request not current')
            self.pending = None
            require(self.clock() <= p['deadline'], 'EXPIRED', 'Consent request expired')
            if not approved: return {'state':'denied'}
            require(not self.closed.is_set() and not self.fault, 'CLOSED', 'Controller unavailable')
            require(not self.releasing and not self.action_lock.locked(), 'BUSY', 'Previous input/capture is still finishing')
            current = self.backend.window(p['window']['window_id'])
            require(current['identity'] == p['window']['identity'], 'WINDOW_CHANGED', 'Window identity changed; request again')
            self.binding = {'request_id':request_id,'lease_id':secrets.token_hex(24),'window':current,
                            'created_at':self.clock(),'expires_at':self.clock()+LEASE_IDLE_S}
            self.frames.clear(); self.history.clear(); self.cancel.clear()
            return {'state':'approved'}

    def status(self, request_id=None):
        # Do not hold the state lock across Win32 queries: release must be able to
        # revoke authorization even if a window query is slow.
        with self.lock: inspected = self.binding
        ready = False
        if inspected:
            try:
                cur = self.backend.window(inspected['window']['window_id'])
                ready = cur['identity']==inspected['window']['identity'] and self.backend.foreground()==cur['window_id']
            except Exception: pass
        with self.lock:
            b = self.binding; p = self.pending
            state = 'fault' if self.fault else 'releasing' if self.releasing else 'active' if b else 'awaiting_local_approval' if p and self.clock()<=p['deadline'] else 'idle'
            result = {'state':state,'emergency_key':'F8','last_stop':self.last_stop,
                      'fault':self.fault,'max_batch_ms':MAX_BATCH_MS,'max_step_ms':MAX_STEP_MS,'max_frame_age_ms':5000,
                      'held_keys':sorted(self.held_keys),'held_buttons':sorted(self.held_buttons),
                      'target_ready':bool(b and b is inspected and ready and not self.cancel.is_set()),
                      'input_tick_ms':INPUT_TICK_MS,'last_release_ms':self.last_release_ms}
            if b:
                result['window'] = b['window']; result['lease_expires_in_seconds'] = max(0,round(b['expires_at']-self.clock(),1))
                result['lease_absolute_remaining_seconds'] = max(0,round(b['created_at']+LEASE_ABSOLUTE_S-self.clock(),1))
                result['actions_remaining'] = max(0,MAX_ACTIONS-len(self.history))
                if request_id and secrets.compare_digest(str(request_id),b['request_id']): result['lease_id'] = b['lease_id']
            return result

    def _lease(self, lease_id, renew=False):
        with self.lock:
            b = self.binding
            require(not self.closed.is_set() and not self.fault, 'CLOSED', 'Controller stopped or cleanup unconfirmed')
            require(b and isinstance(lease_id,str) and secrets.compare_digest(b['lease_id'],lease_id), 'NO_LEASE', 'No matching local approval')
            require(self.clock() <= b['expires_at'], 'EXPIRED', 'Lease expired; request local approval again')
            if renew: b['expires_at'] = min(self.clock()+LEASE_IDLE_S,b['created_at']+LEASE_ABSOLUTE_S)
            return dict(b)

    def _window(self, binding, rect=None, focused=True):
        cur = self.backend.window(binding['window']['window_id'])
        require(cur['identity']==binding['window']['identity'], 'WINDOW_CHANGED', 'Bound window was replaced')
        if rect is not None: require(cur['rect']==rect,'WINDOW_CHANGED','Client rectangle changed; capture again')
        if focused: require(self.backend.foreground()==cur['window_id'],'FOCUS_LOST','Bound window is not in foreground')
        return cur

    def focus(self, lease_id):
        b = self._lease(lease_id, True)
        with self.io:
            self._lease(lease_id)
            self._window(b, focused=False)
            self.backend.focus(b['window']['window_id'])
            self._window(b)
        return {'state':'focused','window_id':b['window']['window_id']}

    def observe(self, lease_id, max_size=1280, settle_ms=0):
        integer(max_size,320,1920,'max_size')
        integer(settle_ms,0,MAX_SETTLE_MS,'settle_ms')
        b = self._lease(lease_id,True)
        require(self.action_lock.acquire(False),'BUSY','An action is in progress')
        try:
            before = self._window(b)
            # Let the application render after key-up, without holding any input.
            # Keep checking consent/focus while waiting; never delay an emergency stop.
            deadline = self.clock()+settle_ms/1000
            while self.clock() < deadline:
                self._lease(lease_id); self._window(b,before['rect'])
                require(not self.cancel.is_set(),'CANCELLED','Controller cancelled')
                self.cancel.wait(min(.01,max(0,deadline-self.clock())))
            self._lease(lease_id); self._window(b,before['rect'])
            captured_at = self.clock()
            image, width, height, backend_name = self.backend.capture(before['rect'], max_size)
            self._lease(lease_id); self._window(b,before['rect'])
            frame_id = secrets.token_hex(16)
            frame = {'frame_id':frame_id,'captured_at':captured_at,'window_id':before['window_id'],
                     'rect':before['rect'],'image_width':width,'image_height':height,
                     'capture_backend':backend_name,'capture_duration_ms':round((self.clock()-captured_at)*1000),
                      'settle_ms':settle_ms,'expires_in_ms':max(0,int((5-(self.clock()-captured_at))*1000))}
            capture_metrics = getattr(self.backend,'last_capture_metrics',None)
            if isinstance(capture_metrics,dict):frame['capture_metrics']=dict(capture_metrics)
            with self.lock:
                self._lease(lease_id)
                self.frames = {frame_id:frame}
            return {'metadata':frame,'png':image}
        finally: self.action_lock.release()

    @staticmethod
    def validate(steps, frame):
        require(isinstance(steps,list) and 1<=len(steps)<=16,'INVALID_ARGUMENT','steps must contain 1..16 items')
        result=[]; total=0
        for step in steps:
            require(isinstance(step,dict) and not set(step)-{'keys','buttons','dx','dy','duration_ms','pointer'},'INVALID_ARGUMENT','Unknown step fields')
            ms=integer(step.get('duration_ms'),20,MAX_STEP_MS,'duration_ms');total+=ms
            keys=step.get('keys',[]);buttons=step.get('buttons',[])
            require(isinstance(keys,list) and len(keys)<=8 and all(isinstance(k,str) and k in KEYS for k in keys),'INVALID_ARGUMENT','Unsupported key')
            require(len(set(keys))==len(keys),'INVALID_ARGUMENT','Duplicate keys')
            require(not ('CTRL' in keys and 'ESC' in keys),'INVALID_ARGUMENT','System shortcut is not allowed')
            require(isinstance(buttons,list) and len(buttons)<=3 and all(isinstance(k,str) and k in BUTTONS for k in buttons),'INVALID_ARGUMENT','Unsupported mouse button')
            require(len(set(buttons))==len(buttons),'INVALID_ARGUMENT','Duplicate buttons')
            dx=integer(step.get('dx',0),-1000,1000,'dx');dy=integer(step.get('dy',0),-1000,1000,'dy')
            pointer=step.get('pointer')
            if pointer is not None:
                require(isinstance(pointer,list) and len(pointer)==2 and dx==dy==0,'INVALID_ARGUMENT','pointer requires [x,y] and no relative delta')
                integer(pointer[0],0,frame['image_width']-1,'pointer x');integer(pointer[1],0,frame['image_height']-1,'pointer y')
            result.append({'keys':keys,'buttons':buttons,'dx':dx,'dy':dy,'pointer':pointer,'duration_ms':ms})
        require(total<=MAX_BATCH_MS,'INVALID_ARGUMENT','Total batch exceeds %d ms' % MAX_BATCH_MS)
        return result

    def _check(self, b, frame, end):
        require(not self.cancel.is_set(),'CANCELLED','Action cancelled')
        self._lease(b['lease_id'])
        require(self.clock()<=end,'DEADLINE','Batch execution deadline exceeded')
        self._window(b,frame['rect'])

    def _release(self):
        errors=[]
        with self.io:
            for kind, held in [('key',self.held_keys),('button',self.held_buttons)]:
                for value in sorted(held, key=lambda value:(value in {'CTRL','SHIFT'},value)):
                    try:
                        self.backend.input(kind,value,False)
                        with self.lock: held.discard(value)
                    except Exception as exc: errors.append(type(exc).__name__)
            if errors:
                with self.lock: self.fault='Input release not confirmed: '+','.join(errors)
        return not errors

    def _begin_release(self, reason):
        # Caller holds state lock, but MUST drop it before waiting for self.io.
        self.releasing += 1
        self.cancel.set();self.binding=None;self.pending=None;self.frames.clear();self.last_stop=reason

    def _finish_release(self, started):
        try:
            clean=self._release()
            elapsed=round((self.clock()-started)*1000,3)
            with self.lock:self.last_release_ms=elapsed
            return {'state':'released' if clean else 'fault','cleanup_confirmed':clean,'release_elapsed_ms':elapsed}
        finally:
            with self.lock:self.releasing-=1

    def revoke(self, reason='Released', expected_lease=None):
        started=self.clock()
        with self.lock:
            # A delayed expiry check must not revoke a replacement lease.
            if expected_lease is not None and (not self.binding or self.binding['lease_id']!=expected_lease):
                return {'state':'unchanged','cleanup_confirmed':False}
            self._begin_release(reason)
        return self._finish_release(started)

    def release(self, lease_id):
        started=self.clock()
        with self.lock:
            # Validation and invalidation are atomic; a stale remote release can
            # never revoke a lease approved after this one.
            self._lease(lease_id)
            self._begin_release('Client released its lease')
        return self._finish_release(started)

    def action_status(self, lease_id, action_id):
        with self.lock:
            item=self.history.get((str(lease_id),str(action_id)))
            return dict(item['result']) if item else {'state':'unknown','do_not_replay':True}

    def act(self, lease_id, frame_id, action_id, steps):
        require(isinstance(action_id,str) and 1<=len(action_id)<=64 and all(c.isascii() and (c.isalnum() or c in '_-') for c in action_id),'INVALID_ARGUMENT','action_id must be 1..64 ASCII identifier characters')
        fingerprint=hashlib.sha256(json.dumps({'frame_id':frame_id,'steps':steps},sort_keys=True,allow_nan=False).encode()).hexdigest()
        key=(str(lease_id),action_id)
        with self.lock:
            old=self.history.get(key)
            if old:
                require(old['fingerprint']==fingerprint,'ID_CONFLICT','action_id already used for a different request')
                return dict(old['result'],replayed=False,deduplicated=True)
        b=self._lease(lease_id,True)
        require(self.action_lock.acquire(False),'BUSY','Another action is running')
        try:
            with self.lock:
                old=self.history.get(key)
                if old:
                    require(old['fingerprint']==fingerprint,'ID_CONFLICT','action_id already used')
                    return dict(old['result'],replayed=False,deduplicated=True)
                self._lease(lease_id)
                require(not self.cancel.is_set(),'CANCELLED','Controller cancelled')
                frame=self.frames.get(frame_id)
                require(frame is not None,'STALE_FRAME','Capture a new frame first')
                require(self.clock()-frame['captured_at']<=5,'STALE_FRAME','Frame older than 5 seconds')
                validated=self.validate(steps,frame)
                require(len(self.history)<MAX_ACTIONS,'SESSION_LIMIT','%d actions reached; release and request local approval again' % MAX_ACTIONS)
                self.frames.clear()
                result={'action_id':action_id,'state':'running','completed_steps':0,'cleanup_confirmed':False}
                self.history[key]={'fingerprint':fingerprint,'result':result}
            started=self.clock(); requested_ms=sum(s['duration_ms'] for s in validated)
            scheduled_end=started+requested_ms/1000; end=scheduled_end+.30
            step_begin=started; tick_s=INPUT_TICK_MS/1000
            tick_lateness=[]; check_cost=[]; step_lateness=[]; missed_ticks=0; first_input=None
            def check():
                before=self.clock();self._check(b,frame,end)
                check_cost.append(self.clock()-before)
            try:
                for step in validated:
                    duration=step['duration_ms']/1000; step_end=step_begin+duration
                    sent_x=sent_y=0; tick_due=step_begin; tick_index=0
                    with self.io:
                        check()
                        # Do not compress an entirely missed step into an instant
                        # click/turn. Abort rather than catch up stale intentions.
                        require(self.clock()<step_end,'SCHEDULE_LATE','Step window missed; observe again, do not replay')
                        step_lateness.append(max(0,self.clock()-step_begin))
                        if step['pointer'] is not None:
                            x,y=step['pointer'];l,t,r,bot=frame['rect']
                            if first_input is None:first_input=self.clock()
                            self.backend.pointer(l+int(x*(r-l)/frame['image_width']),t+int(y*(bot-t)/frame['image_height']))
                        for kind,desired,held in [('key',set(step['keys']),self.held_keys),('button',set(step['buttons']),self.held_buttons)]:
                            for value in sorted(held-desired,key=lambda value:(value in {'CTRL','SHIFT'},value)):
                                if first_input is None:first_input=self.clock()
                                self.backend.input(kind,value,False)
                                with self.lock:held.discard(value)
                            for value in sorted(desired-held,key=lambda value:(value not in {'CTRL','SHIFT'},value)):
                                require(not self.cancel.is_set(),'CANCELLED','Action cancelled')
                                require(self.clock()<step_end,'SCHEDULE_LATE','Step expired during input transition; do not replay')
                                # Record before dispatch so uncertain sends are released.
                                with self.lock:held.add(value)
                                if first_input is None:first_input=self.clock()
                                self.backend.input(kind,value,True)
                    while True:
                        with self.io:
                            check(); now=self.clock()
                            late=max(0,now-tick_due);tick_lateness.append(late)
                            missed_ticks+=int(late/tick_s)
                            fraction=max(0,min(1,(now-step_begin)/duration))
                            tx=round(step['dx']*fraction);ty=round(step['dy']*fraction)
                            if (tx,ty)!=(sent_x,sent_y):
                                if first_input is None:first_input=self.clock()
                                self.backend.relative(tx-sent_x,ty-sent_y)
                            sent_x,sent_y=tx,ty
                        if fraction>=1:break
                        # Absolute deadlines prevent work + relative sleep from
                        # accumulating drift. Missed ticks are skipped, not burst.
                        now=self.clock()
                        tick_index=max(tick_index+1,int(max(0,now-step_begin)/tick_s)+1)
                        tick_due=min(step_end,step_begin+tick_index*tick_s)
                        self.cancel.wait(max(0,tick_due-self.clock()))
                    step_begin=step_end
                    with self.lock:result['completed_steps']+=1
                with self.lock:result['state']='completed'
            except Exception as exc:
                with self.lock:
                    result.update(state='aborted',error_code=getattr(exc,'code','EXECUTION_ERROR'),error=str(exc),do_not_replay=True)
            finally:
                execution_done=self.clock();clean=self._release();finished=self.clock()
                timings={'requested_duration_ms':requested_ms,'input_tick_ms':INPUT_TICK_MS,
                         'first_input_ms':round((first_input-started)*1000,3) if first_input is not None else None,
                         'execution_overshoot_ms':round(max(0,execution_done-scheduled_end)*1000,3),
                         'cleanup_ms':round((finished-execution_done)*1000,3),'missed_ticks':missed_ticks,
                         'tick_lateness':timing_summary(tick_lateness),
                         'step_start_lateness':timing_summary(step_lateness),
                         'window_check':timing_summary(check_cost)}
                with self.lock:
                    result.update(cleanup_confirmed=clean,elapsed_ms=round((finished-started)*1000),timings=timings)
                    if not clean:result.update(state='fault',error_code='CLEANUP_FAILED',do_not_replay=True)
            return dict(result)
        finally:self.action_lock.release()

    def close(self):
        self.closed.set();result=self.revoke('Controller shutdown')
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=.3)
        return result
