"""Exercise unified Tk button on a separate hidden test GUI, no desktop actions."""
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch
from gui import OpenBridgeApp
from computer_use import ComputerUseManager

root=tk.Tk();root.withdraw()
app=OpenBridgeApp(root)
manager=ComputerUseManager()
app.service=SimpleNamespace(is_running=True,require_auth=True,computer_use=manager,workspace='test')
app.current_status='RUNNING_ONLINE'
app.current_url='https://test.invalid/mcp/unchanged'
failures=[]
steps=0

def check():
    global steps
    try:
        if app._cu_busy:
            root.after(20,check);return
        assert app.current_url=='https://test.invalid/mcp/unchanged'
        if steps==0:
            assert not any(manager.enabled.values())
            app.toggle_computer_use()
        elif steps==1:
            assert all(manager.enabled.values())
            assert app.btn_computer_use.cget('text')=='关闭 Computer Use'
            app.toggle_computer_use()
        elif steps==2:
            assert not any(manager.enabled.values())
            app.toggle_computer_use()
        else:
            assert all(manager.enabled.values())
            print('GUI SINGLE-BUTTON OFF/ON/OFF/ON, URL UNCHANGED: PASS',flush=True)
            root.quit();return
        steps+=1
        root.after(20,check)
    except Exception as exc:
        failures.append(exc);root.quit()

def timeout():
    failures.append(RuntimeError('GUI smoke timed out'));root.quit()

try:
    with patch('gui.messagebox.askyesno',return_value=True):
        root.after(0,check);root.after(10000,timeout);root.mainloop()
finally:
    manager.shutdown();root.destroy()
if failures:raise failures[0]
