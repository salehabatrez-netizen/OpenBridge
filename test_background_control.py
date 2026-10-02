"""Offline tests; no windows, clipboard, keyboard or mouse interaction."""
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock,MagicMock,patch
from background_control.service import BackgroundService,BackgroundError
from background_control.schema import NAMES,TOOLS
import local_cu_preferences

class Fake(BackgroundService):
    def __init__(self):
        self.enabled=True;self.now=0.;self.calls=[];self.fail=False
        super().__init__('.',lambda:self.enabled,lambda:self.now)
    def _run(self,op,args):
        self.calls.append((op,args))
        if self.fail:raise TimeoutError('uncertain')
        if op=='snapshot':return {'window':{'window_id':1},'_elements':{'button':{'kind':'native','actions':['message_click']},'edit':{'kind':'uia','actions':['set_value','invoke']}},'elements':[]}
        return {'state':'dispatched_unverified'}

def unpack(result):return json.loads(result['content'][0]['text'])

class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.s=Fake();self.snapshot=unpack(self.s.call('BackgroundSnapshot',{'window_id':1}))
        self.args={'snapshot_id':self.snapshot['snapshot_id'],'element_id':'button','action_id':self.s.prefix+'one','action':'message_click','x':3,'y':4}
    def test_deduplicated_no_replay(self):
        a=unpack(self.s.call('BackgroundAction',self.args));b=unpack(self.s.call('BackgroundAction',self.args))
        self.assertEqual(a['state'],'dispatched_unverified');self.assertTrue(b['deduplicated']);self.assertEqual(len(self.s.calls),2)
    def test_conflicting_id(self):
        self.s.call('BackgroundAction',self.args)
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,x=5))
    def test_timeout_receipt_no_retry(self):
        self.s.fail=True
        a=self.s.call('BackgroundAction',self.args);self.assertTrue(a['isError'])
        self.assertEqual(unpack(self.s.call('BackgroundActionStatus',{'action_id':self.args['action_id']}))['state'],'unknown')
        self.s.call('BackgroundAction',self.args);self.assertEqual(len(self.s.calls),2)
    def test_consumed_frame(self):
        self.s.call('BackgroundAction',self.args)
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,action_id=self.s.prefix+'two'))
    def test_message_frame_expiry(self):
        self.s.now=5.01
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',self.args)
    def test_uia_frame_expiry(self):
        self.s.now=30.01
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,element_id='edit',action='invoke'))
    def test_unsupported_action(self):
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,action='invoke'))
    def test_disabled_blocks_all(self):
        self.s.enabled=False
        for name in NAMES:
            with self.assertRaises(BackgroundError):self.s.call(name,{})
    def test_old_session_id_rejected(self):
        old=self.args['action_id'];self.s=Fake()
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,action_id=old))
    def test_unknown_is_not_nonexecution(self):
        d=unpack(self.s.call('BackgroundActionStatus',{'action_id':'missing'}));self.assertEqual(d['state'],'unknown');self.assertTrue(d['do_not_replay'])
    def test_stop_invalidates_unused_snapshot(self):
        self.s.stop()
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',self.args)
        self.assertEqual(len(self.s.calls),1)
    def test_revocation_generation_checked_before_spawn(self):
        s=BackgroundService('.',lambda:True);s.stop()
        with patch('pathlib.Path.is_file',return_value=True),patch('background_control.service.subprocess.Popen') as spawn:
            with self.assertRaises(BackgroundError):s._run('action',{'_generation':0})
            spawn.assert_not_called()
    def test_kill_failure_does_not_block_revocation(self):
        self.s.process=Mock();self.s.process.poll.return_value=None
        self.s.process.kill.side_effect=OSError('process exited or inaccessible')
        self.s.stop();self.assertEqual(self.s.generation,1)
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',self.args)
    def test_stop_retains_receipts(self):
        self.s.call('BackgroundAction',self.args);self.s.stop()
        self.assertTrue(unpack(self.s.call('BackgroundAction',self.args))['deduplicated'])
    def test_no_eviction_at_capacity(self):
        self.s.receipts={str(i):{} for i in range(1024)}
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',self.args)
        self.assertEqual(len(self.s.calls),1)
    def test_strict_coordinates(self):
        for x in (-1,32768,True,'1',1.5):
            with self.subTest(x=x),self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,x=x))
    def test_invalid_button(self):
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,button='double'))
    def test_set_value_no_clipboard(self):
        args={k:v for k,v in self.args.items() if k not in ('x','y')};args.update(element_id='edit',action='set_value',text='你好')
        self.s.call('BackgroundAction',args);self.assertEqual(self.s.calls[-1][1]['text'],'你好')
    def test_no_text_for_click(self):
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,text='oops'))
    def test_marks_validation(self):
        for change in ({'marks':[[1,2]]},{'capture':True,'marks':[[True,2]]},{'max_size':90000},{'format':'gif'},{'window_id':True}):
            with self.subTest(change=change),self.assertRaises(BackgroundError):self.s.call('BackgroundSnapshot',dict(window_id=1,**{k:v for k,v in change.items() if k!='window_id'}) if 'window_id' not in change else change)
    def test_unknown_arguments_rejected(self):
        with self.assertRaises(BackgroundError):self.s.call('BackgroundAction',dict(self.args,foreground=True))
    def test_discovery_schema(self):
        self.assertEqual(len(NAMES),4)
        for t in TOOLS:self.assertFalse(t['inputSchema']['additionalProperties'])
    def test_manager_disabled_does_not_spawn(self):
        from computer_use import ComputerUseManager
        m=ComputerUseManager();m.tools['desktop']={name:{} for name in NAMES}
        with patch('background_control.service.subprocess.Popen') as popen:
            with self.assertRaises(BackgroundError):m.call_tool('desktop','BackgroundWindows',{})
            popen.assert_not_called()
    def test_manager_requires_discovery(self):
        from computer_use import ComputerUseManager
        m=ComputerUseManager(desktop=True)
        with self.assertRaises(PermissionError):m.call_tool('desktop','BackgroundWindows',{})
    def test_worker_timeout_kills_once(self):
        import subprocess
        s=BackgroundService('.',lambda:True);p=Mock();p.stdin.closed=p.stdout.closed=p.stderr.closed=False
        p.communicate.side_effect=[subprocess.TimeoutExpired('test',8),(b'',b'')]
        with patch('pathlib.Path.is_file',return_value=True),patch('background_control.service.subprocess.Popen',return_value=p) as spawn:
            with self.assertRaises(BackgroundError):s._run('windows',{})
            spawn.assert_called_once();p.kill.assert_called_once()
    def test_native_no_foreground_injection(self):
        import ast
        tree=ast.parse(Path('background_control/native.py').read_text(encoding='utf-8'))
        called={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(called & {'SetForegroundWindow','SetFocus','SetCursorPos','SendInput','mouse_event','keybd_event','SetClipboardData'})

class PreferenceTests(unittest.TestCase):
    def test_missing_fails_closed(self):
        registry=Mock();registry.OpenKey.side_effect=OSError('missing')
        with patch.dict(sys.modules,winreg=registry):self.assertFalse(local_cu_preferences.load())
    def test_explicit_dword_only(self):
        registry=MagicMock();registry.REG_DWORD=4
        with patch.dict(sys.modules,winreg=registry):
            for value,kind,expected in [(1,4,True),(0,4,False),('1',1,False),(True,4,False),(2,4,False)]:
                registry.QueryValueEx.return_value=(value,kind);self.assertEqual(local_cu_preferences.load(),expected)
    def test_save_strict_bool(self):
        with self.assertRaises(ValueError):local_cu_preferences.save(1)
    def test_cli_still_default_off(self):
        from computer_use import ComputerUseManager
        with patch.object(local_cu_preferences,'load',return_value=True) as pref:
            self.assertFalse(any(ComputerUseManager().enabled.values()));pref.assert_not_called()


class NativeMessageTests(unittest.TestCase):
    def setUp(self):
        from background_control.native import Native
        self.n=object.__new__(Native);self.n.u=Mock();self.n.backend=Mock()
        self.window={'window_id':1};self.identity={'hwnd':2,'client_size':[100,50]}
        self.element={'kind':'native','identity':self.identity}
        self.n.backend.window.return_value=self.window;self.n.backend.foreground.return_value=99
        self.n.native_identity=Mock(return_value=self.identity)
    def test_rejected_down_not_retried(self):
        self.n.u.PostMessageW.return_value=False
        with self.assertRaises(BackgroundError):self.n.action(self.window,self.element,'message_click',x=1,y=2)
        self.assertEqual(self.n.u.PostMessageW.call_count,1)
    def test_partial_up_failure_reported(self):
        self.n.u.PostMessageW.side_effect=[True,False]
        with self.assertRaisesRegex(BackgroundError,'PARTIAL'):self.n.action(self.window,self.element,'message_click',x=1,y=2)
        self.assertEqual(self.n.u.PostMessageW.call_count,2)
    def test_coordinate_bounds_before_dispatch(self):
        with self.assertRaises(BackgroundError):self.n.action(self.window,self.element,'message_click',x=100,y=2)
        self.n.u.PostMessageW.assert_not_called()
    def test_changed_child_before_dispatch(self):
        self.n.native_identity.return_value={'hwnd':3}
        with self.assertRaises(BackgroundError):self.n.action(self.window,self.element,'message_click',x=1,y=2)
        self.n.u.PostMessageW.assert_not_called()
    def test_dispatch_does_not_claim_business_success(self):
        self.n.u.PostMessageW.return_value=True
        result=self.n.action(self.window,self.element,'message_click',x=1,y=2,button='right')
        self.assertEqual(result['state'],'dispatched_unverified');self.n.backend.focus.assert_not_called()
        self.assertEqual([c.args[1] for c in self.n.u.PostMessageW.call_args_list],[0x204,0x205])

class GuiPreferenceTests(unittest.TestCase):
    def app(self):
        from gui import OpenBridgeApp
        app=object.__new__(OpenBridgeApp);app.cu_startup=Mock();app.cu_startup.get.return_value=True
        app.cu_enabled=Mock();app.service=Mock();app.append_log=Mock();app._refresh_computer_use_button=Mock()
        return app
    def test_cancel_never_saves_or_enables(self):
        app=self.app()
        with patch('gui.messagebox.askyesno',return_value=False),patch('gui.local_cu_preferences.save') as save:
            app.remember_computer_use();save.assert_not_called();app.cu_enabled.set.assert_not_called()
    def test_remember_does_not_hot_enable_running_service(self):
        app=self.app()
        with patch('gui.messagebox.askyesno',return_value=True),patch('gui.local_cu_preferences.save') as save:
            app.remember_computer_use();save.assert_called_once_with(True);app.cu_enabled.set.assert_not_called()
            app.service.computer_use.set_enabled.assert_not_called()
    def test_local_approval_before_prestart_enable(self):
        app=self.app();app.service=None
        with patch('gui.messagebox.askyesno',return_value=True),patch('gui.local_cu_preferences.save') as save:
            app.remember_computer_use();save.assert_called_once_with(True);app.cu_enabled.set.assert_called_once_with(True)

if __name__=='__main__':unittest.main()
