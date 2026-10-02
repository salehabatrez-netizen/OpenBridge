import unittest

from floating_status import (classify, render_text, _fmt_duration, PRESENTATION,
                             STATE_OFFLINE, STATE_PAUSED, STATE_RUNNING, STATE_LONG,
                             STATE_IDLE, STATE_QUIET, STATE_STALLED,
                             QUIET_AFTER, STALLED_AFTER, LONG_ACTION_AFTER)


def snap(**kw):
    base = {'paused': False, 'busy': False, 'current': None, 'elapsed': 0.0,
            'pending': 0, 'last': None, 'counters': {'ok': 0, 'error': 0}}
    base.update(kw)
    return base


class ClassifyTests(unittest.TestCase):
    def test_offline_when_not_running(self):
        v = classify(snap(), 0, running=False)
        self.assertEqual(v['state'], STATE_OFFLINE)

    def test_offline_when_snapshot_missing(self):
        v = classify(None, 0, running=True)
        self.assertEqual(v['state'], STATE_OFFLINE)

    def test_paused_beats_everything(self):
        # Even mid-action, a local pause is the headline the user needs.
        v = classify(snap(paused=True, busy=True, current='desktop/Click'), 0)
        self.assertEqual(v['state'], STATE_PAUSED)

    def test_running_shows_action_label(self):
        v = classify(snap(busy=True, current='desktop/Screenshot', elapsed=3.0), 0)
        self.assertEqual(v['state'], STATE_RUNNING)
        self.assertIn('desktop/Screenshot', v['headline'])
        self.assertIn('3 秒', v['detail'])

    def test_running_reports_queue_depth(self):
        v = classify(snap(busy=True, current='a', elapsed=1.0, pending=3), 0)
        self.assertIn('+2 排队', v['detail'])

    def test_long_action_is_flagged_not_called_stalled(self):
        # A slow action is still progress; it must not read as "stuck".
        v = classify(snap(busy=True, current='blender/Render',
                          elapsed=LONG_ACTION_AFTER + 5), 0)
        self.assertEqual(v['state'], STATE_LONG)
        self.assertIn('建议查看', v['detail'])

    def test_busy_action_never_classified_stalled_despite_old_age(self):
        # Regression: age is irrelevant while something is actively running.
        v = classify(snap(busy=True, current='x', elapsed=2.0), STALLED_AFTER * 5)
        self.assertEqual(v['state'], STATE_RUNNING)

    def test_recent_activity_is_idle(self):
        v = classify(snap(), 5.0)
        self.assertEqual(v['state'], STATE_IDLE)

    def test_quiet_threshold(self):
        v = classify(snap(), QUIET_AFTER + 1)
        self.assertEqual(v['state'], STATE_QUIET)

    def test_stalled_threshold(self):
        v = classify(snap(), STALLED_AFTER + 1)
        self.assertEqual(v['state'], STATE_STALLED)
        self.assertIn('疑似卡住', v['headline'])

    def test_boundary_exactly_at_stalled(self):
        self.assertEqual(classify(snap(), STALLED_AFTER)['state'], STATE_STALLED)

    def test_boundary_just_below_quiet(self):
        self.assertEqual(classify(snap(), QUIET_AFTER - 0.01)['state'], STATE_IDLE)

    def test_unknown_age_falls_back_to_idle(self):
        v = classify(snap(last={'label': 'desktop/Click', 'ok': True}), None)
        self.assertEqual(v['state'], STATE_IDLE)
        self.assertIn('desktop/Click', v['detail'])

    def test_counters_surface_in_detail(self):
        v = classify(snap(counters={'ok': 7, 'error': 2}), QUIET_AFTER + 1)
        self.assertIn('成功 7', v['detail'])
        self.assertIn('失败 2', v['detail'])

    def test_every_state_has_presentation(self):
        for state in (STATE_OFFLINE, STATE_PAUSED, STATE_RUNNING, STATE_LONG,
                      STATE_IDLE, STATE_QUIET, STATE_STALLED):
            self.assertIn(state, PRESENTATION)
            colour, glyph, short = PRESENTATION[state]
            self.assertTrue(colour.startswith('#'))
            self.assertTrue(glyph and short)

    def test_render_text_returns_colour(self):
        text, colour, short = render_text(classify(snap(), STALLED_AFTER + 1))
        self.assertEqual(colour, PRESENTATION[STATE_STALLED][0])
        self.assertIn('疑似卡住', text)

    def test_stalled_colour_is_red(self):
        self.assertEqual(PRESENTATION[STATE_STALLED][0], '#ff6b6b')


class DurationTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(_fmt_duration(5), '5 秒')

    def test_minutes(self):
        self.assertEqual(_fmt_duration(125), '2 分 05 秒')

    def test_hours(self):
        self.assertEqual(_fmt_duration(3725), '1 小时 02 分')

    def test_negative_clamped(self):
        self.assertEqual(_fmt_duration(-10), '0 秒')


class ImportSafetyTests(unittest.TestCase):
    def test_module_imports_without_display(self):
        # The Tk import must be lazy or headless machines cannot even import.
        import floating_status
        self.assertTrue(hasattr(floating_status, 'FloatingStatusWindow'))


if __name__ == '__main__':
    unittest.main()
