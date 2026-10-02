"""Win32 scan-code/relative input and physical-pixel client-area screenshots."""
import base64
import contextlib
import ctypes as C
from ctypes import wintypes as W
import io
import os
import time
from .engine import ControlError, require

class WindowsBackend:
    def __init__(self):
        if os.name != 'nt': raise RuntimeError('Game input requires Windows')
        self.u=C.WinDLL('user32',use_last_error=True);self.k=C.WinDLL('kernel32',use_last_error=True)
        def signature(dll,name,args,result):
            f=getattr(dll,name);f.argtypes=args;f.restype=result;return f
        signature(self.u,'GetForegroundWindow',[],W.HWND)
        signature(self.u,'IsWindow',[W.HWND],W.BOOL)
        signature(self.u,'IsWindowVisible',[W.HWND],W.BOOL)
        signature(self.u,'IsIconic',[W.HWND],W.BOOL)
        signature(self.u,'GetAncestor',[W.HWND,W.UINT],W.HWND)
        signature(self.u,'GetWindowThreadProcessId',[W.HWND,C.POINTER(W.DWORD)],W.DWORD)
        signature(self.u,'GetWindowTextW',[W.HWND,W.LPWSTR,C.c_int],C.c_int)
        signature(self.u,'GetClassNameW',[W.HWND,W.LPWSTR,C.c_int],C.c_int)
        signature(self.u,'GetClientRect',[W.HWND,C.POINTER(W.RECT)],W.BOOL)
        signature(self.u,'ClientToScreen',[W.HWND,C.POINTER(W.POINT)],W.BOOL)
        signature(self.u,'SetForegroundWindow',[W.HWND],W.BOOL)
        signature(self.u,'SetCursorPos',[C.c_int,C.c_int],W.BOOL)
        signature(self.u,'GetAsyncKeyState',[C.c_int],C.c_short)
        signature(self.u,'MapVirtualKeyW',[W.UINT,W.UINT],W.UINT)
        signature(self.u,'SetThreadDpiAwarenessContext',[W.HANDLE],W.HANDLE)
        signature(self.k,'OpenProcess',[W.DWORD,W.BOOL,W.DWORD],W.HANDLE)
        signature(self.k,'GetProcessTimes',[W.HANDLE]+[C.POINTER(W.FILETIME)]*4,W.BOOL)
        signature(self.k,'CloseHandle',[W.HANDLE],W.BOOL)
        class MI(C.Structure):
            _fields_=[('dx',W.LONG),('dy',W.LONG),('mouseData',W.DWORD),('dwFlags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
        class KI(C.Structure):
            _fields_=[('wVk',W.WORD),('wScan',W.WORD),('dwFlags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
        class U(C.Union): _fields_=[('mi',MI),('ki',KI)]
        class INPUT(C.Structure): _anonymous_=('data',);_fields_=[('type',W.DWORD),('data',U)]
        self.INPUT=INPUT
        signature(self.u,'SendInput',[W.UINT,C.POINTER(INPUT),C.c_int],W.UINT)
        self.enum_callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        signature(self.u,'EnumWindows',[self.enum_callback,W.LPARAM],W.BOOL)

    @contextlib.contextmanager
    def dpi(self):
        previous=self.u.SetThreadDpiAwarenessContext(W.HANDLE(-4))
        require(bool(previous),'DPI_ERROR','Per-monitor physical coordinates unavailable')
        try:yield
        finally:self.u.SetThreadDpiAwarenessContext(previous)

    def window(self, hwnd):
        require(self.u.IsWindow(hwnd) and self.u.IsWindowVisible(hwnd) and not self.u.IsIconic(hwnd),
                'WINDOW_UNAVAILABLE','Window is closed, hidden or minimized')
        require(self.u.GetAncestor(hwnd,2)==hwnd,'WINDOW_UNAVAILABLE','Select a top-level window')
        pid=W.DWORD();self.u.GetWindowThreadProcessId(hwnd,C.byref(pid))
        handle=self.k.OpenProcess(0x1000,False,pid.value)
        require(bool(handle),'WINDOW_UNAVAILABLE','Cannot verify target process identity')
        try:
            times=[W.FILETIME() for _ in range(4)]
            require(self.k.GetProcessTimes(handle,*[C.byref(t) for t in times]),'WINDOW_UNAVAILABLE','Cannot read process start time')
            creation=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
        finally:self.k.CloseHandle(handle)
        title=C.create_unicode_buffer(512);cls=C.create_unicode_buffer(256)
        self.u.GetWindowTextW(hwnd,title,512);self.u.GetClassNameW(hwnd,cls,256)
        with self.dpi():
            rect=W.RECT();origin=W.POINT(0,0)
            require(self.u.GetClientRect(hwnd,C.byref(rect)) and self.u.ClientToScreen(hwnd,C.byref(origin)),
                    'WINDOW_UNAVAILABLE','Cannot locate client rectangle')
        width=rect.right-rect.left;height=rect.bottom-rect.top
        require(width>0 and height>0 and width*height<=16777216,'WINDOW_UNAVAILABLE','Unsupported client dimensions')
        return {'window_id':hwnd,'pid':pid.value,'title':title.value,'class':cls.value,
                'identity':'%d:%d:%d:%s'%(hwnd,pid.value,creation,cls.value),
                'rect':[origin.x,origin.y,origin.x+width,origin.y+height], 'coordinate_space':'physical-screen-pixels'}

    def windows(self):
        result=[]
        @self.enum_callback
        def visit(hwnd,_):
            try:
                info=self.window(hwnd)
                if info['title']:result.append(info)
            except ControlError:pass
            return True
        self.u.EnumWindows(visit,0)
        return result

    def foreground(self):return self.u.GetForegroundWindow() or 0
    def focus(self,hwnd):self.u.SetForegroundWindow(hwnd)
    def emergency_pressed(self):return bool(self.u.GetAsyncKeyState(0x77)&0x8000)  # F8, never injectable via this API.
    def pointer(self,x,y):
        with self.dpi():require(self.u.SetCursorPos(x,y),'INPUT_REJECTED','SetCursorPos rejected')
    def send(self,event):
        require(self.u.SendInput(1,C.byref(event),C.sizeof(event))==1,
                'INPUT_REJECTED','SendInput rejected; check desktop/integrity level. Do not elevate or retry blindly.')
    def relative(self,dx,dy):
        event=self.INPUT(type=0);event.mi.dx=dx;event.mi.dy=dy;event.mi.dwFlags=1;self.send(event)
    def input(self,kind,value,down):
        event=self.INPUT()
        if kind=='button':
            event.type=0;event.mi.dwFlags={'left':(2,4),'right':(8,16),'middle':(32,64)}[value][0 if down else 1]
        else:
            vk=ord(value) if len(value)==1 else {'SPACE':32,'ENTER':13,'ESC':27,'TAB':9,'BACKSPACE':8,
                'SHIFT':16,'CTRL':17,'UP':38,'DOWN':40,'LEFT':37,'RIGHT':39,'F1':112,'F2':113,'F3':114,'F4':115}[value]
            event.type=1;event.ki.wScan=self.u.MapVirtualKeyW(vk,0)
            event.ki.dwFlags=8|(0 if down else 2)|(1 if value in {'UP','DOWN','LEFT','RIGHT'} else 0)
        self.send(event)
    def capture(self,rect,max_size):
        # Pillow is already used by the desktop stack. No heavy model or capture driver.
        from PIL import ImageGrab, Image
        started=time.perf_counter()
        with self.dpi():image=ImageGrab.grab(bbox=tuple(rect),all_screens=True)
        require(image.size==(rect[2]-rect[0],rect[3]-rect[1]),'CAPTURE_ERROR','Capture dimensions disagree with client rectangle')
        grabbed=time.perf_counter()
        image.thumbnail((max_size,max_size),Image.Resampling.LANCZOS)
        resized=time.perf_counter()
        output=io.BytesIO();image.convert('RGB').save(output,format='PNG')
        raw=output.getvalue();encoded=time.perf_counter()
        payload=base64.b64encode(raw).decode('ascii');finished=time.perf_counter()
        self.last_capture_metrics={
            'grab_ms':round((grabbed-started)*1000,3),
            'resize_ms':round((resized-grabbed)*1000,3),
            'png_encode_ms':round((encoded-resized)*1000,3),
            'base64_ms':round((finished-encoded)*1000,3),
            'total_ms':round((finished-started)*1000,3),
            'png_bytes':len(raw),'base64_bytes':len(payload),
        }
        return payload,image.width,image.height,'Pillow.ImageGrab/Win32'
