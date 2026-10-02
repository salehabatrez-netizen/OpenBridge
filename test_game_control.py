"""Offline safety tests: no Windows input and no user windows touched."""
import threading
import time
import unittest
from game_control.engine import Controller, ControlError
from game_control.schema import NAMES, TOOLS

class FakeBackend:
    def __init__(self):
        self.info={'window_id':1,'pid':123,'identity':'1:123:created:lab','title':'Input Lab','class':'Lab','rect':[100,100,740,580]}
        self.active=1;self.events=[];self.stop=False;self.fail_down=False;self.fail_up=False
    def window(self,hwnd):return dict(self.info)
    def windows(self):return [self.window(1)]
    def foreground(self):return self.active
    def focus(self,hwnd):self.active=hwnd
    def emergency_pressed(self):return self.stop
    def input(self,kind,value,down):
        self.events.append((kind,value,down))
        if (down and self.fail_down) or (not down and self.fail_up):raise OSError('injected input fault')
    def relative(self,dx,dy):self.events.append(('relative',dx,dy))
    def pointer(self,x,y):self.events.append(('pointer',x,y))
    def capture(self,rect,max_size):return 'YWJj',640,480,'fake'

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.backend=FakeBackend();self.c=Controller(self.backend,watchdog=False)
        self.request=self.c.request(1)['request_id'];self.c.approve(self.request,True)
        self.lease=self.c.status(self.request)['lease_id']
    def tearDown(self):self.c.close()
    def frame(self):return self.c.observe(self.lease)['metadata']['frame_id']
    def act(self,steps=None,action_id='a',frame=None):
        return self.c.act(self.lease,frame or self.frame(),action_id,steps or [{'keys':['W','D'],'buttons':['left'],'duration_ms':30,'dx':12,'dy':-6}])
    def assertCode(self,code,fn):
        with self.assertRaises(ControlError) as cm:fn()
        self.assertEqual(cm.exception.code,code)
    def test_simultaneous_relative_release(self):
        result=self.act();self.assertEqual(result['state'],'completed');self.assertTrue(result['cleanup_confirmed'])
        for key in ['W','D']:self.assertIn(('key',key,True),self.backend.events);self.assertIn(('key',key,False),self.backend.events)
        self.assertIn(('button','left',False),self.backend.events)
        self.assertEqual(sum(e[1] for e in self.backend.events if e[0]=='relative'),12)
        self.assertEqual(sum(e[2] for e in self.backend.events if e[0]=='relative'),-6)
    def test_local_approval_required(self):
        self.c.revoke();p=self.c.request(1);self.assertNotIn('lease_id',self.c.status(p['request_id']))
        self.assertCode('NO_LEASE',lambda:self.c.observe('guessed'))
    def test_no_remote_approval_schema(self):
        self.assertEqual(len(TOOLS),8);self.assertFalse(any('approve' in n.lower() for n in NAMES))
    def test_request_token_not_public(self):self.assertNotIn('lease_id',self.c.status('someone-else'))
    def test_busy_request(self):self.assertCode('BUSY',lambda:self.c.request(1))
    def test_stale_frame(self):
        f=self.frame();self.c.frames[f]['captured_at']-=6
        self.assertCode('STALE_FRAME',lambda:self.act(frame=f));self.assertEqual(self.backend.events,[])
    def test_old_frame_invalidated(self):
        old=self.frame();self.frame();self.assertCode('STALE_FRAME',lambda:self.act(frame=old))
    def test_consumed_frame(self):
        f=self.frame();self.act(frame=f);self.assertCode('STALE_FRAME',lambda:self.act(frame=f,action_id='b'))
    def test_deduplication(self):
        f=self.frame();a=self.act(frame=f);events=list(self.backend.events);b=self.act(frame=f)
        self.assertEqual(a['state'],b['state']);self.assertTrue(b['deduplicated']);self.assertEqual(events,self.backend.events)
    def test_id_conflict(self):
        f=self.frame();self.act(frame=f)
        self.assertCode('ID_CONFLICT',lambda:self.act(frame=f,steps=[{'duration_ms':40}]))
    def test_unknown_is_not_safe_to_replay(self):self.assertEqual(self.c.action_status(self.lease,'absent'),{'state':'unknown','do_not_replay':True})
    def test_validate_entire_batch_before_press(self):
        for steps in [[{'keys':['W'],'duration_ms':20},{'keys':['WIN'],'duration_ms':20}],
                      [{'keys':['W'],'duration_ms':5000}]*3,[{'keys':['W'],'duration_ms':5001}],
                      [{'duration_ms':True}],[{'duration_ms':20,'surprise':1}],
                      [{'keys':['CTRL','ESC'],'duration_ms':20}],
                      [{'keys':['W','W'],'duration_ms':20}],
                      [{'duration_ms':20,'pointer':[640,0]}],
                      [{'duration_ms':20,'pointer':[0,0],'dx':1}]]:
            self.assertCode('INVALID_ARGUMENT',lambda:self.act(steps));self.assertEqual(self.backend.events,[])
    def test_focus_loss_before_action(self):
        f=self.frame();self.backend.active=2;result=self.act(frame=f)
        self.assertEqual(result['error_code'],'FOCUS_LOST');self.assertEqual(self.backend.events,[])
    def test_resize_before_action(self):
        f=self.frame();self.backend.info['rect']=[0,0,100,100];self.assertEqual(self.act(frame=f)['error_code'],'WINDOW_CHANGED')
    def test_window_identity_reuse(self):
        f=self.frame();self.backend.info['identity']='reused';self.assertEqual(self.act(frame=f)['error_code'],'WINDOW_CHANGED')
    def test_input_failure_releases_uncertain_down(self):
        self.backend.fail_down=True;r=self.act();self.assertEqual(r['state'],'aborted');self.assertTrue(r['cleanup_confirmed'])
        self.assertIn(('key','D',False),self.backend.events)
    def test_cleanup_failure_latches_fault(self):
        self.backend.fail_up=True;r=self.act();self.assertEqual(r['state'],'fault');self.assertFalse(r['cleanup_confirmed'])
        self.assertCode('CLOSED',lambda:self.c.observe(self.lease))
    def test_pointer_coordinates(self):
        self.act([{'pointer':[320,240],'buttons':['left'],'duration_ms':20}])
        self.assertEqual(self.backend.events[0],('pointer',420,340))
    def test_cancel_during_action(self):
        f=self.frame();results=[]
        thread=threading.Thread(target=lambda:results.append(self.act([{'keys':['W'],'duration_ms':1000}],frame=f)))
        thread.start()
        deadline=time.monotonic()+1
        while not self.backend.events and time.monotonic()<deadline:time.sleep(.001)
        self.c.revoke('test local OFF');thread.join(1)
        self.assertFalse(thread.is_alive());self.assertEqual(results[0]['error_code'],'CANCELLED')
        self.assertIn(('key','W',False),self.backend.events);self.assertFalse(self.c.held_keys)
    def test_concurrency_rejected(self):
        self.c.action_lock.acquire()
        try:self.assertCode('BUSY',lambda:self.c.observe(self.lease))
        finally:self.c.action_lock.release()
    def test_watchdog_f8(self):
        self.c.close();self.c=Controller(self.backend);p=self.c.request(1);self.c.approve(p['request_id'],True)
        self.backend.stop=True
        limit=time.monotonic()+1
        while self.c.binding and time.monotonic()<limit:time.sleep(.005)
        self.assertIsNone(self.c.binding);self.assertEqual(self.c.last_stop,'F8 emergency stop')
    def test_expiry(self):
        self.c.binding['expires_at']-=601;self.assertCode('EXPIRED',lambda:self.c.observe(self.lease))
    def test_consent_window_changed(self):
        self.c.revoke();p=self.c.request(1);self.backend.info['identity']='replacement'
        self.assertCode('WINDOW_CHANGED',lambda:self.c.approve(p['request_id'],True))
    def test_history_limit_does_not_evict_receipts(self):
        self.c.history.update({('other',str(i)):{'result':{}} for i in range(1024)})
        self.assertCode('SESSION_LIMIT',lambda:self.act());self.assertEqual(self.backend.events,[])
    def test_closed_cannot_request(self):
        self.c.close();self.assertCode('CLOSED',lambda:self.c.request(1))


class IntegrationTests(unittest.TestCase):
    def test_game_disabled_never_spawns(self):
        from computer_use import ComputerUseManager
        from unittest.mock import patch
        m=ComputerUseManager()
        with patch('computer_use.subprocess.Popen') as spawn:
            with self.assertRaises(PermissionError):m.call_tool('desktop','GameWindows',{})
            spawn.assert_not_called()

    def test_game_schema_keeps_gateway_targets_unchanged(self):
        from computer_use import ComputerUseManager, COMPUTER_USE_SPEC
        from unittest.mock import Mock,patch
        m=ComputerUseManager(desktop=True);child=Mock()
        child.request.return_value={'tools':[{'name':'Screenshot'},{'name':'shell'}]};m.children['desktop']=child
        game=Mock();game.request.return_value={'tools':TOOLS};m.game_child=game
        with patch.object(m,'_client',return_value=child), patch.object(m,'_game_client',return_value=game):
            tools=m.list_tools('desktop')['tools']
        from background_control.schema import NAMES as BACKGROUND_NAMES
        self.assertEqual({t['name'] for t in tools},NAMES|BACKGROUND_NAMES|{'Screenshot'})
        self.assertEqual(COMPUTER_USE_SPEC[-1]['inputSchema']['properties']['target']['enum'],['desktop','browser','blender'])

    def test_master_off_revokes_game_before_close(self):
        from computer_use import ComputerUseManager
        from unittest.mock import Mock
        m=ComputerUseManager(desktop=True);child=Mock();m.game_child=child
        m.set_enabled(False)
        self.assertEqual([c[0] for c in child.method_calls],['revoke','close'])
        self.assertIsNone(m.game_child)

    def test_stale_local_consent_is_not_displayed(self):
        from computer_use import ComputerUseManager
        from unittest.mock import Mock
        m=ComputerUseManager(desktop=True);old=Mock();m.game_child=Mock()
        m._queue_game_consent(old,{'request_id':'stale'})
        self.assertIsNone(m.pop_game_consent())

    def test_active_game_blocks_other_adapters(self):
        from computer_use import ComputerUseManager
        from unittest.mock import Mock,patch
        m=ComputerUseManager(desktop=True,browser=True);child=Mock();m.game_child=child
        child.closed.is_set.return_value=False;child.request.return_value={'state':'active'}
        with patch.object(m,'_call_adapter_tool') as forward:
            with self.assertRaises(PermissionError):m.call_tool('browser','browser_click',{})
            forward.assert_not_called()

    def test_consent_polled_after_startup(self):
        import gui
        from unittest.mock import Mock
        app=object.__new__(gui.OpenBridgeApp)
        import queue
        app._ui_events=queue.Queue();app._ui_logs=queue.Queue();app._dropped_logs=0
        app.root=Mock();app.connection_detail=Mock();app.service=Mock()
        app.service.last_healthy_at=None;app.service.reconnect_count=0
        request={'request_id':'test'};app.service.computer_use.pop_game_consent.return_value=request
        app._confirm_game_window=Mock()
        app._drain_ui_events()
        app._confirm_game_window.assert_called_once_with(app.service,request)
        app.root.after.assert_called_once()

    def test_blender_disconnect_text_is_error(self):
        from computer_use import ComputerUseManager
        from unittest.mock import Mock,patch
        m=ComputerUseManager(blender=True);child=Mock();m.children['blender']=child
        m.tools['blender']={'get_scene_info':{}}
        child.request.return_value={'content':[{'type':'text','text':'Error getting scene info: Not connected to Blender'}],'isError':False}
        with patch.object(m,'_client',return_value=child):self.assertTrue(m.call_tool('blender','get_scene_info')['isError'])

if __name__=='__main__':unittest.main()
