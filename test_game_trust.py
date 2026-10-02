"""Offline tests for the local trust policy: no Win32 input, no real windows."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from game_control import trust
from game_control.engine import Controller, ControlError
from game_control.schema import NAMES, TOOLS
from test_game_control import FakeBackend


MC = {'window_id': 7, 'pid': 4242, 'identity': '7:4242:1:GLFW30', 'title': 'Minecraft 1.21.1', 'class': 'GLFW30', 'rect': [0, 0, 640, 480]}
RULES = [{'name': 'Minecraft Java', 'process': ['javaw.exe'], 'title_prefix': ['Minecraft'], 'class': ['GLFW30']}]


class TrustMatchTests(unittest.TestCase):
    def test_match_requires_every_listed_criterion(self):
        self.assertTrue(trust.match(MC, RULES, exe='javaw.exe'))
        self.assertIsNone(trust.match(MC, RULES, exe='notepad.exe'))
        self.assertIsNone(trust.match(dict(MC, title='Not Minecraft'), RULES, exe='javaw.exe'))
        self.assertIsNone(trust.match(dict(MC, **{'class': 'Chrome_WidgetWin_1'}), RULES, exe='javaw.exe'))

    def test_empty_rule_never_matches(self):
        self.assertIsNone(trust.match(MC, [{'name': 'everything'}], exe='javaw.exe'))
        self.assertIsNone(trust.match(MC, [], exe='javaw.exe'))

    def test_policy_off_without_local_switch(self):
        with patch.dict(os.environ, {'OPENBRIDGE_GAME_TRUST': '0'}):
            self.assertIsNone(trust.policy())
        with patch.dict(os.environ, {'OPENBRIDGE_GAME_TRUST': '1'}):
            self.assertTrue(callable(trust.policy()))

    def test_rules_file_roundtrip_and_bad_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'OPENBRIDGE_GAME_TRUST_DIR': folder}):
                self.assertEqual(trust.load_rules(), [])
                path = trust.ensure_default()
                self.assertEqual(trust.load_rules()[0]['name'], 'Minecraft Java')
                with open(path, 'w', encoding='utf-8') as handle:
                    handle.write('{not json')
                self.assertEqual(trust.load_rules(), [])

    def test_no_approval_tool_is_exposed(self):
        self.assertEqual(len(TOOLS), 8)
        self.assertFalse(any('approve' in name.lower() or 'trust' in name.lower() for name in NAMES))


class ControllerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.backend.info = dict(MC)
        self.backend.active = 7
        self.notes = []

    def tearDown(self):
        self.c.close()

    def test_default_controller_still_prompts(self):
        self.c = Controller(self.backend, notify=lambda m, p: self.notes.append(m), watchdog=False)
        reply = self.c.request(7)
        self.assertEqual(reply['state'], 'awaiting_local_approval')
        self.assertNotIn('lease_id', reply)
        self.assertIn('control/consent', self.notes)

    def test_matching_policy_issues_lease_without_prompt(self):
        self.c = Controller(self.backend, notify=lambda m, p: self.notes.append(m), watchdog=False,
                            policy=lambda info: trust.match(info, RULES, exe='javaw.exe'))
        reply = self.c.request(7)
        self.assertEqual(reply['state'], 'approved')
        self.assertEqual(reply['approved_by'], 'local_trust_policy')
        self.assertNotIn('control/consent', self.notes)
        self.assertIn('control/auto_approved', self.notes)
        lease = reply['lease_id']
        self.assertEqual(self.c.status(reply['request_id'])['lease_id'], lease)
        frame = self.c.observe(lease)['metadata']['frame_id']
        self.assertEqual(self.c.act(lease, frame, 'a1', [{'keys': ['W'], 'duration_ms': 20}])['state'], 'completed')

    def test_non_matching_policy_falls_back_to_prompt(self):
        self.c = Controller(self.backend, notify=lambda m, p: self.notes.append(m), watchdog=False,
                            policy=lambda info: trust.match(info, RULES, exe='chrome.exe'))
        reply = self.c.request(7)
        self.assertEqual(reply['state'], 'awaiting_local_approval')
        self.assertIn('control/consent', self.notes)

    def test_policy_exception_falls_back_to_prompt(self):
        def boom(info):
            raise RuntimeError('policy broken')
        self.c = Controller(self.backend, notify=lambda m, p: self.notes.append(m), watchdog=False, policy=boom)
        reply = self.c.request(7)
        self.assertEqual(reply['state'], 'awaiting_local_approval')
        self.assertIn('control/policy_error', self.notes)

    def test_auto_lease_keeps_limits_and_revocation(self):
        self.c = Controller(self.backend, watchdog=False, policy=lambda info: {'name': 'x'})
        lease = self.c.request(7)['lease_id']
        self.assertTrue(self.c.binding)
        self.c.binding['expires_at'] -= 601
        with self.assertRaises(ControlError) as error:
            self.c.observe(lease)
        self.assertEqual(error.exception.code, 'EXPIRED')

    def test_auto_lease_respects_busy(self):
        self.c = Controller(self.backend, watchdog=False, policy=lambda info: {'name': 'x'})
        self.c.request(7)
        with self.assertRaises(ControlError) as error:
            self.c.request(7)
        self.assertEqual(error.exception.code, 'BUSY')


class ManagerTrustTests(unittest.TestCase):
    def test_flag_reaches_broker_environment(self):
        from computer_use import ComputerUseManager
        from unittest.mock import Mock
        m = ComputerUseManager(desktop=True)
        captured = {}
        class FakeChild:
            def __init__(self, command, cwd, env, consent, log):
                captured['env'] = env
                self.closed = Mock(); self.closed.is_set.return_value = False
            def start(self): pass
            def close(self): pass
        with patch('computer_use.GameClient', FakeChild):
            m.game_trust = True
            m._game_client()
        self.assertEqual(captured['env'].get('OPENBRIDGE_GAME_TRUST'), '1')

    def test_set_game_trust_restarts_broker_and_revokes(self):
        from computer_use import ComputerUseManager
        from unittest.mock import Mock
        m = ComputerUseManager(desktop=True)
        child = Mock(); m.game_child = child
        m.set_game_trust(True)
        self.assertEqual([c[0] for c in child.method_calls], ['revoke', 'close'])
        self.assertIsNone(m.game_child)
        self.assertTrue(m.game_trust)


if __name__ == '__main__':
    unittest.main()
