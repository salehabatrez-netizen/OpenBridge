"""Offline render/capture and continuous-batch regressions. Never inject real input."""
import json
import unittest
from unittest.mock import patch
from test_game_control import FakeBackend
from game_control.engine import Controller, ControlError, MAX_BATCH_MS, MAX_STEP_MS
from game_control.schema import TOOLS
from game_control.worker import call_game_tool


class FluencyTests(unittest.TestCase):
    def setUp(self):
        self.backend=FakeBackend()
        self.controller=Controller(self.backend,watchdog=False)
        self.request=self.controller.request(1)['request_id']
        self.controller.approve(self.request,True)
        self.lease=self.controller.status(self.request)['lease_id']

    def tearDown(self):
        self.controller.close()

    def arguments(self):
        return dict(lease_id=self.lease,frame_id=self.controller.observe(self.lease)['metadata']['frame_id'],
                    action_id='once',steps=[{'duration_ms':20,'keys':['W']}])

    def test_continuous_steps_only_one_key_down_up(self):
        args=self.arguments()
        args['steps']=[{'duration_ms':20,'keys':['W'],'buttons':['left']}]*3
        self.controller.act(**args)
        for kind,value in [('key','W'),('button','left')]:
            self.assertEqual(self.backend.events.count((kind,value,True)),1)
            self.assertEqual(self.backend.events.count((kind,value,False)),1)

    def test_long_batch_validation_and_schema_agree(self):
        frame=self.controller.observe(self.lease)['metadata']
        self.controller.validate([{'duration_ms':MAX_STEP_MS}]*2,frame)
        tool=next(t for t in TOOLS if t['name']=='GameAct')
        self.assertEqual(tool['inputSchema']['properties']['steps']['items']['properties']['duration_ms']['maximum'],MAX_STEP_MS)
        self.assertIn(str(MAX_BATCH_MS),tool['description'])

    def test_capture_options_and_metadata(self):
        args=self.arguments()
        args.update(observation_max_size=640,observation_settle_ms=20)
        with patch.object(self.backend,'capture',wraps=self.backend.capture) as capture:
            result=call_game_tool(self.controller,'GameAct',args)
        self.assertFalse(result['isError'])
        capture.assert_called_once_with(self.backend.info['rect'],640)
        metadata=json.loads(result['content'][0]['text'])['observation']
        self.assertEqual(metadata['settle_ms'],20)
        self.assertGreater(metadata['expires_in_ms'],4800)
        self.assertIn('capture_duration_ms',metadata)
        self.assertEqual(result['content'][1]['type'],'image')

    def test_default_render_settle(self):
        result=call_game_tool(self.controller,'GameAct',self.arguments())
        self.assertEqual(json.loads(result['content'][0]['text'])['observation']['settle_ms'],100)

    def test_invalid_options_never_send_input(self):
        args=self.arguments()
        for option,value in [('observation_max_size',100),('observation_settle_ms',1001),('observation_settle_ms',True)]:
            with self.assertRaises(ControlError):call_game_tool(self.controller,'GameAct',dict(args,**{option:value}))
        self.assertEqual(self.backend.events,[])

    def test_duplicate_neither_replays_nor_captures(self):
        args=self.arguments()
        call_game_tool(self.controller,'GameAct',args)
        events=list(self.backend.events)
        with patch.object(self.backend,'capture',wraps=self.backend.capture) as capture:
            result=call_game_tool(self.controller,'GameAct',args)
        capture.assert_not_called()
        self.assertTrue(json.loads(result['content'][0]['text'])['deduplicated'])
        self.assertEqual(events,self.backend.events)

    def test_capture_failure_keeps_action_receipt(self):
        args=self.arguments()
        with patch.object(self.backend,'capture',side_effect=OSError('capture failed')):
            result=call_game_tool(self.controller,'GameAct',args)
        data=json.loads(result['content'][0]['text'])
        self.assertTrue(result['isError'])
        self.assertEqual(data['state'],'completed')
        self.assertTrue(data['cleanup_confirmed'])
        self.assertTrue(data['do_not_replay'])
        self.assertEqual(self.controller.action_status(self.lease,'once')['state'],'completed')
        self.assertFalse(self.controller.held_keys)

    def test_revoke_during_settle_no_capture(self):
        with patch.object(self.controller.cancel,'wait',side_effect=lambda _:self.controller.revoke('test OFF')):
            with patch.object(self.backend,'capture',wraps=self.backend.capture) as capture:
                with self.assertRaises(ControlError):self.controller.observe(self.lease,settle_ms=50)
                capture.assert_not_called()
        self.assertFalse(self.controller.held_keys)

    def test_focus_loss_during_settle_no_capture(self):
        def lose_focus(_):self.backend.active=2
        with patch.object(self.controller.cancel,'wait',side_effect=lose_focus):
            with patch.object(self.backend,'capture',wraps=self.backend.capture) as capture:
                with self.assertRaises(ControlError) as exc:self.controller.observe(self.lease,settle_ms=50)
                self.assertEqual(exc.exception.code,'FOCUS_LOST')
                capture.assert_not_called()

    def test_remaining_budgets_and_absolute_cap(self):
        before=self.controller.status(self.request)
        self.assertEqual(before['actions_remaining'],1024)
        self.controller.act(**self.arguments())
        self.assertEqual(self.controller.status(self.request)['actions_remaining'],1023)
        self.controller.binding['created_at']=self.controller.clock()-3590
        self.controller.observe(self.lease)
        after=self.controller.status(self.request)
        self.assertLessEqual(after['lease_expires_in_seconds'],10)
        self.assertLessEqual(after['lease_absolute_remaining_seconds'],10)


if __name__=='__main__':unittest.main()
