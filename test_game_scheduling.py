"""Deterministic timing and cancellation regressions; never send OS input."""
import base64
import contextlib
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from game_control.engine import Controller, ControlError, INPUT_TICK_MS, timing_summary
from game_control.schema import RUNTIME_INFO, SCHEMA_REVISION
from game_control.windows import WindowsBackend
from game_control.worker import call_game_tool
from test_game_control import FakeBackend


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.wake_delay = .002
        self.waits = []

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += max(0, seconds) + (self.wake_delay if seconds > 0 else 0)
        return False


class TimedBackend(FakeBackend):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.costs = False
        self.timed_events = []

    def window(self, hwnd):
        if self.costs:
            self.clock.now += .003
        return super().window(hwnd)

    def input(self, kind, value, down):
        self.timed_events.append((self.clock(), kind, value, down))
        super().input(kind, value, down)
        if self.costs:
            self.clock.now += .004

    def relative(self, dx, dy):
        self.timed_events.append((self.clock(), 'relative', dx, dy))
        super().relative(dx, dy)
        if self.costs:
            self.clock.now += .002


def approved(backend, **options):
    controller = Controller(backend, watchdog=False, **options)
    request = controller.request(1)['request_id']
    controller.approve(request, True)
    lease = controller.status(request)['lease_id']
    return controller, lease


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.backend = TimedBackend(self.clock)
        self.controller, self.lease = approved(self.backend, clock=self.clock)
        self.frame = self.controller.observe(self.lease)['metadata']['frame_id']
        self.waiter = patch.object(self.controller.cancel, 'wait', side_effect=self.clock.wait)
        self.waiter.start()

    def tearDown(self):
        self.waiter.stop()
        self.controller.close()

    def act(self, steps):
        return self.controller.act(self.lease, self.frame, 'timing-test', steps)

    def test_absolute_timeline_does_not_accumulate_step_overhead(self):
        self.backend.costs = True
        result = self.act([{'keys': ['W'], 'duration_ms': 100, 'dx': 30}] * 8)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['completed_steps'], 8)
        self.assertLessEqual(result['elapsed_ms'], 820)
        self.assertLessEqual(result['timings']['execution_overshoot_ms'], 10)
        self.assertEqual(sum(e[1] for e in self.backend.events if e[0] == 'relative'), 240)
        self.assertEqual(self.backend.events.count(('key', 'W', True)), 1)
        self.assertEqual(self.backend.events.count(('key', 'W', False)), 1)
        self.assertEqual(result['timings']['requested_duration_ms'], 800)
        self.assertGreater(result['timings']['window_check']['p95_ms'], 0)

    def test_zero_jitter_clock_cannot_spin_on_float_tick_boundary(self):
        self.clock.wake_delay = 0
        original_wait = self.clock.wait
        def checked_wait(seconds):
            if seconds <= 0:
                raise RuntimeError('Non-advancing tick deadline')
            return original_wait(seconds)
        with patch.object(self.controller.cancel, 'wait', side_effect=checked_wait):
            result = self.act([{'duration_ms': 1000}])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['elapsed_ms'], 1000)
        self.assertLessEqual(result['timings']['tick_lateness']['samples'], 102)

    def test_relative_motion_is_exact_despite_late_wakes(self):
        self.clock.wake_delay = .006
        result = self.act([{'duration_ms': 127, 'dx': -137, 'dy': 83}])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(sum(e[1] for e in self.backend.events if e[0] == 'relative'), -137)
        self.assertEqual(sum(e[2] for e in self.backend.events if e[0] == 'relative'), 83)
        self.assertLessEqual(result['timings']['tick_lateness']['samples'], 15)
        self.assertGreater(result['timings']['tick_lateness']['p95_ms'], 0)

    def test_missed_ticks_are_not_burst_replayed(self):
        self.clock.wake_delay = .024
        result = self.act([{'duration_ms': 120, 'dx': 120}])
        self.assertEqual(result['state'], 'completed')
        self.assertGreater(result['timings']['missed_ticks'], 0)
        self.assertLess(result['timings']['tick_lateness']['samples'], 9)
        self.assertEqual(sum(e[1] for e in self.backend.events if e[0] == 'relative'), 120)

    def test_entirely_missed_next_step_aborts_without_pressing_it(self):
        self.clock.wake_delay = .080
        result = self.act([{'duration_ms': 20, 'keys': ['W']},
                           {'duration_ms': 20, 'keys': ['SPACE']}])
        self.assertEqual(result['error_code'], 'SCHEDULE_LATE')
        self.assertEqual(result['completed_steps'], 1)
        self.assertNotIn(('key', 'SPACE', True), self.backend.events)
        self.assertIn(('key', 'W', False), self.backend.events)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertTrue(result['do_not_replay'])

    def test_deadline_overrun_still_releases(self):
        self.clock.wake_delay = .400
        result = self.act([{'duration_ms': 20, 'keys': ['W']}])
        self.assertEqual(result['error_code'], 'DEADLINE')
        self.assertFalse(self.controller.held_keys)
        self.assertTrue(result['cleanup_confirmed'])

    def test_modifier_pressed_first_released_last(self):
        result = self.act([{'duration_ms': 50, 'keys': ['A', 'CTRL']}])
        self.assertEqual(result['state'], 'completed')
        keys = [e for e in self.backend.events if e[0] == 'key']
        self.assertEqual(keys, [('key', 'CTRL', True), ('key', 'A', True),
                               ('key', 'A', False), ('key', 'CTRL', False)])

    def test_empty_input_reports_no_first_input(self):
        result = self.act([{'duration_ms': 40}])
        self.assertEqual(result['state'], 'completed')
        self.assertIsNone(result['timings']['first_input_ms'])
        self.assertEqual(self.backend.events, [])
        self.assertEqual(result['timings']['input_tick_ms'], INPUT_TICK_MS)

    def test_deduplication_preserves_timing_without_new_events(self):
        steps = [{'duration_ms': 40, 'keys': ['W']}]
        first = self.act(steps)
        events = list(self.backend.events)
        again = self.act(steps)
        self.assertTrue(again['deduplicated'])
        self.assertEqual(first['timings'], again['timings'])
        self.assertEqual(self.backend.events, events)

    def test_percentiles_are_nearest_rank(self):
        stats = timing_summary([.001, .002, .003, .004])
        self.assertEqual(stats, {'samples': 4, 'p50_ms': 2., 'p95_ms': 4., 'max_ms': 4.})
        self.assertEqual(timing_summary([])['samples'], 0)


class ConcurrentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.controller, self.lease = approved(self.backend)

    def tearDown(self):
        self.controller.close()

    def test_status_window_query_does_not_block_release(self):
        entered, unblock, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.backend.window
        def slow(hwnd):
            entered.set()
            unblock.wait(2)
            return original(hwnd)
        replies = []
        with patch.object(self.backend, 'window', side_effect=slow):
            reader = threading.Thread(target=lambda: replies.append(self.controller.status()))
            reader.start()
            self.assertTrue(entered.wait(1))
            releaser = threading.Thread(target=lambda: (self.controller.release(self.lease), finished.set()))
            releaser.start()
            try:
                self.assertTrue(finished.wait(.5), 'Status query held the authorization lock')
            finally:
                unblock.set()
                reader.join(2)
                releaser.join(2)
        self.assertEqual(replies[0]['state'], 'idle')
        self.assertFalse(replies[0]['target_ready'])

    def test_wrong_lease_cannot_release_current_controller(self):
        with self.assertRaises(ControlError):
            self.controller.release('not-the-current-lease')
        self.assertEqual(self.controller.binding['lease_id'], self.lease)
        self.assertFalse(self.controller.cancel.is_set())

    def test_delayed_expiry_does_not_revoke_replacement_lease(self):
        self.controller.release(self.lease)
        request = self.controller.request(1)['request_id']
        self.controller.approve(request, True)
        replacement = self.controller.status(request)['lease_id']
        result = self.controller.revoke('stale expiry', expected_lease=self.lease)
        self.assertEqual(result['state'], 'unchanged')
        self.assertEqual(self.controller.binding['lease_id'], replacement)

    def test_new_request_refused_while_old_capture_finishes(self):
        self.controller.release(self.lease)
        self.controller.action_lock.acquire()
        try:
            with self.assertRaises(ControlError) as error:
                self.controller.request(1)
            self.assertEqual(error.exception.code, 'BUSY')
        finally:
            self.controller.action_lock.release()

    def test_new_lease_blocked_until_input_cleanup_finishes(self):
        entered, unblock = threading.Event(), threading.Event()
        self.controller.held_keys.add('W')
        original = self.backend.input
        def slow_release(kind, value, down):
            if not down:
                entered.set()
                unblock.wait(2)
            return original(kind, value, down)
        errors = []
        def release():
            try: self.controller.release(self.lease)
            except Exception as exc: errors.append(exc)
        with patch.object(self.backend, 'input', side_effect=slow_release):
            thread = threading.Thread(target=release)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.controller.status()['state'], 'releasing')
                with self.assertRaises(ControlError) as error:
                    self.controller.request(1)
                self.assertEqual(error.exception.code, 'BUSY')
            finally:
                unblock.set()
                thread.join(2)
        self.assertEqual(errors, [])
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.controller.status()['state'], 'idle')

    def test_release_reports_elapsed_cleanup_not_action_age(self):
        self.controller.held_keys.add('W')
        result = self.controller.release(self.lease)
        self.assertIn('release_elapsed_ms', result)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(self.controller.status()['last_release_ms'], result['release_elapsed_ms'])
        self.assertFalse(self.controller.held_keys)

    def test_runtime_metadata_exposes_actual_schema(self):
        result = call_game_tool(self.controller, 'GameControlStatus', {})
        import json
        data = json.loads(result['content'][0]['text'])
        self.assertEqual(data['runtime']['schema_revision'], SCHEMA_REVISION)
        self.assertTrue(data['runtime']['concurrent_control'])
        self.assertEqual(data['runtime']['max_batch_ms'], data['max_batch_ms'])
        self.assertEqual(RUNTIME_INFO['protocol_revision'], 2)


class CaptureTimingTests(unittest.TestCase):
    def test_capture_phase_metrics_without_real_screen_capture(self):
        class Image:
            width, height = 640, 480
            size = (640, 480)
            def thumbnail(self, bounds, resample):
                self.width, self.height = 320, 240
            def convert(self, mode): return self
            def save(self, output, **kwargs): output.write(b'fake-png')
        fake_pillow = SimpleNamespace(ImageGrab=SimpleNamespace(grab=lambda **kwargs: Image()),
                                      Image=SimpleNamespace(Resampling=SimpleNamespace(LANCZOS=1)))
        backend = object.__new__(WindowsBackend)
        backend.dpi = contextlib.nullcontext
        with patch.dict('sys.modules', {'PIL': fake_pillow}), \
             patch('game_control.windows.time.perf_counter', side_effect=[0, .001, .003, .006, .010]):
            png, width, height, name = backend.capture([0, 0, 640, 480], 320)
        self.assertEqual(base64.b64decode(png), b'fake-png')
        self.assertEqual((width, height), (320, 240))
        self.assertEqual(backend.last_capture_metrics, {
            'grab_ms': 1., 'resize_ms': 2., 'png_encode_ms': 3., 'base64_ms': 4.,
            'total_ms': 10., 'png_bytes': 8, 'base64_bytes': 12})

    def test_capture_metrics_are_copied_into_observation(self):
        backend = FakeBackend()
        backend.last_capture_metrics = {'grab_ms': 2.5, 'png_bytes': 24}
        controller, lease = approved(backend)
        try:
            frame = controller.observe(lease)['metadata']
            self.assertEqual(frame['capture_metrics']['grab_ms'], 2.5)
            backend.last_capture_metrics['grab_ms'] = 99
            self.assertEqual(frame['capture_metrics']['grab_ms'], 2.5)
        finally:
            controller.close()


if __name__ == '__main__':
    unittest.main()
