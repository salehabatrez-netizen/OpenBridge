"""Windows-only worker backend. No SetForegroundWindow/SendInput/clipboard calls."""
import base64
import ctypes as C
from ctypes import wintypes as W
import io
from collections import deque
from game_control.windows import WindowsBackend
from .service import check

PATTERNS={'invoke':('Invoke','Invoke'), 'set_value':('Value','SetValue'),
          'select':('SelectionItem','Select'),'toggle':('Toggle','Toggle'),
          'expand':('ExpandCollapse','Expand'),'collapse':('ExpandCollapse','Collapse'),
          'scroll_up':('Scroll','Scroll'),'scroll_down':('Scroll','Scroll')}

class Native:
    def __init__(self):
        self.backend=WindowsBackend();self.u=self.backend.u
        self.u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];self.u.PostMessageW.restype=W.BOOL
        self.u.IsChild.argtypes=[W.HWND,W.HWND];self.u.IsChild.restype=W.BOOL
        self.u.GetParent.argtypes=[W.HWND];self.u.GetParent.restype=W.HWND
        self.u.IsWindowEnabled.argtypes=[W.HWND];self.u.IsWindowEnabled.restype=W.BOOL
        self.u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)];self.u.GetWindowRect.restype=W.BOOL
        self.u.EnumChildWindows.argtypes=[W.HWND,self.backend.enum_callback,W.LPARAM];self.u.EnumChildWindows.restype=W.BOOL
        self.u.PrintWindow.argtypes=[W.HWND,W.HDC,W.UINT];self.u.PrintWindow.restype=W.BOOL
        self._automation=None

    def automation(self):
        if self._automation is None:
            import comtypes.client
            comtypes.client.GetModule('UIAutomationCore.dll')
            from comtypes.gen import UIAutomationClient as A
            self.A=A;self._automation=comtypes.client.CreateObject(A.CUIAutomation,interface=A.IUIAutomation)
        return self._automation

    def native_identity(self,hwnd,root):
        check(hwnd==root or self.u.IsChild(root,hwnd),'Target child no longer belongs to root')
        check(self.u.IsWindow(hwnd) and self.u.IsWindowVisible(hwnd) and self.u.IsWindowEnabled(hwnd),'Target not visible/enabled')
        pid=W.DWORD();self.u.GetWindowThreadProcessId(hwnd,C.byref(pid))
        cls=C.create_unicode_buffer(256);self.u.GetClassNameW(hwnd,cls,256)
        with self.backend.dpi():
            r=W.RECT();check(self.u.GetWindowRect(hwnd,C.byref(r)),'Cannot get target bounds')
            c=W.RECT();check(self.u.GetClientRect(hwnd,C.byref(c)),'Cannot get client bounds')
        return {'hwnd':hwnd,'pid':pid.value,'class':cls.value,'parent':self.u.GetParent(hwnd) or 0,
                'rect':[r.left,r.top,r.right,r.bottom],'client_size':[c.right,c.bottom]}

    def walk(self,hwnd):
        auto=self.automation();walker=auto.ControlViewWalker
        queue=deque([(auto.ElementFromHandle(hwnd),0)]);count=0
        while queue and count<300:
            element,depth=queue.popleft();count+=1
            yield element
            if depth>=12:continue
            child=walker.GetFirstChildElement(element)
            while child and len(queue)+count<300:
                queue.append((child,depth+1));child=walker.GetNextSiblingElement(child)

    def fingerprint(self,element):
        return {'runtime_id':list(element.GetRuntimeId()),'name':str(element.CurrentName)[:512],
                'control_type':int(element.CurrentControlType),'automation_id':str(element.CurrentAutomationId)[:512]}

    def patterns(self,element):
        if element.CurrentIsPassword or not element.CurrentIsEnabled:return []
        result=[]
        for action,(pattern,_) in PATTERNS.items():
            try:
                if element.GetCurrentPropertyValue(getattr(self.A,'UIA_Is'+pattern+'PatternAvailablePropertyId')):result.append(action)
            except Exception:pass
        return result

    def snapshot(self,window_id,capture=False,format='png',max_size=1280,marks=None):
        window=self.backend.window(window_id);elements={};public=[];warnings=[]
        # Native handles are explicit message targets, not inferred UIA coordinates.
        handles=[window_id]
        @self.backend.enum_callback
        def visit(hwnd,_):
            if len(handles)<300:handles.append(hwnd)
            return len(handles)<300
        self.u.EnumChildWindows(window_id,visit,0)
        for hwnd in handles:
            try:
                identity=self.native_identity(hwnd,window_id);eid='hwnd_'+str(hwnd)
                data={'kind':'native','identity':identity,'actions':['message_click']}
                elements[eid]=data;public.append(dict(data,element_id=eid,coordinate_space='this target HWND client physical pixels'))
            except Exception:continue
        try:
            for element in self.walk(window_id):
                try:
                    fp=self.fingerprint(element);actions=self.patterns(element)
                    if not actions:continue
                    eid='uia_'+str(len(elements));data={'kind':'uia','identity':fp,'actions':actions}
                    elements[eid]=data;public.append(dict(data,element_id=eid))
                except Exception:continue
        except Exception as exc:warnings.append('UIA unavailable: '+type(exc).__name__)
        check(self.backend.window(window_id)==window,'Window moved/changed during snapshot; observe again')
        result={'window':window,'elements':public,'_elements':elements,'warnings':warnings,
                'limits':'Tree bounded to 300 UIA + 300 native nodes; no focus or minimized support; native message support unverified.'}
        if capture:
            try:result.update(self.capture(window_id,max_size,format,marks or []))
            except Exception as exc:result['capture_error']=str(exc)
        return result

    def capture(self,hwnd,max_size,format,marks):
        import win32gui,win32ui
        from PIL import Image,ImageDraw
        with self.backend.dpi():
            r=W.RECT();check(self.u.GetClientRect(hwnd,C.byref(r)),'Client rectangle unavailable')
            width,height=r.right,r.bottom
            check(0<width<=8192 and 0<height<=8192 and width*height<=16000000,'Capture dimensions too large/empty')
            check(all(x<width and y<height for x,y in marks),'Mark outside client image')
            dc=win32gui.GetDC(hwnd);source=None;memory=None;bitmap=None;old=None
            try:
                source=win32ui.CreateDCFromHandle(dc);memory=source.CreateCompatibleDC()
                bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,width,height)
                old=memory.SelectObject(bitmap)
                check(self.u.PrintWindow(hwnd,memory.GetSafeHdc(),3),'PrintWindow rejected; no screen-copy fallback')
                image=Image.frombuffer('RGB',(width,height),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).copy()
            finally:
                if memory and old:memory.SelectObject(old)
                if bitmap:win32gui.DeleteObject(bitmap.GetHandle())
                if memory:memory.DeleteDC()
                if source:source.DeleteDC()
                win32gui.ReleaseDC(hwnd,dc)
        size=image.size;image.thumbnail((max_size,max_size));scale=image.width/width
        draw=ImageDraw.Draw(image)
        for i,(x,y) in enumerate(marks,1):
            x=round(x*scale);y=round(y*scale);draw.line((x-8,y,x+8,y),fill='red',width=2);draw.line((x,y-8,x,y+8),fill='red',width=2);draw.text((x+9,y+2),str(i),fill='red')
        buf=io.BytesIO();image.save(buf,format='JPEG' if format=='jpeg' else 'PNG',**({'quality':85} if format=='jpeg' else {}))
        return {'image':{'mimeType':'image/jpeg' if format=='jpeg' else 'image/png','data':base64.b64encode(buf.getvalue()).decode()},
                'capture':{'method':'PrintWindow client','source_size':size,'image_size':image.size,'scale':scale,'marks_are_preview_only':True,
                           'verified_fresh':False,'warning':'May be black, stale or incomplete for GPU/Chromium/protected content. Never infer success from API return alone.'}}

    def action(self,window,element,action,**args):
        hwnd=window['window_id'];before=self.backend.foreground()
        check(self.backend.window(hwnd)==window,'Target identity/geometry changed; observe again')
        if element['kind']=='native':
            check(action=='message_click','Unsupported native action')
            target=element['identity']['hwnd'];current=self.native_identity(target,hwnd)
            check(current==element['identity'],'Child target changed; observe again')
            x,y=args['x'],args['y'];check(x<current['client_size'][0] and y<current['client_size'][1],'Click outside target client bounds')
            down,up,flag={'left':(0x201,0x202,1),'right':(0x204,0x205,2),'middle':(0x207,0x208,16)}[args.get('button','left')]
            position=(y<<16)|x
            check(self.u.PostMessageW(target,down,flag,position),'Mouse down rejected (UIPI/target); no retry')
            check(self.u.PostMessageW(target,up,0,position),'PARTIAL: mouse up rejected; application may retain pressed state; inspect locally')
            state='dispatched_unverified'
        else:
            check(action in PATTERNS,'Unsupported UIA action')
            found=None
            for current in self.walk(hwnd):
                if self.fingerprint(current)==element['identity']:found=current;break
            check(found is not None,'UIA element disappeared/changed')
            check(action in self.patterns(found),'UIA pattern unavailable or protected/disabled control')
            pattern,method=PATTERNS[action]
            raw=found.GetCurrentPattern(getattr(self.A,'UIA_'+pattern+'PatternId'))
            interface=raw.QueryInterface(getattr(self.A,'IUIAutomation'+pattern+'Pattern'))
            check(self.backend.window(hwnd)==window,'Window changed during UIA resolution')
            if action=='set_value':
                check(not interface.CurrentIsReadOnly,'Value is read-only');interface.SetValue(args['text'])
                state='value_verified' if interface.CurrentValue==args['text'] else 'provider_returned_unverified'
            elif action.startswith('scroll_'):interface.Scroll(2,1 if action=='scroll_up' else 4);state='provider_returned_unverified'
            else:getattr(interface,method)();state='provider_returned_unverified'
        after=self.backend.foreground()
        return {'state':state,'foreground_before':before,'foreground_after':after,'foreground_changed':before!=after,
                'warning':'No focus request made by this tool; target application may activate itself. Verify resulting state; do not replay blindly.'}
