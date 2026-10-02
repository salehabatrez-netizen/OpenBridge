"""Behaviour tests for the Computer Use activity indicator and local soft pause."""
import threading
import time
import unittest

from computer_use import ActivityMonitor, ComputerUseManager


class ActivityMonitorTests(unittest.TestCase):
    def test_idle_snapshot(self):
        mon = ActivityMonitor()
        snap = mon.snapshot()
        self.assertFalse(snap["busy"])
        self.assertFalse(snap["paused"])
        self.assertIsNone(snap["current"])
        self.assertIsNone(snap["last"])

    def test_busy_then_idle_with_counters(self):
        mon = ActivityMonitor()
        seq = mon.begin("desktop/Screenshot")
        snap = mon.snapshot()
        self.assertTrue(snap["busy"])
        self.assertEqual(snap["current"], "desktop/Screenshot")
        self.assertEqual(snap["pending"], 1)
        mon.end(seq, ok=True)
        snap = mon.snapshot()
        self.assertFalse(snap["busy"])
        self.assertEqual(snap["counters"], {"ok": 1, "error": 0})
        self.assertEqual(snap["last"]["label"], "desktop/Screenshot")
        self.assertTrue(snap["last"]["ok"])

    def test_failure_counted_separately(self):
        mon = ActivityMonitor()
        mon.end(mon.begin("browser/click"), ok=False)
        snap = mon.snapshot()
        self.assertEqual(snap["counters"], {"ok": 0, "error": 1})
        self.assertFalse(snap["last"]["ok"])

    def test_concurrent_calls_report_queue_depth(self):
        mon = ActivityMonitor()
        first = mon.begin("desktop/Click")
        time.sleep(0.01)
        mon.begin("browser/browser_snapshot")
        snap = mon.snapshot()
        self.assertEqual(snap["pending"], 2)
        # Oldest in-flight action is the one shown to the user.
        self.assertEqual(snap["current"], "desktop/Click")
        mon.end(first)
        self.assertEqual(mon.snapshot()["current"], "browser/browser_snapshot")

    def test_end_is_idempotent(self):
        mon = ActivityMonitor()
        seq = mon.begin("desktop/Type")
        mon.end(seq)
        self.assertEqual(mon.end(seq), 0.0)
        self.assertEqual(mon.snapshot()["counters"], {"ok": 1, "error": 0})

    def test_pause_blocks_and_resume_restores(self):
        mon = ActivityMonitor()
        mon.check_allowed("desktop/Click")  # not paused: no raise
        mon.set_paused(True)
        self.assertTrue(mon.paused)
        with self.assertRaises(PermissionError) as ctx:
            mon.check_allowed("desktop/Click")
        self.assertIn("Paused by the local user", str(ctx.exception))
        mon.set_paused(False)
        mon.check_allowed("desktop/Click")

    def test_no_leak_under_threads(self):
        mon = ActivityMonitor()
        def work():
            for _ in range(50):
                mon.end(mon.begin("desktop/Wait"))
        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        snap = mon.snapshot()
        self.assertFalse(snap["busy"])
        self.assertEqual(snap["pending"], 0)
        self.assertEqual(snap["counters"]["ok"], 200)


class ManagerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.manager = ComputerUseManager(desktop=True, browser=True)
        self.calls = []
        self.manager._dispatch_tool = lambda target, name, arguments=None: (
            self.calls.append((target, name)) or {"content": [{"type": "text", "text": "ok"}]})

    def test_status_exposes_activity(self):
        status = self.manager.status()
        self.assertIn("activity", status)
        self.assertFalse(status["activity"]["busy"])
        self.assertFalse(status["activity"]["paused"])

    def test_call_tool_tracks_and_logs_pairs(self):
        logs = []
        self.manager.log = logs.append
        self.manager.call_tool("desktop", "Screenshot", {})
        self.assertEqual(self.calls, [("desktop", "Screenshot")])
        self.assertEqual(len(logs), 2)
        self.assertIn("> desktop/Screenshot 开始", logs[0])
        self.assertIn("< desktop/Screenshot 完成", logs[1])
        self.assertFalse(self.manager.activity.snapshot()["busy"])

    def test_error_result_marks_failure(self):
        logs = []
        self.manager.log = logs.append
        self.manager._dispatch_tool = lambda *a, **k: {"content": [], "isError": True}
        self.manager.call_tool("browser", "browser_navigate", {})
        self.assertIn("失败", logs[1])
        self.assertEqual(self.manager.activity.snapshot()["counters"]["error"], 1)

    def test_exception_still_clears_activity(self):
        boom = RuntimeError("adapter died")
        def explode(*a, **k):
            raise boom
        self.manager._dispatch_tool = explode
        with self.assertRaises(RuntimeError):
            self.manager.call_tool("desktop", "Click", {})
        snap = self.manager.activity.snapshot()
        self.assertFalse(snap["busy"], "a crashed call must not leave the light stuck on")
        self.assertEqual(snap["counters"]["error"], 1)

    def test_paused_call_is_refused_without_dispatch(self):
        self.manager.activity.set_paused(True)
        with self.assertRaises(PermissionError):
            self.manager.call_tool("desktop", "Click", {"x": 1, "y": 2})
        self.assertEqual(self.calls, [], "paused must not reach the desktop")
        self.manager.activity.set_paused(False)
        self.manager.call_tool("desktop", "Click", {"x": 1, "y": 2})
        self.assertEqual(self.calls, [("desktop", "Click")])

    def test_pause_is_not_remotely_reachable(self):
        from computer_use import COMPUTER_USE_SPEC
        names = {spec["name"] for spec in COMPUTER_USE_SPEC}
        self.assertEqual(names, {"computer_use_status", "computer_use_tools", "computer_use_call"})
        for spec in COMPUTER_USE_SPEC:
            body = repr(spec).lower()
            self.assertNotIn("pause", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
