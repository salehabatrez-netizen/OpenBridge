"""Real Tk layout capture. Does not start a server or alter production permissions."""
from pathlib import Path
import tkinter as tk
from PIL import ImageGrab
from gui import OpenBridgeApp

root = tk.Tk()
app = OpenBridgeApp(root)
root.title('OpenBridge · 新界面预览（独立窗口，未启动服务）')
root.geometry('1040x840+40+30')
root.attributes('-topmost', True)
app.set_status('STOPPED')
failures = []

def capture():
    try:
        root.update_idletasks()
        print('SCREEN', root.winfo_screenwidth(), root.winfo_screenheight())
        print('WINDOW', root.winfo_width(), root.winfo_height())
        for name in ('btn_toggle_bridge', 'btn_copy_prompt', 'btn_computer_use', 'url_entry', 'notebook'):
            w = getattr(app, name)
            assert w.winfo_ismapped(), name
            assert w.winfo_rootx() + w.winfo_width() <= root.winfo_rootx() + root.winfo_width(), name + ' clipped'
            assert w.winfo_rooty() + w.winfo_height() <= root.winfo_rooty() + root.winfo_height(), name + ' clipped'
        path = Path('test-artifacts/gui-refresh.png')
        path.parent.mkdir(exist_ok=True)
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(path)
        print('REAL TK LAYOUT CAPTURE PASS:', path)
    except Exception as exc:
        failures.append(exc)
    finally:
        root.destroy()
root.after(1600, capture)
root.mainloop()
if failures:
    raise failures[0]
