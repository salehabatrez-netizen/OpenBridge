"""Opt-in Windows smoke. Creates/cleans ONLY its own non-activating test windows."""
import sys
if '--run' not in sys.argv:
    print('Use --run to create disposable non-activating Windows test windows.');raise SystemExit(0)
import ctypes
import json
from pathlib import Path
import threading
import time
import win32con as K
import win32gui as G
from background_control.service import BackgroundService

ready=threading.Event();stop=threading.Event();shared={};report={}
def ui():
    windows=[]
    try:
        parent=G.CreateWindowEx(0x08000000,'Static','OpenBridge background disposable test',K.WS_OVERLAPPEDWINDOW,60,60,480,260,0,0,0,None);windows.append(parent)
        edit=G.CreateWindowEx(0,'Edit','before',K.WS_CHILD|K.WS_VISIBLE|K.WS_BORDER,20,25,240,35,parent,101,0,None)
        button=G.CreateWindowEx(0,'Button','Background checkbox',K.WS_CHILD|K.WS_VISIBLE|K.BS_AUTOCHECKBOX,20,90,240,40,parent,102,0,None)
        G.ShowWindow(parent,K.SW_SHOWNOACTIVATE);G.UpdateWindow(parent)
        cover=G.CreateWindowEx(0x08000000,'Static','OpenBridge test occluder',K.WS_OVERLAPPEDWINDOW,60,60,480,260,0,0,0,None);windows.append(cover)
        G.ShowWindow(cover,K.SW_SHOWNOACTIVATE);G.UpdateWindow(cover)
        shared.update(parent=parent,edit=edit,button=button,cover=cover);ready.set()
        while not stop.wait(.01):G.PumpWaitingMessages()
    except Exception as exc:shared['error']=str(exc);ready.set()
    finally:
        for hwnd in reversed(windows):
            if G.IsWindow(hwnd):G.DestroyWindow(hwnd)
thread=threading.Thread(target=ui);thread.start()
s=BackgroundService(Path(__file__).resolve().parent,lambda:True)
def text(result):return json.loads(result['content'][0]['text'])
def snapshot(capture=False):
    result=s.call('BackgroundSnapshot',{'window_id':shared['parent'],'capture':capture,'format':'jpeg','marks':[[30,30]] if capture else []})
    data=text(result)
    if result['isError']:raise RuntimeError(data)
    return data,result
try:
    assert ready.wait(5) and 'error' not in shared,shared
    before=G.GetForegroundWindow();report['foreground_before']=before;report['test_windows']=dict(shared)
    data,result=snapshot(True)
    report['element_count']=len(data['elements']);report['warnings']=data['warnings'];report['capture']=data.get('capture',data.get('capture_error'))
    assert any(e['element_id']=='hwnd_'+str(shared['button']) for e in data['elements'])
    assert any(b['type']=='image' for b in result['content']),data
    report['printwindow_image_returned']=True
    edit=next((e for e in data['elements'] if e['kind']=='uia' and 'set_value' in e['actions']),None)
    assert edit,'UIA Edit Value pattern unavailable'
    a={'snapshot_id':data['snapshot_id'],'element_id':edit['element_id'],'action_id':data['session_prefix']+'edit','action':'set_value','text':'后台测试 background value'}
    receipt=text(s.call('BackgroundAction',a));report['uia_value']=receipt
    assert receipt['state']=='value_verified',receipt
    assert G.GetWindowText(shared['edit'])=='后台测试 background value'
    duplicate=text(s.call('BackgroundAction',a));assert duplicate['deduplicated'];report['duplicate_suppressed']=True
    data,_=snapshot()
    a={'snapshot_id':data['snapshot_id'],'element_id':'hwnd_'+str(shared['button']),'action_id':data['session_prefix']+'click','action':'message_click','x':10,'y':15}
    receipt=text(s.call('BackgroundAction',a));report['message_click']=receipt
    assert receipt['state']=='dispatched_unverified',receipt
    # Wait only for this disposable checkbox to process the posted messages.
    limit=time.monotonic()+1
    while not G.SendMessage(shared['button'],K.BM_GETCHECK,0,0) and time.monotonic()<limit:time.sleep(.01)
    report['checkbox_checked']=G.SendMessage(shared['button'],K.BM_GETCHECK,0,0)==1
    assert report['checkbox_checked'],'Target did not process message click'
    report['foreground_after']=G.GetForegroundWindow();report['foreground_unchanged']=before==report['foreground_after']
    own={shared['parent'],shared['edit'],shared['button'],shared['cover']}
    samples=[before,report['foreground_after']]+[report[k][f] for k in ('uia_value','message_click') for f in ('foreground_before','foreground_after')]
    report['test_target_stayed_background_at_samples']=not any(h in own for h in samples)
    assert report['test_target_stayed_background_at_samples'],'A disposable target was foreground at an observation point'
    report['focus_scope']='Sampled checks only, not continuous tracing. Other foreground windows may change while the user works.'
    report['passed']=True
finally:
    s.stop();stop.set();thread.join(5)
    Path('background_repair_20260919').mkdir(exist_ok=True)
    Path('background_repair_20260919/smoke_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
