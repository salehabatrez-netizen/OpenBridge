"""Explicit interactive smoke test. ONLY this process's newly created lab windows are approved.
Never imports Minecraft, opens a save, or approves an existing application window.
"""
import base64
import ctypes
import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
import traceback
from computer_use import ComputerUseManager
from game_control.windows import WindowsBackend


def main():
    directory=Path('.input-lab-runs')/time.strftime('%Y%m%d-%H%M%S')
    directory.mkdir(parents=True,exist_ok=False)
    backend=WindowsBackend();previous=backend.foreground()
    # Refuse to compete with physically held modifiers/buttons or test keys.
    if any(backend.u.GetAsyncKeyState(v)&0x8000 for v in [1,2,4,16,17,18,0x5B,0x5C,0x57,0x44]):
        raise RuntimeError('Release keyboard/mouse before running the lab')
    root=tk.Tk();root.title('OpenBridge Input Lab - disposable test window');root.geometry('800x500+80+80')
    root.configure(bg='#10202e');root.resizable(False,False)
    canvas=tk.Canvas(root,bg='#10202e',highlightthickness=0);canvas.pack(fill='both',expand=True)
    canvas.create_text(40,40,text='OPENBRIDGE / INPUT LAB',anchor='w',fill='#72e3b6',font=('Segoe UI',19,'bold'))
    canvas.create_text(40,85,text='Disposable window. No game saves. F8 = stop.',anchor='w',fill='white',font=('Segoe UI',13))
    canvas.create_rectangle(300,220,500,320,fill='#215447',outline='#72e3b6',width=2)
    canvas.create_text(400,270,text='CLICK TARGET',fill='white',font=('Segoe UI',14,'bold'))
    label=canvas.create_text(40,140,text='Preparing native input...',anchor='w',fill='white',font=('Consolas',14))
    detail=canvas.create_text(40,390,text='',anchor='w',fill='#72e3b6',font=('Consolas',12))
    footer=canvas.create_text(40,445,text='Safety tests in progress',anchor='w',fill='#aebdcc',font=('Segoe UI',12))
    second=tk.Toplevel(root);second.title('OpenBridge Input Lab - focus-loss target');second.geometry('400x160+920+80')
    tk.Label(second,text='Disposable focus-loss test window\nNo action should continue here.',font=('Segoe UI',13)).pack(padx=20,pady=40)
    second.withdraw();root.update();hwnd=backend.u.GetAncestor(root.winfo_id(),2)
    assert backend.window(hwnd)['pid']==os.getpid()
    # Local fixture only: disassociate IME from these newly created lab windows.
    # Never change the global input language or any existing application's context.
    imm=ctypes.WinDLL('imm32',use_last_error=True)
    imm.ImmAssociateContextEx.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint]
    imm.ImmAssociateContextEx.restype=ctypes.c_int
    for widget in (root,canvas,second):
        local_hwnd=widget.winfo_id()
        for target in (local_hwnd,backend.u.GetAncestor(local_hwnd,2)):
            local_pid=ctypes.c_ulong()
            backend.u.GetWindowThreadProcessId(target,ctypes.byref(local_pid))
            assert local_pid.value==os.getpid()
            imm.ImmAssociateContextEx(target,None,0)
            imm.ImmAssociateContextEx(target,None,1)  # Existing child windows only.
    states={'held':set(),'combo':False,'clicks':0,'events':[],'motion':0};ui=queue.Queue();managers=[]
    def key(event,down):
        name={87:'w',68:'d',119:'f8'}.get(event.keycode,event.keysym.lower())
        states['events'].append({'kind':'key','key':name,'raw_keysym':event.keysym,'keycode':event.keycode,'down':down,'time':time.monotonic()})
        if down:states['held'].add(name)
        else:states['held'].discard(name)
        # IMEs can replace Tk keysyms. Native virtual-key state is the control evidence.
    root.bind_all('<KeyPress>',lambda e:key(e,True));root.bind_all('<KeyRelease>',lambda e:key(e,False))
    def click(event):
        states['events'].append({'kind':'click','x':event.x,'y':event.y,'time':time.monotonic()})
        if 300<=event.x<=500 and 220<=event.y<=320:states['clicks']+=1
    canvas.bind('<ButtonPress-1>',click)
    canvas.bind('<Motion>',lambda _:states.__setitem__('motion',states['motion']+1))
    def pump():
        if backend.u.GetAsyncKeyState(0x57)&0x8000 and backend.u.GetAsyncKeyState(0x44)&0x8000:
            states['combo']=True
        while True:
            try:fn,done=ui.get_nowait()
            except queue.Empty:break
            try:fn()
            finally:done.set()
        native_held=[name for name,vk in [('W',0x57),('D',0x44),('F8',0x77)] if backend.u.GetAsyncKeyState(vk)&0x8000]
        canvas.itemconfigure(label,text='Native held: '+(', '.join(native_held) or 'NONE'))
        canvas.itemconfigure(detail,text='W+D observed: %s    Target clicks: %d    Mouse events: %d'%('YES' if states['combo'] else 'NO',states['clicks'],states['motion']))
        root.after(20,pump)
    def on_ui(fn):
        done=threading.Event();ui.put((fn,done));assert done.wait(3),'UI event timeout'
    def focus_main():
        second.withdraw();root.deiconify();root.lift();root.focus_force()
    def focus_second():second.deiconify();second.lift();second.focus_force()
    def payload(result):
        text=next(b['text'] for b in result['content'] if b['type']=='text')
        return json.loads(text)
    def save_image(result,name):
        image=next(b for b in result['content'] if b['type']=='image')
        (directory/name).write_bytes(base64.b64decode(image['data']))
    def make_session():
        on_ui(focus_main);time.sleep(.12)
        assert backend.foreground()==hwnd,'Lab failed to obtain foreground; no input will be sent'
        m=ComputerUseManager(desktop=True);managers.append(m)
        child=m._game_client()
        schema=child.request('tools/list');assert len(schema['tools'])==8
        # This local fixture approves ONLY its own lab HWND/PID. Production remote API cannot approve.
        own=backend.window(hwnd);assert own['pid']==os.getpid()
        request=payload(m.call_tool('desktop','GameRequestControl',{'window_id':hwnd}))
        pending=m.pop_game_consent();assert pending and pending['window']['pid']==os.getpid()
        approval=m.approve_game_request(request['request_id'],True);assert approval['state']=='approved'
        status=payload(m.call_tool('desktop','GameControlStatus',{'request_id':request['request_id']}))
        assert status['target_ready'];return m,status['lease_id']
    def observe(m,lease):
        result=m.call_tool('desktop','GameObserve',{'lease_id':lease});assert not result['isError'],payload(result)
        return result,payload(result)['metadata']['frame_id']
    report={'test_scope':'new lab windows in this process only','tests':{},'artifacts':str(directory)}
    def run():
        try:
            on_ui(lambda:canvas.itemconfigure(footer,text='Please CLICK this lab window to begin. Do not touch input during the 8-second test.'))
            print('WAITING_FOR_LOCAL_FOCUS: click OpenBridge Input Lab to start. No input has been sent.',flush=True)
            deadline=time.monotonic()+600
            while backend.foreground()!=hwnd:
                if time.monotonic()>deadline:raise RuntimeError('Local focus not granted; no input sent')
                time.sleep(.1)
            time.sleep(.5)
            states['held'].clear();states['combo']=False;states['clicks']=0
            m,lease=make_session();before,frame=observe(m,lease);save_image(before,'before.png')
            args={'lease_id':lease,'frame_id':frame,'action_id':'combo-click-relative',
                  'steps':[{'keys':['W','D'],'buttons':['left'],'pointer':[400,270],'duration_ms':250},
                           {'keys':['W','D'],'dx':48,'dy':-24,'duration_ms':200}]}
            result=m.call_tool('desktop','GameAct',args);data=payload(result)
            assert not result['isError'],data
            assert data['state']=='completed' and data['cleanup_confirmed'],data
            time.sleep(.1)
            assert states['combo'] and states['clicks']==1,str(states)
            delivered={e['key'] for e in states['events'] if e['kind']=='key' and e['down']}
            assert {'w','d'}<=delivered, 'Application did not receive W/D key-down: '+str(states['events'])
            assert not any(backend.u.GetAsyncKeyState(v)&0x8000 for v in (0x57,0x44,1)), 'Native input still held'
            assert states['motion']>0
            report['tests']['simultaneous_keys_click_relative']=data
            save_image(result,'after-action.png')
            count=len(states['events']);duplicate=payload(m.call_tool('desktop','GameAct',args));time.sleep(.08)
            assert duplicate['deduplicated'] and len(states['events'])==count
            report['tests']['deduplication']='PASS: no second input'
            try:m.call_tool('browser','browser_click',{})
            except PermissionError:report['tests']['cross_adapter_reservation']='PASS'
            else:raise AssertionError('Another adapter bypassed game reservation')
            _,frame=observe(m,lease)
            timer=threading.Timer(.15,lambda:on_ui(focus_second));timer.start()
            result=m.call_tool('desktop','GameAct',{'lease_id':lease,'frame_id':frame,'action_id':'lose-focus',
                'steps':[{'keys':['W'],'duration_ms':1000}]})
            timer.join();data=payload(result);assert data.get('error_code')=='FOCUS_LOST' and data['cleanup_confirmed'],data
            assert not backend.u.GetAsyncKeyState(0x57)&0x8000
            report['tests']['focus_loss']=data
            m.shutdown();on_ui(focus_main)
            m,lease=make_session();_,frame=observe(m,lease)
            def trigger_f8():
                assert backend.foreground()==hwnd
                event=backend.INPUT(type=1);event.ki.wScan=backend.u.MapVirtualKeyW(0x77,0);event.ki.dwFlags=8
                backend.send(event)
                try:time.sleep(.10)
                finally:event.ki.dwFlags=10;backend.send(event)
            timer=threading.Timer(.15,trigger_f8);timer.start()
            result=m.call_tool('desktop','GameAct',{'lease_id':lease,'frame_id':frame,'action_id':'f8-stop',
                'steps':[{'keys':['W'],'duration_ms':1000}]})
            timer.join();data=payload(result);assert data.get('error_code') in ('CANCELLED','NO_LEASE') and data['cleanup_confirmed'],data
            assert not backend.u.GetAsyncKeyState(0x57)&0x8000
            report['tests']['native_f8']=data;m.shutdown()
            for mode in ('master_off','stdin_eof'):
                m,lease=make_session();_,frame=observe(m,lease);child=m.game_child
                def stop(mode=mode,m=m,child=child):
                    if mode=='master_off':m.set_enabled(False)
                    else:child.process.stdin.close()
                timer=threading.Timer(.15,stop);timer.start()
                try:
                    result=m.call_tool('desktop','GameAct',{'lease_id':lease,'frame_id':frame,'action_id':mode,
                        'steps':[{'keys':['W'],'duration_ms':1000}]})
                    receipt=payload(result)
                except (RuntimeError,OSError,ValueError) as exc:receipt={'state':'unknown','reason':type(exc).__name__,'do_not_replay':True}
                timer.join(4);assert not timer.is_alive()
                assert child.released.wait(1),'No release acknowledgement'
                time.sleep(.1)
                assert not backend.u.GetAsyncKeyState(0x57)&0x8000,'W is still held'
                report['tests'][mode]={'cleanup_confirmed':True,'receipt':receipt};m.shutdown()
            time.sleep(.1)
            on_ui(lambda:canvas.itemconfigure(footer,text='PASS: combo / pointer / dedup / focus / F8 / OFF / EOF'))
            time.sleep(.1)
            png,w,h,_=backend.capture(backend.window(hwnd)['rect'],1280)
            (directory/'verified.png').write_bytes(base64.b64decode(png))
            report['success']=True
        except Exception:
            report['success']=False;report['error']=traceback.format_exc()
        finally:
            for m in managers:
                try:m.shutdown()
                except Exception:pass
            report['events']=states['events'];report['final_key_state']={str(v):bool(backend.u.GetAsyncKeyState(v)&0x8000) for v in (0x57,0x44,0x77)}
            (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
            on_ui(lambda:root.after(500,root.destroy))
    root.after(20,pump);root.after(200,lambda:threading.Thread(target=run,daemon=True).start())
    root.after(660000,root.destroy);root.mainloop()
    for m in managers:m.shutdown()
    if backend.foreground() in (0,hwnd):backend.u.SetForegroundWindow(previous)
    if not report.get('success'):raise SystemExit(1)

if __name__=='__main__':main()
