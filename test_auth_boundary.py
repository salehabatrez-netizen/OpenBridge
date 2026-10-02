"""Authentication boundary: an unauthenticated server must never be reachable
beyond this machine (no public tunnel, loopback-only listener)."""
import os
import shutil
import tempfile
import unittest
import urllib.request

import bridge
from bridge import BridgeService


class AuthBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ob_auth_")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_public_tunnel_without_auth_is_refused(self):
        with self.assertRaises(ValueError):
            BridgeService(workspace_dir=self.ws, use_tunnel=True, require_auth=False)

    def test_tunnel_with_auth_is_allowed(self):
        svc = BridgeService(workspace_dir=self.ws, use_tunnel=True, require_auth=True)
        self.assertTrue(svc.secret)

    def test_no_auth_listens_on_loopback_only(self):
        svc = BridgeService(workspace_dir=self.ws, use_tunnel=False, require_auth=False)
        svc.start()
        try:
            self.assertEqual(svc.server.server_address[0], "127.0.0.1")
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % svc.port, timeout=5) as r:
                self.assertEqual(r.status, 200)
        finally:
            svc.stop()

    def test_authenticated_server_requires_the_secret(self):
        svc = BridgeService(workspace_dir=self.ws, use_tunnel=False, require_auth=True)
        self.assertEqual(svc.mcp_path(), "/mcp/%s" % svc.secret)
        self.assertEqual(len(svc.secret), 32)


if __name__ == "__main__":
    unittest.main()
