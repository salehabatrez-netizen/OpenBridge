"""URL persistence: a rotated Quick Tunnel domain must be discoverable locally.

Regression cover for the Cloudflare 530 / error 1033 failure mode, where a client
keeps polling a hostname whose tunnel no longer exists.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from bridge import BridgeService, McpHandler, CURRENT_URL_FILE, _public_origin


class UrlFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="obtest_")
        self.svc = BridgeService(workspace_dir=self.dir, log_callback=lambda _: None)
        self.svc.log = Mock()
        # start() arms persistence; these tests drive the internals directly.
        self.svc._url_file_armed = True
        self.path = os.path.join(self.dir, CURRENT_URL_FILE)

    def read(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_publishing_a_url_writes_it(self):
        self.svc._publish_connection("RUNNING_ONLINE", "https://a.trycloudflare.com/mcp/abc")
        self.assertEqual(self.read()["url"], "https://a.trycloudflare.com/mcp/abc")
        self.assertEqual(self.read()["status"], "RUNNING_ONLINE")

    def test_rotation_overwrites_previous_domain(self):
        """The whole point: the file must never keep pointing at the dead domain."""
        self.svc._publish_connection("RUNNING_ONLINE", "https://old.trycloudflare.com/mcp/abc")
        self.svc._publish_connection("RUNNING_ONLINE", "https://new.trycloudflare.com/mcp/abc")
        data = self.read()
        self.assertEqual(data["url"], "https://new.trycloudflare.com/mcp/abc")
        self.assertNotIn("old", data["url"])

    def test_generation_increments_across_rotations(self):
        before = McpHandler.tunnel_generation
        self.svc._publish_connection("RUNNING_ONLINE", "https://x.trycloudflare.com/mcp/a")
        self.svc._publish_connection("RUNNING_ONLINE", "https://y.trycloudflare.com/mcp/a")
        self.assertEqual(self.read()["tunnel_generation"], before + 2)

    def test_stop_marks_address_invalid(self):
        self.svc._publish_connection("RUNNING_ONLINE", "https://a.trycloudflare.com/mcp/abc")
        self.svc._clear_url_file("STOPPED")
        data = self.read()
        self.assertEqual(data["url"], "")
        self.assertEqual(data["status"], "STOPPED")

    def test_clear_does_not_create_file_when_absent(self):
        self.svc._clear_url_file("STOPPED")
        self.assertFalse(os.path.exists(self.path))

    def test_opt_out_never_touches_disk(self):
        svc = BridgeService(workspace_dir=self.dir, log_callback=lambda _: None,
                            persist_url=False)
        svc.log = Mock()
        svc._url_file_armed = True
        svc._publish_connection("RUNNING_ONLINE", "https://a.trycloudflare.com/mcp/abc")
        self.assertFalse(os.path.exists(self.path))

    def test_unstarted_service_never_writes(self):
        """Regression: unit tests on an unstarted service leaked a file into the
        user's real workspace."""
        svc = BridgeService(workspace_dir=self.dir, log_callback=lambda _: None)
        svc.log = Mock()
        svc._publish_connection("RUNNING_ONLINE", "https://a.trycloudflare.com/mcp/abc")
        self.assertFalse(os.path.exists(self.path))

    def test_unwritable_target_does_not_raise(self):
        """A disk failure must never take the tunnel down."""
        with patch("bridge.open", side_effect=OSError("disk full")):
            self.assertFalse(
                self.svc._write_url_file("https://a.trycloudflare.com/mcp/a", "RUNNING_ONLINE"))
        self.svc.log.assert_called()

    def test_no_temp_file_left_behind(self):
        self.svc._publish_connection("RUNNING_ONLINE", "https://a.trycloudflare.com/mcp/abc")
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_write_is_atomic_via_replace(self):
        """Readers must never see a partially written address."""
        with patch("bridge.os.replace") as replace:
            self.svc._write_url_file("https://a.trycloudflare.com/mcp/a", "RUNNING_ONLINE")
        replace.assert_called_once()
        self.assertTrue(replace.call_args[0][1].endswith(CURRENT_URL_FILE))

    def test_file_is_valid_json_with_expected_keys(self):
        self.svc._publish_connection("RUNNING_ONLINE", "https://a.trycloudflare.com/mcp/abc")
        data = self.read()
        for key in ("url", "public_origin", "status", "tunnel_generation",
                    "workspace", "updated_at"):
            self.assertIn(key, data)


class HealthSecrecyTests(unittest.TestCase):
    """/health is unauthenticated, so it must not leak the credential."""

    def test_public_origin_strips_secret(self):
        self.assertEqual(
            _public_origin("https://h.trycloudflare.com/mcp/e4197451d0ddd573020087681c21d5e8"),
            "https://h.trycloudflare.com")

    def test_public_origin_handles_empty(self):
        self.assertEqual(_public_origin(""), "")
        self.assertEqual(_public_origin(None), "")

    def test_health_payload_has_no_secret(self):
        secret = "e4197451d0ddd573020087681c21d5e8"
        origin = _public_origin("https://h.trycloudflare.com/mcp/%s" % secret)
        self.assertNotIn(secret, origin)
        self.assertNotIn("/mcp/", origin)


if __name__ == "__main__":
    unittest.main()
