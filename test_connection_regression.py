"""Offline regression tests: no real GUI, cloudflared or service restart."""
import io
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge import BridgeService
from gui import OpenBridgeApp

class ConnectionTests(unittest.TestCase):
    def test_running_workspace_cannot_change(self):
        app = SimpleNamespace(current_status='RUNNING_ONLINE')
        with patch('gui.messagebox.showinfo') as info, patch('gui.filedialog.askdirectory') as dialog:
            OpenBridgeApp.browse_workspace(app)
            info.assert_called_once()
            dialog.assert_not_called()

    def test_stopped_workspace_changes_selection(self):
        var = Mock()
        app = SimpleNamespace(current_status='STOPPED', workspace_var=var,
                              update_prompt_preview=Mock(), append_log=Mock())
        with tempfile.TemporaryDirectory() as directory, patch('gui.filedialog.askdirectory', return_value=directory):
            OpenBridgeApp.browse_workspace(app)
            var.set.assert_called_once()
            app.update_prompt_preview.assert_called_once()

    def test_prompt_uses_actual_service_workspace(self):
        app = SimpleNamespace(current_url='https://example.test/mcp/test',
                              service=SimpleNamespace(workspace='actual-root'),
                              workspace_var=Mock(), txt_prompt=Mock())
        OpenBridgeApp.update_prompt_preview(app)
        self.assertIn('actual-root', app.txt_prompt.insert.call_args.args[1])
        app.workspace_var.get.assert_not_called()

    def test_copy_requires_verified_public_url(self):
        for status, url, expected in [('TUNNELING','https://example.test',False),
                                      ('RUNNING_LOCAL','http://127.0.0.1',False),
                                      ('STOPPED','https://old.test',False),
                                      ('RUNNING_ONLINE','https://ready.test',True)]:
            self.assertEqual(OpenBridgeApp._can_copy(SimpleNamespace(
                current_status=status, current_url=url)), expected)

    def test_stale_callbacks_are_ignored(self):
        callbacks = []
        app = SimpleNamespace(_generation=2, append_log=Mock(),
                              _post_ui=lambda fn: callbacks.append(fn))
        fn = Mock()
        OpenBridgeApp._service_event(app,1,fn,'old')
        callbacks.pop()()
        fn.assert_not_called()
        OpenBridgeApp._service_event(app,2,fn,'new')
        callbacks.pop()()
        fn.assert_called_once_with('new')

    def test_probe_rejects_errors_and_wrong_server(self):
        svc = BridgeService(log_callback=lambda _: None)
        outcomes = [
            {'healthy':False, 'kind':'tls_eof', 'error':'EOF'},
            {'healthy':False, 'kind':'invalid_rpc', 'error':'Wrong MCP server'},
            {'healthy':True, 'kind':'ok', 'error':''},
        ]
        with patch('connection_probe.probe_public_bounded', side_effect=outcomes):
            self.assertFalse(svc._probe_public_mcp('https://example.test/mcp/test'))
            self.assertEqual(svc.last_probe_kind, 'tls_eof')
            self.assertFalse(svc._probe_public_mcp('https://example.test/mcp/test'))
            self.assertTrue(svc._probe_public_mcp('https://example.test/mcp/test'))
            self.assertEqual(svc.last_probe_error, '')

    def monitor(self, answers):
        statuses, urls = [], []
        svc = BridgeService(log_callback=lambda _: None, status_callback=statuses.append, url_callback=urls.append)
        svc.log = Mock()
        svc.port = 12345
        svc._test_snapshots = []
        svc._publish_connection('TUNNELING')
        proc = SimpleNamespace(stdout=io.BytesIO(b'https://test-tunnel.trycloudflare.com\n'),
                               poll=Mock(return_value=None))
        # Run the reader synchronously to make monitor testing deterministic.
        class Reader:
            def __init__(self, target, **kw): self.target = target
            def start(self): self.target()
        def wait(_):
            svc._test_snapshots.append((svc._connection_state, svc.public_url))
            if probe.call_count >= len(answers):
                proc.poll.return_value = 1
        svc._stop_event = Mock()
        svc._stop_event.is_set.return_value = False
        svc._stop_event.wait.side_effect = wait
        with patch('bridge.find_cloudflared',return_value='cloudflared'), \
             patch('bridge.subprocess.Popen',return_value=proc), \
             patch('bridge.threading.Thread',Reader), \
             patch.object(svc,'_probe_public_mcp',side_effect=answers) as probe:
            svc._run_tunnel_once('cloudflared')
        return svc, statuses, urls, proc

    def test_domain_without_probe_success_never_online(self):
        svc, statuses, urls, proc = self.monitor([False])
        self.assertNotIn('RUNNING_ONLINE', statuses)
        self.assertEqual(statuses[-1], 'RECONNECTING')
        self.assertIsNone(svc.public_url)
        self.assertTrue(proc.stdout.closed)

    def test_transient_failure_keeps_url_until_process_exits(self):
        svc, statuses, urls, _ = self.monitor([True, False])
        self.assertEqual(statuses, ['TUNNELING','RUNNING_ONLINE','DEGRADED','RECONNECTING'])
        self.assertEqual(svc._test_snapshots[1][0], 'DEGRADED')
        self.assertEqual(svc._test_snapshots[0][1], svc._test_snapshots[1][1])
        self.assertTrue(urls[0].startswith('https://test-tunnel.'))
        self.assertEqual(urls[-1], '')
        self.assertIsNone(svc.public_url)

    def test_three_failures_disable_ready_state(self):
        svc, statuses, urls, _ = self.monitor([True, False, False, False])
        self.assertEqual(svc._test_snapshots[2][0], 'DEGRADED')
        self.assertEqual(svc._test_snapshots[3], ('RECONNECTING', None))

    def test_transient_recovery_preserves_same_url(self):
        svc, statuses, urls, _ = self.monitor([True, False, True])
        self.assertEqual(statuses, ['TUNNELING', 'RUNNING_ONLINE', 'DEGRADED',
                                   'RUNNING_ONLINE', 'RECONNECTING'])
        self.assertEqual(svc._test_snapshots[0][1], svc._test_snapshots[2][1])
        self.assertEqual(len([url for url in urls if url]), 1)

    def test_restart_rotates_secret(self):
        a, b = BridgeService(), BridgeService()
        self.assertNotEqual(a.mcp_path(), b.mcp_path())

if __name__ == '__main__':
    unittest.main()
