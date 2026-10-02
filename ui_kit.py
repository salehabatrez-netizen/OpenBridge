"""
ui_kit - Apple 风格的 Tk 小组件（OpenBridge GUI 使用）

纯 Tk + Pillow 实现，Pillow 缺失时自动退化为 Tk 原生图形：
- Theme       浅色 / 深色调色板（默认跟随 Windows 应用主题），字体与 DPI 缩放
- RoundBox    抗锯齿圆角容器（卡片、输入框底、分段控件底），内容放在 .body 中
- PillButton  胶囊按钮，兼容 tk.Button 常用接口：config(text/state/bg/fg/...)、cget、btn['text']
- Switch      iOS 风格开关，绑定 BooleanVar，变量被代码改写时自动同步动画
- Dot         状态圆点

圆角只在四个角贴缓存好的小图，中间用矩形填充，所以窗口拖拽缩放时几乎没有开销。
"""

import os
import sys
import tkinter as tk
import tkinter.font as tkfont

try:
    from PIL import Image, ImageDraw, ImageTk
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
    text='#f5f5f7', muted='#98989d', tertiary='#636366',
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


class Theme:
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

        ui = pick('MiSans', 'HarmonyOS Sans SC', 'Microsoft YaHei UI')
        ui_medium = pick('MiSans Medium', 'MiSans Demibold', ui)
        ui_bold = pick('MiSans Semibold', 'MiSans Demibold', ui)
        bold = 'normal' if ui_bold != ui else 'bold'
        display = pick('Segoe UI Variable Display Semib', 'Segoe UI Variable Display', 'Segoe UI', ui)
        mono = pick('Cascadia Mono', 'Consolas')
        self.f_display = (display, 21, 'bold' if 'Semib' not in display else 'normal')
        self.f_section = (ui_bold, 12, bold)
        self.f_body = (ui, 10)
        self.f_body_b = (ui_medium, 10, 'normal' if ui_medium != ui else 'bold')
        self.f_caption = (ui, 9)
        self.f_btn = (ui_medium, 10, 'normal' if ui_medium != ui else 'bold')
        self.f_btn_small = (ui_medium, 9, 'normal' if ui_medium != ui else 'bold')
        self.f_mono = (mono, 10)
        self.f_mono_small = (mono, 9)
        self._fonts = {}
        self._images = {}

    # 尺寸与字体
    def px(self, value):
        return int(round(value * self.scale))

    def font(self, spec):
        key = tuple(spec)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(root=self.root, font=spec)
        return self._fonts[key]

    # 按钮配色：返回 (底色, 文字, 悬停底色, 按下底色)
    def button_colors(self, variant, surface):
        c = self.c
        hover_mix = '#ffffff' if self.dark else '#000000'
        tinted = lambda fill, fg: (fill, fg, blend(fill, hover_mix, 0.06), blend(fill, hover_mix, 0.12))
        if variant == 'primary':
            return c['accent'], c['on_accent'], c['accent_hover'], c['accent_press']
        if variant == 'danger':
            return tinted(c['red_tint'], c['red_text'])
        if variant == 'success':
            return tinted(c['green_tint'], c['green_text'])
        if variant == 'warning':
            return tinted(c['orange_tint'], c['orange_text'])
        if variant == 'plain':
            return surface, c['accent'], blend(surface, c['accent'], 0.08), blend(surface, c['accent'], 0.16)
        if variant == 'segment':
            return surface, c['muted'], blend(surface, c['text'], 0.05), blend(surface, c['text'], 0.1)
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
        if key not in self._images:
            ss = 4
            im = Image.new('RGB', (d * ss, d * ss), surface)
            ImageDraw.Draw(im).ellipse([0, 0, d * ss - 1, d * ss - 1], fill=fill,
                                       outline=ring, width=ss if ring else 0)
            self._images[key] = im.resize((d, d), Image.LANCZOS)
        return self._images[key]

    def circle_photo(self, d, fill, surface, ring=None):
        key = ('circle_photo', d, fill, surface, ring)
        if key not in self._images:
            im = self._circle(d, fill, surface, ring)
            self._images[key] = ImageTk.PhotoImage(im, master=self.root) if im is not None else None
        return self._images[key]

    def corners(self, r, fill, surface, border=None):
        key = ('corners', r, fill, surface, border)
        if key not in self._images:
            im = self._circle(2 * r, fill, surface, border)
            if im is None:
                self._images[key] = None
            else:
                boxes = ((0, 0, r, r), (r, 0, 2 * r, r), (0, r, r, 2 * r), (r, r, 2 * r, 2 * r))
                self._images[key] = [ImageTk.PhotoImage(im.crop(b), master=self.root) for b in boxes]
        return self._images[key]


def draw_round(canvas, theme, tag, w, h, r, fill, surface, border=None, x=0, y=0):
    """在 canvas 上画一个圆角矩形（四角贴图 + 中间矩形），旧图形按 tag 删除。"""
    canvas.delete(tag)
    w, h = int(w), int(h)
    if w < 2 or h < 2:
        return
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
        return
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


class RoundBox(tk.Canvas):
    """圆角容器。子控件放进 self.body。

    expand=False：高度随内容变化（卡片）；expand=True：内容填满分配到的空间（日志区）。
    """

    def __init__(self, parent, theme, fill, radius=14, pad=(18, 16), border=None,
                 expand=False, surface=None, fit_width=False):
        self._t = theme
        self._fit = fit_width
        self._fill = fill
        self._surface = surface or parent.cget('bg')
        self._radius = theme.px(radius)
        self._padx, self._pady = theme.px(pad[0]), theme.px(pad[1])
        self._border = border
        self._expand = expand
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
        draw_round(self, self._t, 'bg', event.width, event.height, self._radius,
                   self._fill, self._surface, self._border)
        self.tag_lower('bg')


class Dot(tk.Canvas):
    def __init__(self, parent, theme, color, size=8, surface=None):
        self._t, self._size = theme, theme.px(size)
        self._surface = surface or parent.cget('bg')
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0,
                         width=self._size, height=self._size)
        self.set_color(color)

    def set_color(self, color):
        self.delete('all')
        photo = self._t.circle_photo(self._size, color, self._surface)
        if photo is not None:
            self.create_image(0, 0, image=photo, anchor='nw')
        else:
            self.create_oval(0, 0, self._size - 1, self._size - 1, fill=color, outline='')


class PillButton(tk.Canvas):
    """胶囊按钮。兼容 tk.Button 的常用读写方式，便于原有逻辑与测试继续使用。"""

    _OWN = ('text', 'command', 'variant', 'state', 'bg', 'background', 'fg', 'foreground',
            'activebackground', 'activeforeground', 'font', 'textvariable', 'disabledforeground',
            'relief', 'bd', 'padx', 'pady')

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
        self._radius = theme.px(radius) if radius is not None else self._h // 2
        self._surface = surface or parent.cget('bg')
        self._interactive = interactive
        self._override = {}
        self._hover = self._press = False
        self._textvar = None
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
        else:
            self._draw()

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

    # 内部 --------------------------------------------------------------
    def _bind_textvariable(self, var):
        self._textvar = var
        self._text = var.get()

        def changed(*_):
            self._text = var.get()
            self._resize()
        var.trace_add('write', changed)

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

    def _resize(self):
        width = self._t.font(self._font).measure(self._text) + 2 * self._padx
        tk.Canvas.configure(self, width=max(self._minw, width), height=self._h)
        self._draw()

    def _draw(self):
        w = self.winfo_width()
        if w <= 1:
            w = int(float(tk.Canvas.cget(self, 'width')))
        h = self._h
        fill, fg, hover, press = self._colors()
        color = press if self._press else hover if self._hover else fill
        draw_round(self, self._t, 'shape', w, h, self._radius, color, self._surface)
        self.delete('label')
        self.create_text(w // 2, h // 2, text=self._text, fill=fg, font=self._font, tags='label')

    def _set_hover(self, value):
        self._hover = value and self._state != 'disabled'
        if not value:
            self._press = False
        self._draw()

    def _on_press(self, _event):
        if self._state == 'disabled':
            return
        self._press = True
        self._draw()

    def _on_release(self, event):
        was = self._press
        self._press = False
        self._draw()
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if was and inside and self._state != 'disabled' and self._command:
            self._command()


class Switch(tk.Canvas):
    """iOS 风格开关：点击翻转变量后调用 command；变量被程序改写时自动重绘。"""

    def __init__(self, parent, theme, variable, command=None, surface=None):
        self._t = theme
        self._var = variable
        self._command = command
        self._surface = surface or parent.cget('bg')
        self._sw, self._sh = theme.px(44), theme.px(26)
        self._pos = 1.0 if variable.get() else 0.0
        self._anim = None
        self._state = 'normal'
        super().__init__(parent, bg=self._surface, highlightthickness=0, bd=0,
                         width=self._sw, height=self._sh, cursor='hand2', takefocus=0)
        self.bind('<ButtonRelease-1>', self._on_click)
        variable.trace_add('write', lambda *_: self._animate())
        self._draw()

    def configure(self, cnf=None, **kw):
        if 'state' in kw:
            self._state = str(kw.pop('state'))
            tk.Canvas.configure(self, cursor='hand2' if self._state != 'disabled' else '')
            self._draw()
        if kw or cnf:
            return tk.Canvas.configure(self, cnf, **kw)

    config = configure

    def _on_click(self, event):
        if self._state == 'disabled':
            return
        if not (0 <= event.x < self._sw and 0 <= event.y < self._sh):
            return
        self._var.set(not bool(self._var.get()))
        if self._command:
            self._command()

    def _animate(self):
        target = 1.0 if self._var.get() else 0.0
        if self._anim is not None:
            self.after_cancel(self._anim)
            self._anim = None

        def step():
            delta = target - self._pos
            if abs(delta) < 0.04:
                self._pos = target
                self._anim = None
            else:
                self._pos += delta * 0.45
                self._anim = self.after(14, step)
            self._draw()
        step()

    def _draw(self):
        c = self._t.c
        on = self._pos >= 0.5
        track = c['green'] if on else c['switch_off']
        if self._state == 'disabled':
            track = blend(track, self._surface, 0.5)
        draw_round(self, self._t, 'track', self._sw, self._sh, self._sh // 2, track, self._surface)
        margin = self._t.px(2)
        d = self._sh - 2 * margin
        x = margin + int(round(self._pos * (self._sw - self._sh)))
        self.delete('knob')
        ring = None if on or self._t.dark else c['knob_ring']
        photo = self._t.circle_photo(d, c['knob'], track, ring)
        if photo is not None:
            self.create_image(x, margin, image=photo, anchor='nw', tags='knob')
        else:
            self.create_oval(x, margin, x + d, margin + d, fill=c['knob'], outline=ring or '', tags='knob')


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
