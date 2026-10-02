"""Offscreen render check for the activity strip. Builds the real GUI, never
starts a Bridge, never touches the desktop."""
import tkinter as tk

import gui
from computer_use import ActivityMonitor


class FakeCU:
    def __init__(self):
        self.activity = ActivityMonitor()


class FakeService:
    is_running = True
    require_auth = True
    workspace = "."
    last_healthy_at = None
    reconnects = 0
    def __init__(self):
        self.computer_use = FakeCU()


def main():
    root = tk.Tk()
    root.withdraw()
    app = gui.OpenBridgeApp(root)

    app._refresh_activity(force=True)
    print("no service   :", app.activity_var.get())
    assert "服务未启动" in app.activity_var.get()
    assert str(app.btn_pause_cu["state"]) == "disabled"

    app.service = FakeService()
    monitor = app.service.computer_use.activity
    app._refresh_activity(force=True)
    print("idle         :", app.activity_var.get())
    assert "空闲" in app.activity_var.get()
    assert str(app.btn_pause_cu["state"]) == "normal"

    seq = monitor.begin("desktop/Screenshot")
    app._refresh_activity(force=True)
    print("busy         :", app.activity_var.get())
    assert "AI 正在操作" in app.activity_var.get()

    monitor.begin("browser/browser_snapshot")
    app._refresh_activity(force=True)
    print("busy+queued  :", app.activity_var.get())
    assert "排队" in app.activity_var.get()

    monitor.end(seq, ok=True)
    app._refresh_activity(force=True)

    app.toggle_cu_pause()
    print("paused       :", app.activity_var.get())
    print("pause button :", app.btn_pause_cu["text"])
    assert monitor.paused
    assert "已暂停" in app.activity_var.get()
    assert app.btn_pause_cu["text"] == "恢复 AI 操作"

    app.toggle_cu_pause()
    print("resumed btn  :", app.btn_pause_cu["text"])
    assert not monitor.paused
    assert app.btn_pause_cu["text"] == "暂停 AI 操作"

    app.append_log("[COMPUTER USE] > desktop/Screenshot 开始")
    dump = app.txt_log.dump("1.0", "end", tag=True)
    assert any(item[1] == "cu" for item in dump if item[0] == "tagon"), "missing cu colour tag"
    print("log tag      : [COMPUTER USE] -> 'cu' colour OK")

    root.destroy()
    print("\nGUI SMOKE OK")


if __name__ == "__main__":
    main()
