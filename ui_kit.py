"""
ui_kit - Apple 风格的 Tk 小组件（OpenBridge GUI 使用）

纯 Tk + Pillow 实现，Pillow 缺失时自动退化为 Tk 原生图形：
- Theme       浅色 / 深色调色板（默认跟随 Windows 应用主题），字体与 DPI 缩放
- RoundBox    抗锯齿圆角容器（卡片、输入框底、分段控件底），内容放在 .body 中
- PillButton  胶囊按钮，兼容 tk.Button 常用接口：config(text/state/bg/fg/...)、cget、btn['text']
              悬停 / 按下 / 状态切换都有颜色过渡；文字以「●」开头时画成抗锯齿圆点，可呼吸
              按下时整体内缩；文字变化时宽度平滑过渡、新文字淡入
- Switch      iOS 风格开关，整体超采样绘制；滑块缓动、底色渐变、按住时滑块拉伸
- Segmented   分段控件，选中滑块在两段之间滑动，文字颜色随之过渡
- Dot         状态圆点，可选呼吸光环（set_pulse）

动效统一由 Anim 驱动（基于时间，约 60 fps）。Windows 关闭了「显示动画」或设置
OPENBRIDGE_REDUCED_MOTION=1 时全部瞬时完成；窗口最小化时呼吸类循环动画暂停重绘。

字体（Tk 在 Windows 上用 GDI 画字，清晰度取决于字体自带的 hinting）：
- 正文 / 说明 / 粗体标题用微软雅黑 UI：完整 hinting，96 DPI 下笔画贴合像素，小字号最清楚；
- 按钮等中等字重优先用真实的 Medium 字体（思源 / Noto Sans SC Medium 等），比雅黑粗体匀称；
- 绝不给只有 Regular 的字体加 bold：Windows 会把字形「描粗」，中文糊成一团（旧版 MiSans 的问题）。
  思源 / MiSans 没有 hinting，做正文时小字号发虚，所以不再用作正文。
"""

import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import OrderedDict

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except Exception:  # pragma: no cover - Pillow is optional
    Image = None


# ── 颜色工具 ────────────────────────────────────────────────────────────

def _rgb(color):
    color = color.lstrip('#')
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def blend(a, b, t):
    """a → b 线性混合，t=0 返回 a，t=1 返回 b。"""
    ra, rb = _rgb(a), _rgb(b)
    return '#%02x%02x%02x' % tuple(int(round(x + (y - x) * t)) for x, y in zip(ra, rb))


# ── 动效 ────────────────────────────────────────────────────────────────

def ease_out_cubic(t):
    return 1.0 - (1.0 - t) ** 3


def ease_in_out(t):
    return t * t * (3.0 - 2.0 * t)


def motion_enabled():
    """OPENBRIDGE_REDUCED_MOTION=1 或 Windows「显示动画」关闭时返回 False。"""
    if os.environ.get('OPENBRIDGE_REDUCED_MOTION', '').strip() in ('1', 'true', 'yes'):
        return False
    if sys.platform == 'win32':
        try:
            import ctypes
            value = ctypes.c_int(1)
            # SPI_GETCLIENTAREAANIMATION
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
                return bool(value.value)
        except Exception:
            pass
    return True


MOTION = motion_enabled()
try:  # 调试用：>1 放慢全部动效，便于逐帧检查
    MOTION_SCALE = max(0.05, float(os.environ.get('OPENBRIDGE_MOTION_SCALE', '1') or 1))
except ValueError:
    MOTION_SCALE = 1.0


class Anim:
    """把一个 0..1（或任意）数值按时间缓动到目标值，每帧回调 on_step()。

    基于 time.monotonic，掉帧时动画时长不变；控件销毁后自动停止。"""

    FRAME_MS = 15

    def __init__(self, widget, on_step, value=0.0):
        self.widget = widget
        self.on_step = on_step
        self.value = self.start = self.target = float(value)
        self.t0 = 0.0
        self.duration = 0.16
        self.ease = ease_out_cubic
        self.job = None

    @property
    def running(self):
        return self.job is not None

    def set(self, value):
        self.cancel()
        self.value = self.start = self.target = float(value)

    def to(self, target, duration=0.16, ease=ease_out_cubic, restart=False):
        target = float(target)
        if not restart and target == self.target and (self.job is not None or self.value == target):
            return
        self.start, self.target = self.value, target
        self.t0 = time.monotonic()
        self.duration = max(0.001, float(duration) * MOTION_SCALE)
        self.ease = ease
        if not MOTION or duration <= 0:
            self.cancel()
            self.value = target
            self.on_step()
            return
        if self.job is None:
            self._tick()

    def cancel(self):
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def _tick(self):
        self.job = None
        try:
            if not self.widget.winfo_exists():
                return
        except tk.TclError:
            return
        p = min(1.0, (time.monotonic() - self.t0) / self.duration)
        self.value = self.start + (self.target - self.start) * self.ease(p)
        try:
            self.on_step()
        except tk.TclError:
            return
        if p < 1.0:
            self.job = self.widget.after(self.FRAME_MS, self._tick)


def _surface_color(widget, color):
    """把 Tk 颜色（包括 Windows 默认底色 SystemButtonFace 这类系统色名）统一成 #rrggbb，供 PIL 使用。"""
    color = str(color)
    if len(color) == 7 and color.startswith('#'):
        return color
    try:
        r, g, b = widget.winfo_rgb(color)
    except tk.TclError:
        return color
    return '#%02x%02x%02x' % (r >> 8, g >> 8, b >> 8)


def _cancel_after(widget, job):
    """安全取消 after 任务（控件销毁后待执行的回调会在 Tcl 里报 invalid command）。"""
    if job is not None:
        try:
            widget.after_cancel(job)
        except tk.TclError:
            pass
    return None


# ── 调色板（取自 Apple Human Interface Guidelines 的系统色） ─────────────

LIGHT = dict(
    dark=False,
    bg='#f5f5f7', card='#ffffff', field='#f2f2f5', separator='#e5e5ea',
    text='#1d1d1f', muted='#6e6e73', tertiary='#aeaeb2',
    accent='#0071e3', accent_hover='#0077ed', accent_press='#0062c4', on_accent='#ffffff',
    secondary='#e8e8ed', secondary_hover='#dcdce1', secondary_press='#d1d1d6',
    green='#34c759', green_text='#248a3d', green_tint='#e4f6e8',
    orange='#ff9f0a', orange_text='#b25000', orange_tint='#fff2df',
    red='#ff3b30', red_text='#d70015', red_tint='#ffe9e7',
    blue_text='#0066cc', blue_tint='#e6f0fc',
    seg_bg='#e8e8ed', seg_on='#ffffff',
    switch_off='#e3e3e8', knob='#ffffff', knob_ring='#d8d8dd',
    select='#b3d7ff', toast='#1d1d1f', toast_text='#ffffff',
    log_tool='#0066cc', log_exec='#a05a00', log_success='#248a3d',
    log_warning='#d70015', log_time='#8e8e93', log_cu='#8944ab',
)

DARK = dict(
    dark=True,
    bg='#1c1c1e', card='#2c2c2e', field='#232325', separator='#3a3a3c',
    text='#f5f5f7', muted='#a1a1a6', tertiary='#636366',
    accent='#0a84ff', accent_hover='#2491ff', accent_press='#0071e3', on_accent='#ffffff',
    secondary='#3a3a3c', secondary_hover='#454548', secondary_press='#505053',
    green='#30d158', green_text='#32d74b', green_tint='#1e3a26',
    orange='#ff9f0a', orange_text='#ffb340', orange_tint='#3d2f17',
    red='#ff453a', red_text='#ff6961', red_tint='#422322',
    blue_text='#64b5ff', blue_tint='#1b2f48',
    seg_bg='#1c1c1e', seg_on='#48484a',
    switch_off='#48484a', knob='#ffffff', knob_ring='#48484a',
    select='#3f638b', toast='#e5e5ea', toast_text='#1c1c1e',
    log_tool='#64b5ff', log_exec='#ffb340', log_success='#32d74b',
    log_warning='#ff6961', log_time='#8e8e93', log_cu='#da8fff',
)


def system_prefers_dark():
    """OPENBRIDGE_THEME=light|dark 优先，否则跟随 Windows「应用模式」。"""
    forced = os.environ.get('OPENBRIDGE_THEME', '').strip().lower()
    if forced in ('light', 'dark'):
        return forced == 'dark'
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
        return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except Exception:
        return False


# 字体选择（见模块说明）。常规体只用 hinting 良好的字体；中等字重只用真实存在的 Medium 文件；
# 粗体只交给自带真实粗体文件的家族，避免 Windows 合成「描粗」。
REGULAR_FONTS = ('Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC')
MEDIUM_FONTS = ('Noto Sans SC Medium', 'Source Han Sans SC Medium', 'HarmonyOS Sans SC Medium',
                'MiSans Medium', 'MiSans Demibold')
TRUE_BOLD_FONTS = ('Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC')


def pick_font_stack(families, prefer=None):
    """返回 (常规家族, (中等家族, 字重), (粗体家族, 字重))，只用真实存在的字重。

    prefer（或环境变量 OPENBRIDGE_FONT）可指定常规体家族名，例如想要 MiSans 观感时。"""
    prefer = prefer or os.environ.get('OPENBRIDGE_FONT', '').strip() or None
    candidates = ((prefer,) if prefer else ()) + REGULAR_FONTS
    regular = next((name for name in candidates if name in families), None)
    if regular is None:
        # 非 Windows / 精简系统：交给 Tk 默认字体，粗体由平台字体系统负责
        return 'TkDefaultFont', ('TkDefaultFont', 'normal'), ('TkDefaultFont', 'bold')
    medium = next((name for name in (regular + ' Medium',) + MEDIUM_FONTS if name in families), None)
    if regular in TRUE_BOLD_FONTS:
        bold = (regular, 'bold')
    elif regular + ' Bold' in families:
        bold = (regular + ' Bold', 'normal')
    elif medium is not None:
        bold = (medium, 'normal')
    else:
        bold = (regular, 'normal')   # 宁可不加粗，也不让系统描粗
    return regular, ((medium, 'normal') if medium is not None else bold), bold


class Theme:
    IMAGE_CACHE_MAX = 3000

    def __init__(self, root, dark=None):
        self.root = root
        self.c = dict(DARK if (system_prefers_dark() if dark is None else dark) else LIGHT)
        self.dark = self.c['dark']
        try:
            self.scale = max(1.0, float(root.winfo_fpixels('1i')) / 96.0)
        except Exception:
            self.scale = 1.0
        families = set(tkfont.families(root))

        def pick(*names):
            for name in names:
                if name in families:
                    return name
            return names[-1]

        ui, (medium, medium_w), (bold, bold_w) = pick_font_stack(families)
        self.font_stack = (ui, medium, bold)
        display = pick('Segoe UI Variable Display Semib', 'Segoe UI Semibold', bold)
        display_w = 'bold' if display == bold and bold_w == 'bold' else 'normal'
        mono = pick('Cascadia Mono', 'Consolas', 'Courier New')
        self.f_display = (display, 21, display_w)
        self.f_section = (bold, 12, bold_w)
        self.f_body = (ui, 10)
        self.f_body_b = (medium, 10, medium_w)
        self.f_caption = (ui, 9)
        self.f_btn = (medium, 10, medium_w)
        self.f_btn_small = (medium, 9, medium_w)
        self.f_mono = (mono, 10)
        self.f_mono_small = (mono, 9)
        self._fonts = {}
        # 图片缓存有上限（LRU）。正在显示的图片由控件自己持有引用，被淘汰也不会消失。
        self._images = OrderedDict()

    # 尺寸与字体
    def px(self, value):
        return int(round(value * self.scale))

    def font(self, spec):
        key = tuple(spec)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(root=self.root, font=spec)
        return self._fonts[key]

    # 图片缓存
    def cache_get(self, key):
        hit = self._images.get(key)
        if hit is not None:
            self._images.move_to_end(key)
        return hit

    def cache_put(self, key, value):
        self._images[key] = value
        while len(self._images) > self.IMAGE_CACHE_MAX:
            self._images.popitem(last=False)
        return value

    # 按钮配色：返回 (底色, 文字, 悬停底色, 按下底色)
    def button_colors(self, variant, surface):
        c = self.c
        hover_mix = '#ffffff' if self.dark else '#000000'
        tinted = lambda fill, fg: (fill, fg, blend(fill, hover_mix, 0.08), blend(fill, hover_mix, 0.15))
        if variant == 'primary':
            return c['accent'], c['on_accent'], c['accent_hover'], c['accent_press']
        if variant == 'danger':
            return tinted(c['red_tint'], c['red_text'])
        if variant == 'success':
            return tinted(c['green_tint'], c['green_text'])
        if variant == 'warning':
            return tinted(c['orange_tint'], c['orange_text'])
        if variant == 'plain':
            return surface, c['accent'], blend(surface, c['accent'], 0.10), blend(surface, c['accent'], 0.18)
        if variant == 'segment':
            return surface, c['muted'], blend(surface, c['text'], 0.06), blend(surface, c['text'], 0.11)
        if variant == 'segment_on':
            return c['seg_on'], c['text'], c['seg_on'], c['seg_on']
        if variant == 'toast':
            return c['toast'], c['toast_text'], c['toast'], c['toast']
        return c['secondary'], c['text'], c['secondary_hover'], c['secondary_press']

    # 抗锯齿圆角素材（按参数缓存）
    def _circle(self, d, fill, surface, ring=None):
        if Image is None or d < 2:
            return None
        key = ('circle', d, fill, surface, ring)
        im = self.cache_get(key)
        if im is None:
            ss = 4
            big = Image.new('RGB', (d * ss, d * ss), surface)
            ImageDraw.Draw(big).ellipse([0, 0, d * ss - 1, d * ss - 1], fill=fill,
                                        outline=ring, width=ss if ring else 0)
            im = self.cache_put(key, big.resize((d, d), Image.LANCZOS))
        return im

    def circle_photo(self, d, fill, surface, ring=None):
        key = ('circle_photo', d, fill, surface, ring)
        photo = self.cache_get(key)
        if photo is None:
            im = self._circle(d, fill, surface, ring)
            if im is None:
                return None
            photo = self.cache_put(key, ImageTk.PhotoImage(im, master=self.root))
        return photo

    def corners(self, r, fill, surface, border=None):
        key = ('corners', r, fill, surface, border)
        photos = self.cache_get(key)
        if photos is None:
            im = self._circle(2 * r, fill, surface, border)
            if im is None:
                return None
            boxes = ((0, 0, r, r), (r, 0, 2 * r, r), (0, r, r, 2 * r), (r, r, 2 * r, 2 * r))
            photos = self.cache_put(key, [ImageTk.PhotoImage(im.crop(b), master=self.root) for b in boxes])
        return photos

    def halo_photo(self, size, extent, color, surface, phase):
        """圆点 + 向外扩散并淡出的光环（phase 0..1），超采样抗锯齿。"""
        if Image is None:
            return None
        phase = round(phase * 32) / 32.0
        key = ('halo', size, extent, color, surface, phase)
        photo = self.cache_get(key)
        if photo is None:
            ss = 4
            full = size + 2 * extent
            big = Image.new('RGB', (full * ss, full * ss), surface)
            draw = ImageDraw.Draw(big)
            cx = cy = full * ss / 2.0
            ring_r = (size / 2.0 + 0.5 + extent * ease_out_cubic(phase)) * ss
            ring_color = blend(color, surface, 0.25 + 0.75 * phase)
            glow_color = blend(color, surface, 0.80 + 0.20 * phase)
            if phase < 0.999:
                # 柔和的内晕 + 一圈细环，向外扩散并淡出
                draw.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r], fill=glow_color,
                             outline=ring_color, width=max(1, int(ss * 1.2)))
            r = size / 2.0 * ss
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
            im = big.resize((full, full), Image.LANCZOS)
            photo = self.cache_put(key, ImageTk.PhotoImage(im, master=self.root))
        return photo


def draw_round(canvas, theme, tag, w, h, r, fill, surface, border=None, x=0, y=0):
    """在 canvas 上画一个圆角矩形（四角贴图 + 中间矩形），旧图形按 tag 删除。

    返回本次用到的图片列表；调用方应保存它，保证图片在显示期间不被回收。"""
    canvas.delete(tag)
    w, h = int(w), int(h)
    if w < 2 or h < 2:
        return []
    r = int(max(0, min(r, h // 2, w // 2)))
    kw = dict(fill=fill, outline='', width=0, tags=tag)
    corner = theme.corners(r, fill, surface, border) if r >= 2 else None
    if corner is None:
        if r >= 2:  # Pillow 缺失：退化为 Tk 原生平滑多边形
            pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r, x + w, y + h,
                   x + w - r, y + h, x + r, y + h, x, y + h, x, y + h - r, x, y + r, x, y]
            canvas.create_polygon(pts, smooth=True, fill=fill, outline=border or '', tags=tag)
        else:
            canvas.create_rectangle(x, y, x + w, y + h, fill=fill, outline=border or '', tags=tag)
        return []
    canvas.create_rectangle(x + r, y, x + w - r, y + h, **kw)
    canvas.create_rectangle(x, y + r, x + w, y + h - r, **kw)
    tl, tr, bl, br = corner
    canvas.create_image(x, y, image=tl, anchor='nw', tags=tag)
    canvas.create_image(x + w - r, y, image=tr, anchor='nw', tags=tag)
    canvas.create_image(x, y + h - r, image=bl, anchor='nw', tags=tag)
    canvas.create_image(x + w - r, y + h - r, image=br, anchor='nw', tags=tag)
    if border:
        line = dict(fill=border, width=1, tags=tag)
        canvas.create_line(x + r, y, x + w - r, y, **line)
        canvas.create_line(x + r, y + h - 1, x + w - r, y + h - 1, **line)
        canvas.create_line(x, y + r, x, y + h - r, **line)
        canvas.create_line(x + w - 1, y + r, x + w - 1, y + h - r, **line)
    return list(corner)


class RoundBox(tk.Canvas):
    """圆角容器。子控件放进 self.body。

    expand=False：高度随内容变化（卡片）；expand=True：内容填满分配到的空间（日志区）。
    """

    def __init__(self, parent, theme, fill, radius=14, pad=(18, 16), border=None,
                 expand=False, surface=None, fit_width=False):
        self._t = theme
        self._fit = fit_width
        self._fill = fill
        self._surface = _surface_color(parent, surface or parent.cget('bg'))
        self._radius = theme.px(radius)
        self._padx, self._pady = theme.px(pad[0]), theme.px(pad[1])
        self._border = border
        self._expand = expand
        self._refs = []
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0,
                         width=10, height=10)
        self.body = tk.Frame(self, bg=fill)
        self._win = self.create_window(self._padx, self._pady, window=self.body, anchor='nw')
        self.bind('<Configure>', self._on_canvas)
        if not expand:
            # The body is unmapped (outside the 10px canvas) until we size the canvas,
            # so it would never get <Configure> on its own: sync once Tk is idle.
            self.body.bind('<Configure>', self._on_body, add='+')
            self.after_idle(self._on_body)

    def _on_body(self, _event=None):
        if not self.winfo_exists():
            return
        height = self.body.winfo_reqheight() + 2 * self._pady
        if int(float(self.cget('height'))) != height:
            tk.Canvas.configure(self, height=height)
        if self._fit:
            width = self.body.winfo_reqwidth() + 2 * self._padx
            if int(float(self.cget('width'))) != width:
                tk.Canvas.configure(self, width=width)

    def _on_canvas(self, event):
        self.itemconfigure(self._win, width=max(1, event.width - 2 * self._padx))
        if self._expand:
            self.itemconfigure(self._win, height=max(1, event.height - 2 * self._pady))
        self._refs = draw_round(self, self._t, 'bg', event.width, event.height, self._radius,
                                self._fill, self._surface, self._border)
        self.tag_lower('bg')


class Dot(tk.Canvas):
    """状态圆点。halo>0 时预留光环空间，set_pulse('slow'|'fast'|None) 控制呼吸。"""

    PERIODS = {'slow': 2.2, 'fast': 1.0}

    def __init__(self, parent, theme, color, size=8, surface=None, halo=0):
        self._t, self._size = theme, theme.px(size)
        self._extent = theme.px(halo)
        self._surface = _surface_color(parent, surface or parent.cget('bg'))
        self._color = color
        self._pulse = None
        self._t0 = time.monotonic()
        self._job = None
        self._ref = None
        self._item = None
        full = self._size + 2 * self._extent
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0,
                         width=full, height=full)
        self._render()

    def set_color(self, color):
        if color == self._color and self.find_all():
            return
        self._color = color
        self._render()

    def set_pulse(self, mode):
        mode = mode if (mode in self.PERIODS and self._extent > 0 and MOTION) else None
        if mode == self._pulse:
            return
        self._pulse = mode
        self._t0 = time.monotonic()
        if mode and self._job is None:
            self._loop()
        elif not mode:
            self._job = _cancel_after(self, self._job)
            self._render()

    def destroy(self):
        self._job = _cancel_after(self, self._job)
        super().destroy()

    def _phase(self):
        if not self._pulse:
            return 1.0
        return ((time.monotonic() - self._t0) / self.PERIODS[self._pulse]) % 1.0

    def _photo(self):
        e, s = self._extent, self._size
        if self._pulse:
            return self._t.halo_photo(s, e, self._color, self._surface, self._phase()), (0, 0)
        return self._t.circle_photo(s, self._color, self._surface), (e, e)

    def _render(self):
        self.delete('all')
        e, s = self._extent, self._size
        photo, (x, y) = self._photo()
        if photo is not None:
            self._item = self.create_image(x, y, image=photo, anchor='nw')
        else:
            self._item = None
            self.create_oval(e, e, e + s - 1, e + s - 1, fill=self._color, outline='')
        self._ref = photo

    def _loop(self):
        self._job = None
        try:
            if not self.winfo_exists() or not self._pulse:
                return
            if not self.winfo_viewable():          # 窗口最小化 / 被隐藏：不重绘，低频等待
                self._job = self.after(500, self._loop)
                return
        except tk.TclError:
            return
        photo, (x, y) = self._photo()
        if photo is not None and self._item is not None and self.find_all():
            if photo is not self._ref:             # 只换图，不重建图元
                self.itemconfigure(self._item, image=photo)
                self.coords(self._item, x, y)
                self._ref = photo
        else:
            self._render()
        self._job = self.after(40 if self._pulse == 'fast' else 60, self._loop)


class PillButton(tk.Canvas):
    """胶囊按钮。兼容 tk.Button 的常用读写方式，便于原有逻辑与测试继续使用。"""

    _OWN = ('text', 'command', 'variant', 'state', 'bg', 'background', 'fg', 'foreground',
            'activebackground', 'activeforeground', 'font', 'textvariable', 'disabledforeground',
            'relief', 'bd', 'padx', 'pady')
    LEAD_DOT = '\u25cf'
    PULSE_PERIODS = {'slow': 2.4, 'fast': 1.0}

    def __init__(self, parent, theme, text='', command=None, variant='secondary',
                 state='normal', font=None, padx=16, height=34, minwidth=0, radius=None,
                 surface=None, interactive=True, textvariable=None):
        self._t = theme
        self._text = text
        self._command = command
        self._variant = variant
        self._state = state
        self._font = font or theme.f_btn
        self._padx = theme.px(padx)
        self._h = theme.px(height)
        self._minw = theme.px(minwidth)
        self._minw_flash = 0
        self._radius = theme.px(radius) if radius is not None else self._h // 2
        self._surface = _surface_color(parent, surface or parent.cget('bg'))
        self._interactive = interactive
        self._override = {}
        self._hover = self._press = False
        self._textvar = None
        self._text_trace = None
        self._refs = []
        self._flash_job = None
        self._flash_saved = None
        self._pulse = None
        self._pulse_t0 = time.monotonic()
        self._pulse_job = None
        # 颜色过渡：从 _from 渐变到 _to，进度由 _anim 驱动
        self._from = self._to = None
        self._anim = Anim(self, self._draw, value=1.0)
        self._press_anim = Anim(self, self._draw, value=0.0)        # 按下内缩 0..1
        self._width_anim = Anim(self, self._apply_width, value=0.0)  # 文字变化时的宽度过渡
        self._width_from = 0
        self._dot_item = None
        self._dot_ref = None
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0,
                         cursor='hand2' if interactive else '', takefocus=0,
                         width=10, height=self._h)
        if textvariable is not None:
            self._bind_textvariable(textvariable)
        if interactive:
            self.bind('<Enter>', lambda e: self._set_hover(True))
            self.bind('<Leave>', lambda e: self._set_hover(False))
            self.bind('<ButtonPress-1>', self._on_press)
            self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Configure>', lambda e: self._draw())
        self._to = self._target()
        self._from = self._to
        self._resize()

    # 兼容 tk.Button 的接口 ---------------------------------------------
    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = dict(cnf, **kw)
        elif isinstance(cnf, str):
            return self.cget(cnf)
        if not kw:
            return tk.Canvas.configure(self)
        relayout = False
        for key in list(kw):
            if key not in self._OWN:
                continue
            value = kw.pop(key)
            if key == 'text':
                relayout = relayout or value != self._text
                self._text = value
            elif key == 'textvariable':
                self._bind_textvariable(value)
                relayout = True
            elif key == 'command':
                self._command = value
            elif key == 'variant':
                self._variant = value
                for k in ('fill', 'fg', 'hover'):
                    self._override.pop(k, None)
            elif key == 'state':
                self._state = str(value)
                if self._state == 'disabled':
                    self._hover = self._press = False
                    self._press_anim.to(0.0, 0.12)
            elif key in ('bg', 'background'):
                self._override['fill'] = value
            elif key in ('fg', 'foreground'):
                self._override['fg'] = value
            elif key == 'activebackground':
                self._override['hover'] = value
            elif key == 'font':
                self._font = value
                relayout = True
        if kw:
            tk.Canvas.configure(self, **kw)
        self.configure_cursor()
        if relayout:
            self._resize()
        self._retarget(0.22)

    config = configure

    def cget(self, key):
        if key == 'text':
            return self._text
        if key == 'state':
            return self._state
        if key == 'variant':
            return self._variant
        if key == 'command':
            return self._command
        if key in ('bg', 'background'):
            return self._colors()[0]
        if key in ('fg', 'foreground'):
            return self._colors()[1]
        return tk.Canvas.cget(self, key)

    __getitem__ = cget

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def invoke(self):
        if self._state != 'disabled' and self._command:
            return self._command()

    # 动效 API ----------------------------------------------------------
    def set_pulse(self, mode):
        """文字以「●」开头时让圆点呼吸：'slow'（稳定）/ 'fast'（进行中）/ None。"""
        mode = mode if (mode in self.PULSE_PERIODS and MOTION) else None
        if mode == self._pulse:
            return
        self._pulse = mode
        self._pulse_t0 = time.monotonic()
        if mode and self._pulse_job is None:
            self._pulse_loop()
        else:
            if not mode:
                self._pulse_job = _cancel_after(self, self._pulse_job)
            self._draw()

    def flash(self, text, variant='success', duration_ms=1400):
        """短暂换成反馈文字与配色（如「已复制 ✓」），宽度不缩，随后恢复。"""
        if self._flash_saved is None:
            self._flash_saved = (self._text, self._variant)
        else:
            self.after_cancel(self._flash_job)
        self._minw_flash = self.winfo_width() if self.winfo_width() > 1 else 0
        self.configure(text=text, variant=variant)

        def restore():
            self._flash_job = None
            saved, self._flash_saved = self._flash_saved, None
            self._minw_flash = 0
            if saved is not None:
                self.configure(text=saved[0], variant=saved[1])
        self._flash_job = self.after(duration_ms, restore)

    def destroy(self):
        self._pulse_job = _cancel_after(self, self._pulse_job)
        self._flash_job = _cancel_after(self, self._flash_job)
        for anim in (self._anim, self._press_anim, self._width_anim):
            anim.cancel()
        if self._textvar is not None and self._text_trace is not None:
            try:
                self._textvar.trace_remove('write', self._text_trace)
            except tk.TclError:
                pass
            self._text_trace = None
        super().destroy()

    def appear(self, show=True, duration=0.22, on_done=None):
        """从底色淡入（show=True）或淡出到底色（show=False）。"""
        fill, fg = self._target()
        clear = (self._surface, self._surface)
        if show:
            self._from, self._to = clear, (fill, fg)
        else:
            self._from, self._to = self._shown(), clear
        self._anim.set(0.0)
        self._anim.to(1.0, duration, ease_out_cubic, restart=True)
        if on_done is not None:
            self.after(int(duration * 1000) + 30, on_done)

    # 内部 --------------------------------------------------------------
    def _bind_textvariable(self, var):
        self._textvar = var
        self._text = var.get()

        def changed(*_):
            self._text = var.get()
            self._resize()
        self._text_trace = var.trace_add('write', changed)

    def configure_cursor(self):
        if self._interactive:
            tk.Canvas.configure(self, cursor='hand2' if self._state != 'disabled' else '')

    def _colors(self):
        fill, fg, hover, press = self._t.button_colors(self._variant, self._surface)
        fill = self._override.get('fill', fill)
        fg = self._override.get('fg', fg)
        hover = self._override.get('hover', hover if 'fill' not in self._override else fill)
        if self._state == 'disabled':
            base = fill if self._variant not in ('primary',) else self._t.c['secondary']
            return base, blend(self._t.c['text'], base, 0.62), base, base
        return fill, fg, hover, press

    def _target(self):
        fill, fg, hover, press = self._colors()
        return (press if self._press else hover if self._hover else fill), fg

    def _shown(self):
        if self._from is None or self._to is None:
            return self._target()
        t = round(min(1.0, max(0.0, self._anim.value)) * 12) / 12.0  # 量化，限制缓存规模
        if t >= 1.0:
            return self._to
        return blend(self._from[0], self._to[0], t), blend(self._from[1], self._to[1], t)

    def _retarget(self, duration):
        target = self._target()
        if target == self._to and (self._anim.running or self._shown() == target):
            self._draw()
            return
        self._from = self._shown()
        self._to = target
        self._anim.set(0.0)
        self._anim.to(1.0, duration, ease_out_cubic, restart=True)

    def _parts(self):
        """(是否画圆点, 去掉圆点后的文字)。"""
        text = self._text or ''
        if text.startswith(self.LEAD_DOT):
            return True, text[1:].lstrip()
        return False, text

    def _dot_metrics(self):
        d = max(4, int(round(self._h * 0.24)))
        return d, d + self._t.px(7)

    def _natural_width(self):
        dot, label = self._parts()
        width = self._t.font(self._font).measure(label) + 2 * self._padx
        if dot:
            width += self._dot_metrics()[1]
        return max(self._minw, self._minw_flash, width)

    def _resize(self):
        width = self._natural_width()
        current = int(float(tk.Canvas.cget(self, 'width')))
        try:
            shown = bool(self.winfo_ismapped())
        except tk.TclError:
            shown = False
        if MOTION and shown and current > 10 and abs(width - current) > 1:
            # 已显示的按钮换文字：宽度缓动过去，新文字随之淡入（_draw 里处理）
            self._width_from = current
            self._width_anim.set(current)
            self._width_anim.to(width, 0.24, ease_out_cubic)
            self._draw()
            return
        self._width_anim.set(width)
        tk.Canvas.configure(self, width=width, height=self._h)
        self._draw()

    def _apply_width(self):
        tk.Canvas.configure(self, width=int(round(self._width_anim.value)), height=self._h)
        self._draw()

    def _width_progress(self):
        """宽度过渡进度 0..1（没有过渡时为 1）。"""
        if not self._width_anim.running:
            return 1.0
        span = self._width_anim.target - self._width_from
        if not span:
            return 1.0
        return max(0.0, min(1.0, (self._width_anim.value - self._width_from) / span))

    def _dot_photo(self, d, fill, fg, h):
        if self._pulse and Image is not None:
            period = self.PULSE_PERIODS[self._pulse]
            phase = ((time.monotonic() - self._pulse_t0) / period) % 1.0
            extent = max(2, min(self._t.px(5), int((h - d) / 2) - self._t.px(4)))
            return self._t.halo_photo(d, extent, fg, fill, phase)
        return self._t.circle_photo(d, fg, fill)

    def _draw(self):
        if self._width_anim.running:
            w = int(round(self._width_anim.value))
        else:
            w = self.winfo_width()
            if w <= 1:
                w = int(float(tk.Canvas.cget(self, 'width')))
        h = self._h
        fill, fg = self._shown()
        progress = self._width_progress()
        if progress < 1.0:   # 换文字过程中：新文字从底色淡入
            fg = blend(fill, fg, round(progress * progress * 12) / 12.0)
        # 按下：整体向内收 1–2 px，像被按进去
        inset = int(round(self._press_anim.value * max(1.0, h * 0.045)))
        self._refs = draw_round(self, self._t, 'shape', w - 2 * inset, h - 2 * inset,
                                max(0, self._radius - inset), fill, self._surface, x=inset, y=inset)
        self.delete('label')
        self._dot_item = None
        dot, label = self._parts()
        font = self._t.font(self._font)
        if not dot:
            self.create_text(w // 2, h // 2, text=label, fill=fg, font=self._font, tags='label')
            return
        d, advance = self._dot_metrics()
        total = advance + font.measure(label)
        x0 = (w - total) // 2
        cx, cy = x0 + d / 2.0, h / 2.0
        photo = self._dot_photo(d, fill, fg, h)
        if photo is not None:
            self._dot_item = self.create_image(int(cx), int(cy), image=photo, anchor='center', tags='label')
        else:
            self.create_oval(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2, fill=fg, outline='',
                             tags='label')
        self._dot_ref = photo
        self.create_text(x0 + advance, h // 2, text=label, fill=fg, font=self._font, anchor='w',
                         tags='label')

    def _pulse_loop(self):
        self._pulse_job = None
        try:
            if not self.winfo_exists() or not self._pulse:
                return
            if not self.winfo_viewable():          # 窗口最小化 / 被隐藏：不重绘，低频等待
                self._pulse_job = self.after(500, self._pulse_loop)
                return
        except tk.TclError:
            return
        busy = self._anim.running or self._width_anim.running or self._press_anim.running
        if busy or self._dot_item is None:
            self._draw()
        else:
            # 其余部分不变：只换圆点那一张图
            fill, fg = self._shown()
            photo = self._dot_photo(self._dot_metrics()[0], fill, fg, self._h)
            if photo is not None and photo is not self._dot_ref:
                self.itemconfigure(self._dot_item, image=photo)
                self._dot_ref = photo          # 显示中的图片由控件持有（缓存是 LRU）
        self._pulse_job = self.after(40 if self._pulse == 'fast' else 60, self._pulse_loop)

    def _set_hover(self, value):
        self._hover = value and self._state != 'disabled'
        if not value:
            self._press = False
            self._press_anim.to(0.0, 0.18)
        self._retarget(0.14)

    def _on_press(self, _event):
        if self._state == 'disabled':
            return
        self._press = True
        self._press_anim.to(1.0, 0.07)
        self._retarget(0.06)

    def _on_release(self, event):
        was = self._press
        self._press = False
        self._press_anim.to(0.0, 0.22)
        self._retarget(0.18)
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if was and inside and self._state != 'disabled' and self._command:
            self._command()


class Switch(tk.Canvas):
    """iOS 风格开关：点击翻转变量后调用 command；变量被程序改写时自动缓动到新位置。

    整个开关（底槽 + 滑块 + 阴影）超采样画成一张图，滑块永远在圆角底槽之内。"""

    def __init__(self, parent, theme, variable, command=None, surface=None):
        self._t = theme
        self._var = variable
        self._command = command
        self._surface = _surface_color(parent, surface or parent.cget('bg'))
        self._sw, self._sh = theme.px(44), theme.px(26)
        self._state = 'normal'
        self._hover = False
        self._ref = None
        self._pos = Anim(self, self._draw, value=1.0 if variable.get() else 0.0)
        self._stretch = Anim(self, self._draw, value=0.0)
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0,
                         width=self._sw, height=self._sh, cursor='hand2', takefocus=0)
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<ButtonRelease-1>', self._on_click)
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self._trace = variable.trace_add('write', lambda *_: self._animate())
        self._draw()

    def destroy(self):
        self._pos.cancel()
        self._stretch.cancel()
        if self._trace is not None:
            try:
                self._var.trace_remove('write', self._trace)
            except tk.TclError:
                pass
            self._trace = None
        super().destroy()

    # 兼容旧代码读取 _pos 数值
    @property
    def position(self):
        return self._pos.value

    def configure(self, cnf=None, **kw):
        if 'state' in kw:
            self._state = str(kw.pop('state'))
            tk.Canvas.configure(self, cursor='hand2' if self._state != 'disabled' else '')
            self._draw()
        if kw or cnf:
            return tk.Canvas.configure(self, cnf, **kw)

    config = configure

    def _set_hover(self, value):
        self._hover = value and self._state != 'disabled'
        if not value:
            self._stretch.to(0.0, 0.18)
        self._draw()

    def _on_press(self, _event):
        if self._state != 'disabled':
            self._stretch.to(1.0, 0.12)

    def _on_click(self, event):
        self._stretch.to(0.0, 0.22)
        if self._state == 'disabled':
            return
        if not (0 <= event.x < self._sw and 0 <= event.y < self._sh):
            return
        self._var.set(not bool(self._var.get()))
        if self._command:
            self._command()

    def _animate(self):
        self._pos.to(1.0 if self._var.get() else 0.0, 0.26, ease_in_out)

    def _colors(self, pos):
        c = self._t.c
        track = blend(c['switch_off'], c['green'], pos)
        if self._hover and not self._t.dark:
            track = blend(track, '#000000', 0.04)
        elif self._hover:
            track = blend(track, '#ffffff', 0.06)
        if self._state == 'disabled':
            track = blend(track, self._surface, 0.5)
        return track

    def _image(self, pos, stretch, track):
        pos, stretch = round(pos * 32) / 32.0, round(stretch * 8) / 8.0
        key = ('switch', self._sw, self._sh, pos, stretch, track, self._surface, self._t.dark)
        photo = self._t.cache_get(key)
        if photo is None:
            photo = self._t.cache_put(key, ImageTk.PhotoImage(self.render(pos, stretch, track),
                                                              master=self._t.root))
        return photo

    def render(self, pos, stretch=0.0, track=None):
        """把整只开关超采样画成一张 PIL 图（控件尺寸，RGB）。滑块始终在圆角底槽之内。"""
        if track is None:
            track = self._colors(pos)
        ss = 4
        W, H = self._sw * ss, self._sh * ss
        big = Image.new('RGBA', (W, H), self._surface)
        draw = ImageDraw.Draw(big)
        draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=H / 2.0, fill=track)
        m = 2 * ss
        d = H - 2 * m
        kw = d * (1.0 + 0.22 * stretch)                     # 按住时滑块横向拉伸
        x = m + pos * (W - 2 * m - kw)
        # 柔和投影：模糊后的深色椭圆，再盖上白色滑块
        shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([x, m + ss * 0.8, x + kw, m + d + ss * 0.8],
                                                 radius=d / 2.0, fill=(0, 0, 0, 70 if not self._t.dark else 110))
        shadow = shadow.filter(ImageFilter.GaussianBlur(ss * 1.2))
        big = Image.alpha_composite(big, shadow)
        draw = ImageDraw.Draw(big)
        ring = None if (pos > 0.5 or self._t.dark) else self._t.c['knob_ring']
        draw.rounded_rectangle([x, m, x + kw, m + d], radius=d / 2.0, fill=self._t.c['knob'],
                               outline=ring, width=ss if ring else 0)
        return big.convert('RGB').resize((self._sw, self._sh), Image.LANCZOS)

    def _draw(self):
        pos, stretch = self._pos.value, self._stretch.value
        track = self._colors(pos)
        self.delete('all')
        if Image is not None:
            self._ref = self._image(pos, stretch, track)
            self.create_image(0, 0, image=self._ref, anchor='nw')
            return
        # Pillow 缺失：原生图形，滑块只画圆，不会越出底槽
        r = self._sh // 2
        self.create_oval(0, 0, self._sh, self._sh, fill=track, outline='')
        self.create_oval(self._sw - self._sh, 0, self._sw, self._sh, fill=track, outline='')
        self.create_rectangle(r, 0, self._sw - r, self._sh, fill=track, outline='')
        m = self._t.px(2)
        d = self._sh - 2 * m
        x = m + int(round(pos * (self._sw - self._sh)))
        self.create_oval(x, m, x + d, m + d, fill=self._t.c['knob'], outline='')


class Segmented(tk.Canvas):
    """分段控件（iOS 风格）：底槽 + 会滑动的选中滑块 + 文字。

    select(i) 让滑块缓动到第 i 段（用于同步外部状态，不调用 command）；
    用户点击某一段时先移动滑块，再调用 command(i)。"""

    def __init__(self, parent, theme, labels, command=None, font=None, height=34, padx=18,
                 minwidth=96, radius=10, inset=3, surface=None):
        self._t = theme
        self._labels = list(labels)
        self._command = command
        self._font = font or theme.f_btn_small
        self._h = theme.px(height)
        self._inset = theme.px(inset)
        self._radius = theme.px(radius)
        self._surface = _surface_color(parent, surface or parent.cget('bg'))
        measure = theme.font(self._font).measure
        self._seg_w = max(theme.px(minwidth), max(measure(t) for t in self._labels) + 2 * theme.px(padx))
        self._total_w = self._seg_w * len(self._labels) + 2 * self._inset
        self._index = 0
        self._hover = None
        self._refs = []
        self._pos = Anim(self, self._draw, value=0.0)
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0, width=self._total_w,
                         height=self._h, cursor='hand2', takefocus=0)
        self.bind('<Motion>', lambda e: self._set_hover(self._hit(e.x)))
        self.bind('<Leave>', lambda e: self._set_hover(None))
        self.bind('<ButtonRelease-1>', self._on_click)
        self._draw()

    @property
    def index(self):
        return self._index

    @property
    def position(self):
        """滑块当前位置（以段为单位，动画中为小数）。"""
        return self._pos.value

    def select(self, index, animate=True):
        index = max(0, min(len(self._labels) - 1, int(index)))
        self._index = index
        if animate:
            self._pos.to(float(index), 0.28, ease_out_cubic)   # 不回弹：滑块永不越出底槽
        else:
            self._pos.set(float(index))
            self._draw()

    def destroy(self):
        self._pos.cancel()
        super().destroy()

    def _hit(self, x):
        i = int((x - self._inset) // self._seg_w)
        return i if 0 <= i < len(self._labels) else None

    def _set_hover(self, index):
        if index != self._hover:
            self._hover = index
            self._draw()

    def _on_click(self, event):
        index = self._hit(event.x)
        if index is None or not (0 <= event.y < self._h):
            return
        if index != self._index:
            self.select(index)
        if self._command:
            self._command(index)

    def _draw(self):
        t, c = self._t, self._t.c
        w, h, ins, seg = self._total_w, self._h, self._inset, self._seg_w
        refs = draw_round(self, t, 'track', w, h, self._radius, c['seg_bg'], self._surface)
        pos = max(0.0, min(len(self._labels) - 1.0, self._pos.value))
        x = ins + int(round(pos * seg))
        border = None if t.dark else blend(c['seg_bg'], '#000000', 0.07)
        refs += draw_round(self, t, 'thumb', seg, h - 2 * ins, max(2, self._radius - ins),
                           c['seg_on'], c['seg_bg'], border, x=x, y=ins)
        self.delete('label')
        for i, label in enumerate(self._labels):
            near = max(0.0, 1.0 - abs(pos - i))          # 滑块越近，文字越接近正文色
            color = blend(c['muted'], c['text'], near)
            if self._hover == i and near < 0.5:
                color = blend(color, c['text'], 0.4)
            self.create_text(ins + i * seg + seg // 2, h // 2, text=label, fill=color,
                             font=self._font, tags='label')
        self._refs = refs


def fade_in_text(widget, background, duration=0.2):
    """让 tk.Text 的文字（含各 tag 的前景色）从底色淡入。用于切换页面。"""
    if not MOTION:
        return
    colors = getattr(widget, '_ob_fade_colors', None)
    if colors is None:   # 第一次：记下真实颜色；之后的淡入都以它为终点
        tags = {}
        for tag in widget.tag_names():
            color = widget.tag_cget(tag, 'foreground')
            if color and tag != 'sel':
                tags[tag] = str(color)
        colors = widget._ob_fade_colors = (str(widget.cget('foreground')), tags)
    fg, tags = colors

    def step():
        p = anim.value
        try:
            widget.configure(foreground=blend(background, fg, p))
            for tag, color in tags.items():
                widget.tag_configure(tag, foreground=blend(background, color, p))
        except tk.TclError:
            anim.cancel()

    old = getattr(widget, '_ob_fade_anim', None)
    if old is not None:
        old.cancel()
    anim = widget._ob_fade_anim = Anim(widget, step, value=0.0)
    step()
    anim.to(1.0, duration, ease_out_cubic)


def fade_in_window(root, duration=0.24):
    """窗口启动时从透明淡入；任何异常都保证最终完全不透明。"""
    if not MOTION:
        return
    try:
        root.attributes('-alpha', 0.0)
    except tk.TclError:
        return
    t0 = time.monotonic()

    def step():
        try:
            p = min(1.0, (time.monotonic() - t0) / duration)
            root.attributes('-alpha', ease_out_cubic(p))
            if p < 1.0:
                root.after(15, step)
        except tk.TclError:
            pass
    root.after(30, step)
    # 兜底：无论如何 1 秒后完全不透明
    root.after(1000, lambda: _safe_alpha(root))


def _safe_alpha(root):
    try:
        root.attributes('-alpha', 1.0)
    except tk.TclError:
        pass


def style_window_chrome(root, theme):
    """Windows 10/11：深色标题栏；Win11 上再把标题栏染成窗口底色，与内容融为一体。"""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        dwm = ctypes.windll.dwmapi
        value = ctypes.c_int(1 if theme.dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（新 / 旧编号）
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), 4) == 0:
                break
        r, g, b = _rgb(theme.c['bg'])
        caption = ctypes.c_int(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), 4)   # DWMWA_CAPTION_COLOR
        r, g, b = _rgb(theme.c['text'])
        text = ctypes.c_int(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), 4)      # DWMWA_TEXT_COLOR
    except Exception:
        pass
