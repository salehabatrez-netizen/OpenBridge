"""ui_kit 回归测试：字体只用真实字重、动画按时间推进、开关滑块不越出底槽、分段控件、按钮动效。

Tk 用例只创建一个隐藏（withdraw）的根窗口，屏幕上不会出现任何窗口；
没有 Tk / 显示环境时自动跳过。"""

import os
import time
import types
import unittest
from unittest import mock

import ui_kit
from ui_kit import Anim, pick_font_stack


WIN_FAMILIES = {'Microsoft YaHei UI', 'Microsoft YaHei UI Light', 'Noto Sans SC', 'Noto Sans SC Medium',
                'MiSans', 'Segoe UI', 'Segoe UI Variable Display Semib', 'Consolas'}


class FontStackTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'OPENBRIDGE_FONT': ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_body_uses_hinted_yahei_and_buttons_real_medium(self):
        regular, medium, bold = pick_font_stack(WIN_FAMILIES)
        self.assertEqual(regular, 'Microsoft YaHei UI')
        self.assertEqual(medium, ('Noto Sans SC Medium', 'normal'))
        self.assertEqual(bold, ('Microsoft YaHei UI', 'bold'))

    def test_regular_only_family_is_never_synthesised_bold(self):
        # 旧问题：MiSans 只装了 Regular，按钮用 weight=bold，被 Windows 描粗后中文糊成一团
        regular, medium, bold = pick_font_stack({'MiSans', 'Microsoft YaHei UI'}, prefer='MiSans')
        self.assertEqual(regular, 'MiSans')
        self.assertNotIn(('MiSans', 'bold'), (medium, bold))

    def test_without_medium_font_uses_true_bold(self):
        self.assertEqual(pick_font_stack({'Microsoft YaHei UI'}),
                         ('Microsoft YaHei UI', ('Microsoft YaHei UI', 'bold'), ('Microsoft YaHei UI', 'bold')))

    def test_environment_override(self):
        with mock.patch.dict(os.environ, {'OPENBRIDGE_FONT': 'Noto Sans SC'}):
            regular, medium, _bold = pick_font_stack(WIN_FAMILIES)
        self.assertEqual(regular, 'Noto Sans SC')
        self.assertEqual(medium, ('Noto Sans SC Medium', 'normal'))

    def test_unknown_system_falls_back_to_tk_default(self):
        self.assertEqual(pick_font_stack({'DejaVu Sans'})[0], 'TkDefaultFont')

    def test_bold_weight_only_for_families_with_real_bold_files(self):
        for families in (WIN_FAMILIES, {'MiSans'}, {'Noto Sans SC'}, {'Noto Sans SC', 'Noto Sans SC Medium'},
                         set()):
            for prefer in (None, 'MiSans', 'Noto Sans SC'):
                _regular, medium, bold = pick_font_stack(families, prefer=prefer)
                for family, weight in (medium, bold):
                    if weight == 'bold':
                        self.assertIn(family, ui_kit.TRUE_BOLD_FONTS + ('TkDefaultFont',),
                                      (families, prefer, medium, bold))


class _FakeWidget:
    def __init__(self):
        self.jobs = {}
        self.seq = 0

    def after(self, _ms, fn):
        self.seq += 1
        self.jobs[self.seq] = fn
        return self.seq

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def winfo_exists(self):
        return True

    def step(self):
        job = min(self.jobs)
        self.jobs.pop(job)()


class AnimTests(unittest.TestCase):
    def test_time_based_progress_reaches_exact_target(self):
        clock = [100.0]
        widget = _FakeWidget()
        with mock.patch.object(ui_kit, 'MOTION', True), mock.patch.object(ui_kit, 'MOTION_SCALE', 1.0), \
                mock.patch('ui_kit.time.monotonic', lambda: clock[0]):
            anim = Anim(widget, lambda: None, value=0.0)
            anim.to(10.0, 0.2)
            clock[0] += 0.05
            widget.step()
            self.assertTrue(0.0 < anim.value < 10.0)
            self.assertTrue(anim.running)
            clock[0] += 1.0          # 掉帧：时长不变，直接到终点
            widget.step()
        self.assertEqual(anim.value, 10.0)
        self.assertFalse(anim.running)

    def test_reduced_motion_is_instant(self):
        widget, seen = _FakeWidget(), []
        with mock.patch.object(ui_kit, 'MOTION', False):
            anim = Anim(widget, lambda: seen.append(anim.value), value=0.0)
            anim.to(1.0, 0.3)
        self.assertEqual(anim.value, 1.0)
        self.assertEqual(seen, [1.0])
        self.assertFalse(widget.jobs)


class TkCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.tk = tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as exc:  # 无显示环境
            raise unittest.SkipTest('Tk 不可用: %s' % exc)
        cls.theme = ui_kit.Theme(cls.root, dark=True)

    @classmethod
    def tearDownClass(cls):
        cls.root.update_idletasks()      # 先处理掉排队的 ttk 主题事件，避免销毁后 Tcl 报错
        cls.root.destroy()

    def setUp(self):
        for name, value in (('MOTION', True), ('MOTION_SCALE', 1.0)):
            patcher = mock.patch.object(ui_kit, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.005)


def _outside_pill(x, y, w, h, margin):
    """像素中心是否在胶囊形（半径 h/2）之外超过 margin 像素。"""
    r = h / 2.0
    px, py = x + 0.5, y + 0.5
    cx = min(max(px, r), w - r)
    return ((px - cx) ** 2 + (py - r) ** 2) ** 0.5 - r > margin


class SwitchTests(TkCase):
    def test_knob_and_track_never_paint_outside_the_pill(self):
        # 回归：旧版滑块是方形贴图，打开时右端露出绿色直角，看起来「绿色按钮右边超出」
        if ui_kit.Image is None:
            self.skipTest('需要 Pillow')
        for dark in (True, False):
            theme = ui_kit.Theme(self.root, dark=dark)
            var = self.tk.BooleanVar(master=self.root, value=True)
            switch = ui_kit.Switch(self.root, theme, var, surface=theme.c['card'])
            surface = ui_kit._rgb(theme.c['card'])
            for pos in (0.0, 0.25, 0.5, 0.75, 1.0):
                for stretch in (0.0, 1.0):
                    image = switch.render(pos, stretch)
                    w, h = image.size
                    for y in range(h):
                        for x in range(w):
                            if _outside_pill(x, y, w, h, 1.0):
                                got = image.getpixel((x, y))
                                self.assertTrue(all(abs(a - b) <= 12 for a, b in zip(got, surface)),
                                                (dark, pos, stretch, x, y, got, surface))
            switch.destroy()

    def test_variable_change_animates_to_end(self):
        var = self.tk.BooleanVar(master=self.root, value=False)
        switch = ui_kit.Switch(self.root, self.theme, var)
        var.set(True)
        self.pump(0.05)
        self.assertTrue(0.0 < switch.position < 1.0)
        self.pump(0.6)
        self.assertEqual(switch.position, 1.0)
        switch.destroy()


class SegmentedTests(TkCase):
    def test_click_moves_thumb_and_calls_command(self):
        calls = []
        seg = ui_kit.Segmented(self.root, self.theme, ('连接提示词', '运行日志'), command=calls.append)
        seg._on_click(types.SimpleNamespace(x=seg._inset + seg._seg_w + 5, y=5))
        self.assertEqual(calls, [1])
        self.assertEqual(seg.index, 1)
        samples = []
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            self.root.update()
            samples.append(seg.position)
            time.sleep(0.005)
        self.assertTrue(any(0.0 < s < 1.0 for s in samples), '应有中间帧')
        self.assertTrue(all(0.0 <= s <= 1.0 for s in samples), '滑块不得越出底槽')
        self.assertEqual(seg.position, 1.0)
        seg.destroy()

    def test_click_outside_segments_is_ignored(self):
        calls = []
        seg = ui_kit.Segmented(self.root, self.theme, ('A', 'B'), command=calls.append)
        seg._on_click(types.SimpleNamespace(x=-3, y=5))
        seg._on_click(types.SimpleNamespace(x=seg._total_w + 3, y=5))
        self.assertEqual(calls, [])
        seg.destroy()

    def test_select_without_animation(self):
        seg = ui_kit.Segmented(self.root, self.theme, ('A', 'B', 'C'))
        seg.select(2, animate=False)
        self.assertEqual(seg.position, 2.0)
        seg.select(9, animate=False)          # 越界被夹住
        self.assertEqual(seg.index, 2)
        seg.destroy()


class PillButtonTests(TkCase):
    def test_flash_restores_text_and_variant(self):
        button = ui_kit.PillButton(self.root, self.theme, text='复制 URL', variant='secondary')
        button.flash('已复制 ✓', duration_ms=60)
        self.assertEqual(button.cget('text'), '已复制 ✓')
        self.assertEqual(button.cget('variant'), 'success')
        self.pump(0.3)
        self.assertEqual(button.cget('text'), '复制 URL')
        self.assertEqual(button.cget('variant'), 'secondary')
        button.destroy()

    def test_press_insets_then_returns_to_rest(self):
        calls = []
        button = ui_kit.PillButton(self.root, self.theme, text='按钮', command=lambda: calls.append(1))
        button._on_press(None)
        self.pump(0.2)
        self.assertGreater(button._press_anim.value, 0.9)
        button._on_release(types.SimpleNamespace(x=-5, y=-5))   # 在按钮外松开：不触发
        self.pump(0.5)
        self.assertEqual(button._press_anim.value, 0.0)
        self.assertEqual(calls, [])
        button.destroy()

    def test_hidden_button_resizes_immediately(self):
        button = ui_kit.PillButton(self.root, self.theme, text='短')
        before = int(float(self.tk.Canvas.cget(button, 'width')))
        button.config(text='这是一个明显更长的按钮文字')
        after = int(float(self.tk.Canvas.cget(button, 'width')))
        self.assertGreater(after, before)
        self.assertFalse(button._width_anim.running)
        button.destroy()

    def test_pulse_on_hidden_window_does_not_redraw_or_fail(self):
        button = ui_kit.PillButton(self.root, self.theme, text='●  公网已验证', interactive=False)
        button.set_pulse('slow')
        self.pump(0.15)
        self.assertIsNotNone(button._pulse_job)
        button.set_pulse(None)
        button.destroy()


class SurfaceColorTests(TkCase):
    def test_named_parent_background_is_converted_for_pil(self):
        # Windows 上默认底色是 SystemButtonFace，PIL 不认识；放进默认底色容器也不能报错
        frame = self.tk.Frame(self.root, bg='gray85')
        button = ui_kit.PillButton(frame, self.theme, text='按钮')
        switch = ui_kit.Switch(frame, self.theme, self.tk.BooleanVar(master=self.root, value=True))
        self.assertEqual(button._surface, '#d9d9d9')
        self.assertEqual(switch._surface, '#d9d9d9')
        self.assertEqual(ui_kit._surface_color(frame, '#123456'), '#123456')
        frame.destroy()


class DotAndTextTests(TkCase):
    def test_dot_pulse_start_stop(self):
        dot = ui_kit.Dot(self.root, self.theme, '#30d158', halo=5)
        dot.set_pulse('fast')
        self.pump(0.1)
        dot.set_pulse(None)
        self.pump(0.6)
        self.assertIsNone(dot._pulse)
        dot.destroy()

    def test_fade_in_text_ends_at_original_colors(self):
        text = self.tk.Text(self.root, fg='#ffffff', bg='#000000')
        text.tag_configure('ok', foreground='#00ff00')
        ui_kit.fade_in_text(text, '#000000', duration=0.05)
        self.assertNotEqual(text.cget('foreground'), '#ffffff')
        self.pump(0.3)
        self.assertEqual(str(text.cget('foreground')), '#ffffff')
        self.assertEqual(str(text.tag_cget('ok', 'foreground')), '#00ff00')
        ui_kit.fade_in_text(text, '#000000', duration=0.05)   # 第二次仍以真实颜色为终点
        self.pump(0.3)
        self.assertEqual(str(text.cget('foreground')), '#ffffff')
        text.destroy()


if __name__ == '__main__':
    unittest.main()
