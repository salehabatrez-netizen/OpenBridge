"""Fault injection for the supervisor, policy and cancellation; no real tunnel."""
import queue
import io
import socket
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bridge import BridgeService, ThreadedHTTPServer, McpHandler
from connection_health import HealthTracker
from gui import OpenBridgeApp

class HealthPolicyTests(unittest.TestCase):
    def test_transient_and_recovery(self):
        p = HealthTracker(0)
        self.assertEqual(p.observe(True, 1), 'RUNNING_ONLINE')
        self.assertEqual(p.observe(False, 10), 'DEGRADED')
        self.assertEqual(p.observe(False, 20), 'DEGRADED')
        self.assertEqual(p.observe(True, 30), 'RUNNING_ONLINE')
        self.assertEqual(p.failures, 0)
        self.assertFalse(p.should_rebuild(200))

    def test_three_failures_then_wait_before_rebuild(self):
        p = HealthTracker(0)
        p.observe(True, 1)
        for t in (10, 20, 30): status = p.observe(False, t)
        self.assertEqual(status, 'RECONNECTING')
        self.assertFalse(p.should_rebuild(129))
        self.assertTrue(p.should_rebuild(130))

    def test_no_domain_timeout(self):
        p = HealthTracker(10)
        self.assertFalse(p.should_rebuild(129))
        self.assertTrue(p.should_rebuild(130))

    def test_recent_tool_call_suppresses_false_probe_rebuild(self):
        # A live caller suppresses a rebuild while the outage is still within the
        # absolute ceiling; once the caller goes quiet the rebuild proceeds.
        p = HealthTracker(0)
        self.assertFalse(p.should_rebuild(130, last_client_activity=129))
        self.assertTrue(p.should_rebuild(175, last_client_activity=129))

    def test_activity_cannot_postpone_rebuild_past_max_outage(self):
        """A client polling faster than the 45s window must not block healing forever."""
        p = HealthTracker(0)
        p.observe(True, 1)
        p.observe(False, 10)
        # Caller keeps touching the process every 30s; without the ceiling this
        # returned False indefinitely and the tunnel stayed dead until a human
        # pressed "repair".
        self.assertFalse(p.should_rebuild(100, last_client_activity=90))
        self.assertTrue(p.should_rebuild(191, last_client_activity=190))

    def test_max_outage_is_measured_from_outage_start(self):
        p = HealthTracker(0, max_outage=180)
        p.observe(True, 1)
        p.observe(False, 50)
        self.assertFalse(p.should_rebuild(229, last_client_activity=228))
        self.assertTrue(p.should_rebuild(230, last_client_activity=229))

    def test_initial_failure_is_not_degraded_or_online(self):
        self.assertEqual(HealthTracker(0).observe(False, 1), 'RECONNECTING')

class SupervisorTests(unittest.TestCase):
    def service(self):
        svc = BridgeService(log_callback=lambda _: None)
        svc.log = Mock()
        return svc

    def test_exited_child_retries_with_capped_backoff(self):
        svc = self.service()
        def wait(delay):
            if svc._run_tunnel_once.call_count == 8:
                svc._stop_event.is_set.return_value = True
                return True
            return False
        svc._stop_event = Mock()
        svc._stop_event.is_set.return_value = False
        svc._stop_event.wait.side_effect = wait
        with patch('bridge.find_cloudflared', return_value='cf'), \
             patch.object(svc, '_run_tunnel_once', return_value=False):
            svc._start_tunnel()
        self.assertEqual([c.args[0] for c in svc._stop_event.wait.call_args_list],
                         [1, 2, 4, 8, 16, 30, 30, 30])
        self.assertEqual(svc.reconnect_count, 8)

    def test_stop_cancels_pending_retry(self):
        svc = self.service()
        def crashed(_):
            svc._stop_event.set()
            return False
        with patch('bridge.find_cloudflared', return_value='cf'), \
             patch.object(svc, '_run_tunnel_once', side_effect=crashed) as attempt:
            svc._start_tunnel()
        attempt.assert_called_once()
        self.assertEqual(svc.reconnect_count, 0)

    def test_start_after_stop_cannot_resurrect(self):
        svc = self.service()
        svc.stop()
        with patch.object(svc, '_start_locked') as start:
            svc.start()
        start.assert_not_called()

    def test_missing_binary_stays_local(self):
        svc = self.service()
        with patch('bridge.find_cloudflared', return_value=None), \
             patch.object(svc, '_run_tunnel_once') as run:
            svc._start_tunnel()
        run.assert_not_called()
        self.assertEqual(svc._connection_state, 'RUNNING_LOCAL')

    def test_spawn_error_returns_to_supervisor(self):
        svc = self.service(); svc.port = 1234
        with patch('bridge.subprocess.Popen', side_effect=OSError('unavailable')):
            self.assertFalse(svc._run_tunnel_once('cf'))

    def test_manual_repair_does_not_reset_tools_or_permission(self):
        svc = self.service(); svc.is_running = True
        secret, manager = svc.secret, svc.computer_use
        svc.request_reconnect()
        self.assertTrue(svc._reconnect_event.is_set())
        self.assertEqual(svc.secret, secret)
        self.assertIs(svc.computer_use, manager)

    def test_terminate_escalates_and_reaps_exact_child(self):
        svc = self.service()
        proc = Mock(); proc.poll.return_value = None
        proc.wait.side_effect = [TimeoutError(), 0]
        svc._terminate_tunnel(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 2)

class UIQueueTests(unittest.TestCase):
    def test_worker_posts_without_any_tk_calls(self):
        app = SimpleNamespace(_ui_events=queue.SimpleQueue(), _ui_logs=queue.Queue(1),
                              append_log=Mock(), _deliver_log=Mock(), root=Mock(), _dropped_logs=0)
        fn = Mock()
        thread = threading.Thread(target=lambda: OpenBridgeApp._post_ui(app, fn, 'new'))
        thread.start(); thread.join()
        fn.assert_not_called(); app.root.after.assert_not_called()
        callback, args = app._ui_events.get_nowait()
        callback(*args); fn.assert_called_once_with('new')

    def test_log_flood_cannot_block_control_events(self):
        app = SimpleNamespace(_ui_events=queue.SimpleQueue(), _ui_logs=queue.Queue(1),
                              append_log=Mock(), _deliver_log=Mock(), _dropped_logs=0)
        for _ in range(100): OpenBridgeApp._post_ui(app, app.append_log, 'log')
        fn = Mock(); OpenBridgeApp._post_ui(app, fn)
        self.assertEqual(app._dropped_logs, 99)
        self.assertEqual(app._ui_events.get_nowait()[0], fn)

    def test_stale_log_is_ignored(self):
        app = SimpleNamespace(_generation=2, append_log=Mock())
        OpenBridgeApp._deliver_log(app, 1, 'old')
        app.append_log.assert_not_called()
        OpenBridgeApp._deliver_log(app, 2, 'new')
        app.append_log.assert_called_once_with('new')

class ListenerTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows exclusive socket semantics')
    def test_exclusive_listener_rejects_legacy_reuseaddr_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as old:
            old.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            old.bind(('0.0.0.0', 0)); old.listen(1)
            with self.assertRaises(OSError):
                ThreadedHTTPServer(('0.0.0.0', old.getsockname()[1]), McpHandler)

    def test_bind_retry_uses_real_bound_port_not_released_probe(self):
        attempts = []
        def factory(address, handler):
            attempts.append(address)
            if len(attempts) == 1:
                raise OSError('address already in use')
            return ThreadedHTTPServer(('127.0.0.1', 0), handler)
        with tempfile.TemporaryDirectory() as directory:
            svc = BridgeService(workspace_dir=directory, use_tunnel=False)
            svc.log = Mock()
            try:
                with patch('bridge.ThreadedHTTPServer', side_effect=factory), \
                     patch('bridge.find_available_port') as probe:
                    svc.start()
                self.assertEqual([a[1] for a in attempts], [8765, 8766])
                self.assertEqual(svc.port, svc.server.socket.getsockname()[1])
                self.assertIn(':%d/' % svc.port, svc.local_url)
                probe.assert_not_called()
            finally:
                svc.stop()

    def test_partial_start_cleanup_never_waits_on_unstarted_server(self):
        svc = BridgeService(); svc.log = Mock(); svc.server = Mock()
        server = svc.server
        svc.stop()
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once()

    def test_connection_reset_is_not_reported_as_server_crash(self):
        server = object.__new__(ThreadedHTTPServer)
        callback = Mock()
        with patch('bridge.sys.exc_info', return_value=(ConnectionResetError, ConnectionResetError(), None)), \
             patch.object(McpHandler, 'log_callback', callback), \
             patch('bridge.HTTPServer.handle_error') as fallback:
            server.handle_error(None, None)
        fallback.assert_not_called()
        callback.assert_called_once()

    def test_unexpected_server_errors_are_not_swallowed(self):
        server = object.__new__(ThreadedHTTPServer)
        with patch('bridge.sys.exc_info', return_value=(ValueError, ValueError(), None)), \
             patch('bridge.HTTPServer.handle_error') as fallback:
            server.handle_error(None, None)
        fallback.assert_called_once()

if __name__ == '__main__':
    unittest.main()
