"""bridge.py v3: pipe-leak fix, session pruning, exec audit, _coord kill guard, charter loader."""
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

import bridge
from bridge import McpHandler, ThreadedHTTPServer, WorkspaceTools

PY = '"%s"' % sys.executable


def wait_done(tools, cid, timeout=20):
    t0 = time.time()
    while tools.sessions[cid].status == "running" and time.time() - t0 < timeout:
        time.sleep(0.05)
    return tools.sessions[cid]


def cid_of(output):
    return re.search(r"command_id: (\S+)", output).group(1)


class Workspace(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="obv3-")
        self.tools = WorkspaceTools(self.ws)
        bridge._guard_cache.clear()
        bridge._charter_cache.update(key=None, text=None)

    def tearDown(self):
        for sess in list(self.tools.sessions.values()):
            sess.kill()
        shutil.rmtree(self.ws, ignore_errors=True)

    def audit_lines(self):
        path = os.path.join(self.ws, "logs", "exec_audit.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()


class PipeLeakTests(Workspace):
    def test_pipes_closed_after_command_finishes(self):
        out = self.tools.run_command('%s -c "print(123)"' % PY, timeout_ms=20000)
        self.assertIn("123", out)
        sess = wait_done(self.tools, cid_of(out))
        self.assertTrue(sess.proc.stdout.closed)
        self.assertTrue(sess.proc.stdin.closed)

    def test_background_output_still_readable_after_exit(self):
        out = self.tools.run_command('%s -c "print(456)"' % PY, background=True)
        cid = cid_of(out)
        wait_done(self.tools, cid)
        self.assertIn("456", self.tools.get_command_output(cid))
        self.assertIn("not running", self.tools.send_command_input(cid, "x"))

    def test_finished_sessions_are_pruned(self):
        saved = bridge.MAX_FINISHED_SESSIONS
        bridge.MAX_FINISHED_SESSIONS = 3
        try:
            for i in range(7):
                self.tools.run_command('%s -c "print(%d)"' % (PY, i), timeout_ms=20000)
                wait_done(self.tools, max(self.tools.sessions, key=lambda k: self.tools.sessions[k].started_at))
            # prune runs before each insert: at most MAX finished + the newest one
            self.assertLessEqual(len(self.tools.sessions), 4)
            self.assertIn("kept for", self.tools.get_command_output("cmd-does-not-exist"))
        finally:
            bridge.MAX_FINISHED_SESSIONS = saved

    def test_old_sessions_expire_but_running_ones_stay(self):
        out = self.tools.run_command('%s -c "import time; time.sleep(30)"' % PY, background=True)
        running = cid_of(out)
        done = cid_of(self.tools.run_command('%s -c "print(1)"' % PY, timeout_ms=20000))
        wait_done(self.tools, done)
        with self.tools._sessions_lock:
            self.tools._prune_sessions(now=time.time() + bridge.FINISHED_SESSION_TTL + 5)
        self.assertNotIn(done, self.tools.sessions)
        self.assertIn(running, self.tools.sessions)


class AuditTests(Workspace):
    def test_exec_and_end_lines_parse_like_the_guardian_expects(self):
        cid = cid_of(self.tools.run_command('%s -c "print(1)"' % PY, timeout_ms=20000))
        wait_done(self.tools, cid)
        time.sleep(0.1)
        lines = self.audit_lines()
        tags = [re.match(r"\S+ \S+ \[(\S+)\]", l).group(1) for l in lines]
        self.assertEqual(tags[:2], ["EXEC", "END"])
        for line in lines:
            time.strptime(line[:19], "%Y-%m-%d %H:%M:%S")       # guardian.recent_exec format
        self.assertIn(cid, lines[0])
        self.assertIn("rc=0", lines[1])

    def test_multiline_command_stays_on_one_line(self):
        bridge.audit(self.ws, "EXEC", "a\nb\r\nc")
        self.assertEqual(len(self.audit_lines()), 1)

    def test_rotation(self):
        saved = bridge.AUDIT_MAX_BYTES
        bridge.AUDIT_MAX_BYTES = 200
        try:
            for i in range(20):
                bridge.audit(self.ws, "EXEC", "x" * 50)
            self.assertTrue(os.path.exists(os.path.join(self.ws, "logs", "exec_audit.log.1")))
        finally:
            bridge.AUDIT_MAX_BYTES = saved


class GuardTests(Workspace):
    def _guard(self, body):
        os.makedirs(os.path.join(self.ws, "_coord"), exist_ok=True)
        path = os.path.join(self.ws, "_coord", "bridge_guard.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        stamp = time.time() + len(bridge._guard_cache) + 1
        os.utime(path, (stamp, stamp))

    def test_blocked_command_is_not_spawned_and_is_audited(self):
        self._guard("def check_command(command, root=None):\n"
                    "    return ('taskkill' not in command), 'protected: infra.tunnel'\n")
        before = len(self.tools.sessions)
        # A process name that cannot exist: the guard fails open by design, so a
        # real target here would kill the contributor's tunnel if loading broke.
        out = self.tools.run_command("taskkill /IM openbridge-guard-test-dummy.exe /F")
        self.assertIn("blocked_by_coord", out)
        self.assertIn("infra.tunnel", out)
        self.assertEqual(len(self.tools.sessions), before)
        self.assertTrue(any("[BLOCKED]" in l for l in self.audit_lines()))
        self.assertIn("ok", self.tools.run_command('%s -c "print(\'ok\')"' % PY, timeout_ms=20000))

    def test_guard_errors_fail_open_and_are_audited(self):
        self._guard("def check_command(command, root=None):\n    raise RuntimeError('boom')\n")
        out = self.tools.run_command('%s -c "print(7)"' % PY, timeout_ms=20000)
        self.assertIn("7", out)
        self.assertTrue(any("[GUARD-ERROR]" in l and "boom" in l for l in self.audit_lines()))

    def test_guard_is_hot_reloaded(self):
        self._guard("def check_command(command, root=None):\n    return False, 'v1'\n")
        self.assertEqual(bridge.coord_guard(self.ws, "x"), (False, "v1"))
        self._guard("def check_command(command, root=None):\n    return True, 'v2'\n")
        self.assertEqual(bridge.coord_guard(self.ws, "x"), (True, "v2"))

    def test_no_coord_dir_allows(self):
        self.assertEqual(bridge.coord_guard(self.ws, "taskkill /IM x.exe"), (True, "no _coord"))


class CharterTests(Workspace):
    def _charter(self, text, bump=1):
        os.makedirs(os.path.join(self.ws, "HANDOFF"), exist_ok=True)
        path = os.path.join(self.ws, "HANDOFF", "CHARTER.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        stamp = time.time() + bump
        os.utime(path, (stamp, stamp))

    def test_fallback_without_charter(self):
        self.assertEqual(bridge.load_instructions(self.ws), bridge.SERVER_INSTRUCTIONS)
        self.assertIn("stateless", bridge.SERVER_INSTRUCTIONS)

    def test_charter_is_used_and_reloaded(self):
        self._charter("# 章程 v1\n规则一", 1)
        self.assertIn("章程 v1", bridge.load_instructions(self.ws))
        self._charter("# 章程 v2\n规则二", 2)
        self.assertIn("章程 v2", bridge.load_instructions(self.ws))

    def test_empty_charter_falls_back_and_oversize_is_capped(self):
        self._charter("   \n", 1)
        self.assertEqual(bridge.load_instructions(self.ws), bridge.SERVER_INSTRUCTIONS)
        self._charter("字" * (bridge.CHARTER_MAX_CHARS + 500), 2)
        text = bridge.load_instructions(self.ws)
        self.assertLess(len(text), bridge.CHARTER_MAX_CHARS + 100)
        self.assertIn("已截断", text)

    def test_initialize_delivers_the_charter(self):
        self._charter("# 本机章程\n先 brief 再 claim", 1)
        old = (McpHandler.tools, McpHandler.secret)
        McpHandler.tools = self.tools
        McpHandler.secret = secrets.token_hex(16)
        server = ThreadedHTTPServer(("127.0.0.1", 0), McpHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d/mcp/%s" % (server.server_port, McpHandler.secret)
            body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "t", "version": "1"}}}
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
                "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.load(resp)["result"]
            self.assertIn("先 brief 再 claim", result["instructions"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            McpHandler.tools, McpHandler.secret = old


class PromptTests(unittest.TestCase):
    def test_prompt_is_short_and_defers_rules_to_instructions(self):
        prompt = bridge.DEFAULT_PROMPT_TEMPLATE.format(url="https://x.test/mcp/s", workspace="C:/w")
        self.assertLess(len(prompt), 900)
        self.assertIn("instructions", prompt)
        self.assertIn("1033", prompt)
        self.assertIn("capabilities {}", prompt)


if __name__ == "__main__":
    unittest.main()
