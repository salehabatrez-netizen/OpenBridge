"""Always-on-top floating window: "is the AI working, or is it stuck?"

Why a separate window
---------------------
The GUI already has an activity strip, but it only lights up for Computer Use
calls and it is buried inside a window the user normally keeps behind the
editor and the game. The question this window answers is different and needs
to be visible without hunting for it:

    Is anything happening right now, and if not, how long has it been quiet?

Two independent signals feed it, because either alone lies:

* ``ActivityMonitor``     - Computer Use actions only. Says what is running.
* ``last_client_activity``- refreshed by EVERY tools/call in bridge.py. This
  is what catches "the assistant is reading files / running commands", which
  the activity monitor never sees.

Stall detection therefore keys off the *later* of the two. A long-running
single action is reported as RUNNING with its elapsed time (not a stall),
while silence across both signals past a threshold turns the window red.

The window is deliberately small, frameless and draggable so it can sit over a
fullscreen game without stealing focus. It has two sizes - a one-line capsule
and a status card - fades while the pointer is elsewhere, and offers local-only
quick actions (pause AI, open the console) plus a right-click menu.

Project board
-------------
Below the status line sits a collapsible "项目进展" section: one row per
HANDOFF project (health dot, done/total, progress bar, live claim / what waits
on the user / focus, last activity) plus the _coord guardian line. The data
comes from ``project_board.py``, which is re-imported whenever that file
changes, so the board can improve without restarting the GUI (a restart
changes the MCP URL and drops every connected agent). A broken new version is
never loaded; the last good one keeps painting.
"""

import time

# Thresholds in seconds. QUIET is "nothing for a while, probably thinking or
# between turns"; STALLED is "long enough that you should go look".
QUIET_AFTER = 45.0
STALLED_AFTER = 180.0
# A single Computer Use action that runs this long is suspicious in itself.
LONG_ACTION_AFTER = 90.0

# Project board: refresh every BOARD_EVERY ticks (1 s each), at most BOARD_LIMIT rows.
BOARD_EVERY = 5
BOARD_LIMIT = 8
BAR_WIDTH = 284
BG = '#1f1f22'

STATE_OFFLINE = 'offline'
STATE_PAUSED = 'paused'
STATE_RUNNING = 'running'
STATE_LONG = 'long'
STATE_IDLE = 'idle'
STATE_QUIET = 'quiet'
STATE_STALLED = 'stalled'

# Colour + glyph per state. Kept here so the renderer stays dumb and the
# mapping is unit-testable without a display.
PRESENTATION = {
    STATE_OFFLINE: ('#6b7280', '\u26aa', '服务未启动'),
    STATE_PAUSED:  ('#efc775', '\u23f8', '已暂停'),
    STATE_RUNNING: ('#42d392', '\U0001f7e2', '正在操作'),
    STATE_LONG:    ('#f0a742', '\U0001f7e0', '操作耗时偏长'),
    STATE_IDLE:    ('#7fb4ff', '\U0001f535', '空闲'),
    STATE_QUIET:   ('#f0a742', '\U0001f7e0', '安静'),
    STATE_STALLED: ('#ff6b6b', '\U0001f534', '疑似卡住'),
}


def active_project(root=None):
    """Which project the user has assigned, for display only.

    Deliberately silent on every failure: the indicator must keep working
    when the registry is missing, half-written, or from a future schema.
    """
    import json
    import os
    if root is None:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'HANDOFF')
    try:
        with open(os.path.join(root, 'registry.json'), encoding='utf-8') as handle:
            data = json.load(handle)
        slug = data.get('active')
        if not slug:
            return None
        meta = (data.get('projects') or {}).get(slug) or {}
        return {'slug': slug, 'name': meta.get('name', slug)}
    except Exception:
        return None


_BOARD = {'mod': None, 'mtime': None, 'path': None, 'error': None}


def board_module(path=None):
    """Return project_board, re-imported whenever the file changes.

    Loaded under a private name with importlib so a failed import cannot leave
    a half-initialised module behind: on any error the previous good module is
    kept, the error is recorded, and the load is retried on the next refresh.
    """
    import importlib.util
    import os
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'project_board.py')
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        _BOARD['error'] = '缺少 project_board.py'
        return _BOARD['mod']
    if _BOARD['mod'] is not None and mtime == _BOARD['mtime'] and _BOARD['path'] == path:
        return _BOARD['mod']
    try:
        spec = importlib.util.spec_from_file_location('project_board_live', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, 'board', None)):
            raise AttributeError('project_board.board() 不存在')
    except Exception as exc:
        _BOARD['error'] = 'project_board.py 加载失败，沿用旧版：%s' % exc
        return _BOARD['mod']
    _BOARD.update(mod=module, mtime=mtime, path=path, error=None)
    return module


def _fmt_duration(seconds):
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return '%.0f 秒' % seconds
    if seconds < 3600:
        return '%d 分 %02d 秒' % (int(seconds // 60), int(seconds % 60))
    return '%d 小时 %02d 分' % (int(seconds // 3600), int((seconds % 3600) // 60))


def classify(snapshot, last_tool_call_age, running=True,
             quiet_after=QUIET_AFTER, stalled_after=STALLED_AFTER,
             long_action_after=LONG_ACTION_AFTER):
    """Decide what the indicator should say.

    ``snapshot``           ActivityMonitor.snapshot() output, or None.
    ``last_tool_call_age`` seconds since ANY tools/call, or None if unknown.

    Returned dict is pure data so the Tk layer can be replaced or tested.
    """
    if not running or snapshot is None:
        return {'state': STATE_OFFLINE, 'headline': '服务未启动',
                'detail': '启动 OpenBridge 后这里会显示 AI 的实时动作', 'age': None}

    if snapshot.get('paused'):
        return {'state': STATE_PAUSED, 'headline': '已暂停 AI 操作',
                'detail': '本机已按下暂停；AI 的操作请求会被拒绝', 'age': None}

    if snapshot.get('busy'):
        elapsed = float(snapshot.get('elapsed') or 0.0)
        pending = int(snapshot.get('pending') or 0)
        queued = '  (+%d 排队)' % (pending - 1) if pending > 1 else ''
        label = snapshot.get('current') or '(未命名动作)'
        state = STATE_LONG if elapsed >= long_action_after else STATE_RUNNING
        detail = '已用 %s%s' % (_fmt_duration(elapsed), queued)
        if state == STATE_LONG:
            detail += '  ·  超过 %s 仍未结束，建议查看' % _fmt_duration(long_action_after)
        return {'state': state, 'headline': label, 'detail': detail,
                'age': elapsed}

    # Nothing running. How long has the whole bridge been silent?
    age = last_tool_call_age
    if age is None:
        last = snapshot.get('last')
        headline = '空闲'
        detail = ('上次 %s %s' % (last['label'], '完成' if last.get('ok') else '失败')
                  if last else '尚未执行任何操作')
        return {'state': STATE_IDLE, 'headline': headline, 'detail': detail,
                'age': None}

    counters = snapshot.get('counters') or {}
    tail = '  ·  累计成功 %d / 失败 %d' % (counters.get('ok', 0), counters.get('error', 0))
    if age >= stalled_after:
        return {'state': STATE_STALLED,
                'headline': '疑似卡住：%s 无任何动作' % _fmt_duration(age),
                'detail': '超过 %s 没有工具调用，建议检查对话是否中断%s'
                          % (_fmt_duration(stalled_after), tail),
                'age': age}
    if age >= quiet_after:
        return {'state': STATE_QUIET,
                'headline': '安静 %s' % _fmt_duration(age),
                'detail': 'AI 可能在思考或等待你回复%s' % tail,
                'age': age}
    return {'state': STATE_IDLE,
            'headline': '空闲  ·  %s 前有活动' % _fmt_duration(age),
            'detail': '连接正常%s' % tail,
            'age': age}


def render_text(verdict):
    """One-line form used by the window title and any text fallback."""
    colour, glyph, short = PRESENTATION[verdict['state']]
    return '%s  %s' % (glyph, verdict['headline']), colour, short

# ════════════════════════════════════════════════════════════════════════
#  Window (Apple-style HUD)
# ════════════════════════════════════════════════════════════════════════
#
# Two sizes, so it can live over a game without covering it:
#   mini  - a one-line capsule (dot · what is happening · waiting badge)
#   card  - status, local quick actions, and the collapsible project board
# It fades to IDLE_ALPHA while the pointer is elsewhere and comes back to full
# opacity on hover or when something needs attention (stalled / long action).
# Layout preferences are remembered only when the GUI passes ``prefs_path``.

HUD = {
    'bg': '#1f1f22', 'raised': '#2c2c2f', 'separator': '#38383b', 'border': '#3a3a3d',
    'text': '#f5f5f7', 'muted': '#a1a1a6', 'tertiary': '#6e6e73', 'track': '#3a3a3d',
}
# Apple system colours per state (PRESENTATION keeps the legacy palette for text/tests).
STATE_COLOURS = {
    STATE_OFFLINE: '#8e8e93', STATE_PAUSED: '#ffd60a', STATE_RUNNING: '#30d158',
    STATE_LONG: '#ff9f0a', STATE_IDLE: '#0a84ff', STATE_QUIET: '#ffd60a',
    STATE_STALLED: '#ff453a',
}
ATTENTION = (STATE_STALLED, STATE_LONG)
ANIMATED = (STATE_RUNNING, STATE_LONG, STATE_STALLED)
# project_board.py (hot-reloaded) still speaks the old palette: translate on paint.
BOARD_COLOURS = {
    '#42d392': '#30d158', '#efc775': '#ffd60a', '#f0a742': '#ff9f0a', '#ff6b6b': '#ff453a',
    '#7fb4ff': '#0a84ff', '#6b7280': '#8e8e93', '#8b9bb4': HUD['muted'], '#5b6b85': HUD['tertiary'],
}
IDLE_ALPHA = 0.82
CARD_WIDTH = 330
ROWS_COLLAPSED = 4            # project rows shown before "显示全部"
SNAP_DISTANCE = 18
ICON_FONTS = ('Segoe Fluent Icons', 'Segoe MDL2 Assets')
ICONS = {'expand': '\ue70d', 'collapse': '\ue70e', 'close': '\ue711', 'more': '\ue712'}
ICONS_TEXT = {'expand': '\u25be', 'collapse': '\u25b4', 'close': '\u2715', 'more': '\u22ef'}


def board_colour(colour):
    return BOARD_COLOURS.get(str(colour or '').lower(), colour or HUD['muted'])


def clip_text(text, width):
    """Clip to ``width`` display columns (CJK = 2) with an ellipsis."""
    import unicodedata
    text = ' '.join(str(text or '').split())
    out, used = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if used + w > width:
            return ''.join(out).rstrip() + '\u2026'
        out.append(ch)
        used += w
    return text


def mini_text(verdict):
    """What the capsule says: short and scannable."""
    state = verdict['state']
    if state in (STATE_RUNNING, STATE_LONG):
        age = verdict.get('age')
        tail = ('  ' + _fmt_duration(age)) if age is not None else ''
        return clip_text(verdict['headline'], 22) + tail
    if state == STATE_STALLED:
        return '疑似卡住  ' + _fmt_duration(verdict.get('age') or 0)
    if state == STATE_QUIET:
        return '安静  ' + _fmt_duration(verdict.get('age') or 0)
    if state == STATE_IDLE and verdict.get('age') is not None:
        return '空闲  ·  %s前' % _fmt_duration(verdict['age']).replace(' ', '')
    return PRESENTATION[state][2]


def load_prefs(path):
    import json
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(path, data):
    import json
    import os
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass                                # layout prefs are a convenience only


def default_prefs_path():
    import os
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'OpenBridge', 'floating_window.json')


class _PulseDot:
    """Status dot with a soft breathing halo (antialiased via ui_kit when available)."""

    def __init__(self, parent, theme, size=8, box=18):
        import tkinter as tk
        self.theme, self.size, self.box = theme, theme.px(size), theme.px(box)
        self.canvas = tk.Canvas(parent, width=self.box, height=self.box, bg=HUD['bg'],
                                highlightthickness=0, bd=0)
        self.colour = STATE_COLOURS[STATE_OFFLINE]
        self.phase = None
        self.draw()

    def draw(self):
        from ui_kit import blend
        c, t = self.canvas, self.theme
        c.delete('all')
        c._ob_refs = []   # ui_kit 图片缓存是 LRU：显示中的图片由画布自己持有
        mid = self.box // 2
        if self.phase is not None:
            # halo grows and fades: 0 → 1
            d = self.size + int((self.box - self.size) * self.phase)
            d -= d % 2
            halo = blend(self.colour, HUD['bg'], 0.55 + 0.45 * self.phase)
            photo = t.circle_photo(d, halo, HUD['bg']) if d >= 2 else None
            if photo is not None:
                c._ob_refs.append(photo)
                c.create_image(mid - d // 2, mid - d // 2, image=photo, anchor='nw')
        photo = t.circle_photo(self.size, self.colour, HUD['bg'])
        x = mid - self.size // 2
        if photo is not None:
            c._ob_refs.append(photo)
            c.create_image(x, x, image=photo, anchor='nw')
        else:
            c.create_oval(x, x, x + self.size, x + self.size, fill=self.colour, outline='')


class FloatingStatusWindow:
    """Small always-on-top Tk window. Created lazily so importing is safe.

    The whole class is inert without a display: `open()` is the only method
    that touches Tk, and every callback guards on the window still existing.

    ``actions`` (optional, all local-only): ``toggle_pause()``, ``show_console()``
    and ``on_close()`` callbacks supplied by the GUI.
    ``prefs_path`` (optional): JSON file for size/position/opacity; when given,
    the first launch starts as the compact capsule.
    """

    def __init__(self, parent, poll, actions=None, prefs_path=None):
        """``poll`` returns (snapshot_or_None, last_tool_call_age, running)."""
        self.parent = parent
        self.poll = poll
        self.actions = dict(actions or {})
        self.prefs_path = prefs_path
        self.window = None
        self._after_id = None
        self._anim_id = None
        self._hover_id = None
        self._drag = (0, 0)
        self._drag_moved = False
        self._last_state = None
        self._mapped_mode = None
        self._verdict = None
        self._board_rows = []
        self._board_data = None
        self._ticks = 0
        self._phase = 0.0
        self._alpha = 1.0
        # Layout defaults: compact when the GUI manages prefs, full card otherwise.
        self._mode = 'mini' if prefs_path else 'card'
        self._board_open = not prefs_path
        self._show_all = False
        self._idle_alpha = IDLE_ALPHA
        self._snap = True
        self._position = None
        if prefs_path:
            self._apply_prefs(load_prefs(prefs_path))

    # -------------------------------------------------------------- prefs
    def _apply_prefs(self, data):
        if data.get('mode') in ('mini', 'card'):
            self._mode = data['mode']
        for key, attr in (('board_open', '_board_open'), ('show_all', '_show_all'),
                          ('snap', '_snap')):
            if isinstance(data.get(key), bool):
                setattr(self, attr, data[key])
        alpha = data.get('idle_alpha')
        if isinstance(alpha, (int, float)) and 0.3 <= alpha <= 1.0:
            self._idle_alpha = float(alpha)
        pos = data.get('position')
        if isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) for v in pos):
            self._position = tuple(pos)

    def _save_prefs(self):
        if not self.prefs_path:
            return
        pos = None
        if self.window is not None:
            try:
                pos = [self.window.winfo_x(), self.window.winfo_y()]
            except Exception:
                pos = None
        save_prefs(self.prefs_path, {
            'mode': self._mode, 'board_open': self._board_open, 'show_all': self._show_all,
            'idle_alpha': self._idle_alpha, 'snap': self._snap,
            'position': pos or (list(self._position) if self._position else None)})

    # -------------------------------------------------------------- setup
    def toggle(self):
        if self.window is not None:
            self.close()
            return False
        self.open()
        return True

    def open(self):
        import tkinter as tk
        import tkinter.font as tkfont
        from ui_kit import Theme
        if self.window is not None:
            return self.window
        win = tk.Toplevel(self.parent)
        self.window = win
        win.title('AI 状态')
        win.overrideredirect(True)          # frameless
        win.attributes('-topmost', True)    # stays above the game
        win.configure(bg=HUD['bg'])
        self.theme = t = Theme(win, dark=True)
        px = t.px
        families = set(tkfont.families(win))
        icon_family = next((f for f in ICON_FONTS if f in families), None)
        self._icons = ICONS if icon_family else ICONS_TEXT
        self._f_icon = (icon_family, 9) if icon_family else ('Segoe UI', 10)
        self._f_head = (t.f_section[0], 11, t.f_section[2])
        self._f_body = (t.f_body[0], 9)
        self._f_small = (t.f_caption[0], 8)
        self._f_label = (t.f_body_b[0], 9, t.f_body_b[2])
        self._bar_w = px(BAR_WIDTH)

        self.shell = tk.Frame(win, bg=HUD['bg'])
        self.shell.pack(fill='both', expand=True)
        self._build_mini()
        self._build_card()
        self._build_board(self.card)
        self._build_menu()

        for widget in (self.shell, self.mini, self.mini_text, self.mini_dot.canvas,
                       self.card, self.card_top, self.card_dot.canvas, self.lbl_state,
                       self.lbl_age, self.lbl_head, self.lbl_detail, self.lbl_project,
                       self.board_box, self.board_rows_box, self.lbl_board_foot, self.lbl_guard):
            self._draggable(widget)
        for widget in (self.mini_badge,):
            self._draggable(widget)
        win.bind('<Button-3>', self._show_menu)
        win.bind('<Escape>', lambda _e: self.set_mode('mini'))

        self._layout()
        win.update_idletasks()
        win.geometry('+%d+%d' % self._initial_position(win))
        win.protocol('WM_DELETE_WINDOW', self.close)
        self._round_corners()
        self._tick()
        self._hover_loop()
        return win

    # widgets ------------------------------------------------------------
    def _label(self, parent, text='', fg=None, font=None, **kw):
        import tkinter as tk
        kw.setdefault('anchor', 'w')
        kw.setdefault('justify', 'left')
        return tk.Label(parent, text=text, bg=HUD['bg'], fg=fg or HUD['text'],
                        font=font or self._f_body, bd=0, **kw)

    def _icon(self, parent, name, command, tip_colour=None):
        label = self._label(parent, self._icons[name], fg=HUD['tertiary'], font=self._f_icon,
                            cursor='hand2', padx=self.theme.px(5), pady=self.theme.px(2))
        label.bind('<Enter>', lambda _e: label.config(fg=tip_colour or HUD['text']))
        label.bind('<Leave>', lambda _e: label.config(fg=HUD['tertiary']))
        label.bind('<Button-1>', lambda _e: command())
        return label

    def _build_mini(self):
        import tkinter as tk
        px = self.theme.px
        self.mini = tk.Frame(self.shell, bg=HUD['bg'], padx=px(10), pady=px(6))
        self.mini_dot = _PulseDot(self.mini, self.theme)
        self.mini_dot.canvas.pack(side='left', padx=(0, px(6)))
        self.mini_text = self._label(self.mini, '正在连接…', font=self._f_label)
        self.mini_text.pack(side='left')
        self.mini_badge = self._label(self.mini, '', fg='#1f1f22', font=self._f_small,
                                      padx=px(6), pady=0)
        self.mini_expand = self._icon(self.mini, 'expand', lambda: self.set_mode('card'))
        self.mini_expand.pack(side='right', padx=(px(6), 0))

    def _build_card(self):
        import tkinter as tk
        from ui_kit import PillButton
        px = self.theme.px
        self.card = tk.Frame(self.shell, bg=HUD['bg'], padx=px(14), pady=px(12))
        self.card_top = tk.Frame(self.card, bg=HUD['bg'])
        self.card_top.pack(fill='x')
        self.card_dot = _PulseDot(self.card_top, self.theme)
        self.card_dot.canvas.pack(side='left', padx=(0, px(4)))
        self.lbl_state = self._label(self.card_top, '', font=self._f_label)
        self.lbl_state.pack(side='left')
        self._icon(self.card_top, 'close', self.close, tip_colour='#ff453a').pack(side='right')
        self._icon(self.card_top, 'collapse', lambda: self.set_mode('mini')).pack(side='right')
        self._icon(self.card_top, 'more', lambda: self._show_menu(None)).pack(side='right')
        self.lbl_age = self._label(self.card_top, '', fg=HUD['tertiary'], font=self._f_small)
        self.lbl_age.pack(side='right', padx=(0, px(6)))
        wrap = px(CARD_WIDTH - 28)
        self.lbl_head = self._label(self.card, '正在连接…', font=self._f_head, wraplength=wrap)
        self.lbl_head.pack(fill='x', pady=(px(6), 0))
        self.lbl_detail = self._label(self.card, '', fg=HUD['muted'], wraplength=wrap)
        self.lbl_detail.pack(fill='x', pady=(px(2), 0))
        # Naming the assigned project makes a mis-assigned assistant obvious.
        self.lbl_project = self._label(self.card, '', fg=HUD['tertiary'], font=self._f_small)
        self.lbl_project.pack(fill='x', pady=(px(4), 0))
        actions = tk.Frame(self.card, bg=HUD['bg'])
        actions.pack(fill='x', pady=(px(10), 0))
        common = dict(height=26, padx=12, font=self._f_small, surface=HUD['bg'])
        self.btn_pause = PillButton(actions, self.theme, text='暂停 AI', variant='secondary',
                                    command=self._do_pause, **common)
        self.btn_pause.pack(side='left', padx=(0, px(6)))
        self.btn_console = PillButton(actions, self.theme, text='打开控制台', variant='secondary',
                                      command=self._do_console, **common)
        self.btn_console.pack(side='left')
        if 'toggle_pause' not in self.actions:
            self.btn_pause.pack_forget()
        if 'show_console' not in self.actions:
            self.btn_console.pack_forget()
        if not self.actions:
            actions.pack_forget()
        # Fixed card width keeps wrapping predictable.
        self._card_spacer = tk.Frame(self.card, bg=HUD['bg'], width=px(CARD_WIDTH - 28), height=0)
        self._card_spacer.pack()

    # -------------------------------------------------------- project board
    def _build_board(self, frame):
        import tkinter as tk
        px = self.theme.px
        self.board_box = tk.Frame(frame, bg=HUD['bg'])
        self.board_box.pack(fill='x', pady=(px(10), 0))
        tk.Frame(self.board_box, bg=HUD['separator'], height=1).pack(fill='x', pady=(0, px(8)))
        # The header is the collapse toggle, so it is deliberately not a drag handle.
        self.lbl_board_head = self._label(self.board_box, '\u25be 项目进展', fg=HUD['muted'],
                                          font=self._f_label, cursor='hand2')
        self.lbl_board_head.pack(fill='x')
        self.lbl_board_head.bind('<Button-1>', lambda _e: self._toggle_board())
        self.board_rows_box = tk.Frame(self.board_box, bg=HUD['bg'])
        self.board_rows_box.pack(fill='x')
        self.board_rows_box.grid_columnconfigure(0, weight=1)
        self.lbl_more = self._label(self.board_box, '', fg='#0a84ff', font=self._f_small, cursor='hand2')
        self.lbl_more.bind('<Button-1>', lambda _e: self._toggle_show_all())
        self.lbl_board_foot = self._label(self.board_box, '', fg=HUD['tertiary'], font=self._f_small,
                                          wraplength=px(CARD_WIDTH - 28))
        self.lbl_guard = self._label(self.board_box, '', fg=HUD['tertiary'], font=self._f_small,
                                     wraplength=px(CARD_WIDTH - 28))

    def _toggle_board(self):
        self._board_open = not self._board_open
        self._refresh_board()
        self._save_prefs()

    def _toggle_show_all(self):
        self._show_all = not self._show_all
        if self._board_data is not None:
            self._paint_board(self._board_data)
        self._save_prefs()

    def _make_row(self):
        import tkinter as tk
        from ui_kit import Dot
        px = self.theme.px
        f = tk.Frame(self.board_rows_box, bg=HUD['bg'])
        dot = Dot(f, self.theme, HUD['tertiary'], size=7, surface=HUD['bg'])
        dot.grid(row=0, column=0, sticky='w', padx=(0, px(8)))
        title = self._label(f, font=self._f_label)
        title.grid(row=0, column=1, sticky='w')
        right = self._label(f, fg=HUD['tertiary'], font=self._f_small, anchor='e')
        right.grid(row=0, column=2, sticky='e', padx=(px(8), 0))
        sub = self._label(f, fg=HUD['muted'], font=self._f_small)
        sub.grid(row=1, column=1, columnspan=2, sticky='w', pady=(px(1), 0))
        h = px(4)
        bar = tk.Canvas(f, width=self._bar_w + h, height=h, bg=HUD['bg'], highlightthickness=0, bd=0)
        bar.create_line(h // 2, h // 2, self._bar_w, h // 2, fill=HUD['track'], width=h, capstyle='round')
        fill = bar.create_line(0, h // 2, 0, h // 2, fill=HUD['tertiary'], width=h,
                               capstyle='round', state='hidden')
        bar.grid(row=2, column=1, columnspan=2, sticky='w', pady=(px(4), 0))
        f.grid_columnconfigure(1, weight=1)
        for widget in (f, dot, title, right, sub, bar):
            self._draggable(widget)
        row = {'frame': f, 'dot': dot, 'oval': None, 'title': title,
               'right': right, 'sub': sub, 'bar': bar, 'fill': fill}
        self._board_rows.append(row)
        return row

    def _fill_row(self, w, r):
        colour = board_colour(r.get('colour'))
        w['dot'].set_color(colour)
        w['title'].config(text=r['title'])
        w['right'].config(text='  '.join(x for x in (r.get('badges'), r['right'], r['age']) if x))
        w['sub'].config(text=r['sub'], fg=board_colour(r.get('sub_colour')) if r.get('sub_colour') else HUD['muted'])
        progress = r.get('progress')
        mid = int(float(w['bar'].cget('height'))) // 2
        if progress is None:
            w['bar'].coords(w['fill'], 0, mid, 0, mid)
            w['bar'].itemconfigure(w['fill'], state='hidden')
        else:
            width = max(2, int(self._bar_w * min(1.0, max(0.0, progress))))
            w['bar'].coords(w['fill'], mid, mid, width, mid)
            w['bar'].itemconfigure(w['fill'], fill=colour, state='normal')

    def _refresh_board(self):
        """Repaint the project section. Never lets an error reach the status loop."""
        if self.window is None or not hasattr(self, 'board_box'):
            return
        try:
            module = board_module()
            if module is None:
                raise RuntimeError(_BOARD['error'] or 'project_board 不可用')
            data = module.board(limit=BOARD_LIMIT)
            self._paint_board(data, _BOARD['error'])
        except Exception as exc:
            try:
                self.lbl_board_head.config(text='\u25be 项目进展')
                self.lbl_board_foot.config(text='项目进展读取失败：%s' % str(exc)[:80], fg='#ff453a')
                self.lbl_board_foot.pack(fill='x', pady=(self.theme.px(6), 0))
            except Exception:
                pass

    def _paint_board(self, data, load_error=None):
        px = self.theme.px
        self._board_data = data
        arrow = '\u25be ' if self._board_open else '\u25b8 '
        self.lbl_board_head.config(text=arrow + (data.get('header') or '项目进展'))
        rows = (data.get('rows') or []) if self._board_open else []
        extra = 0
        if rows and not self._show_all and len(rows) > ROWS_COLLAPSED:
            extra = len(rows) - ROWS_COLLAPSED
            rows = rows[:ROWS_COLLAPSED]
        while len(self._board_rows) < len(rows):
            self._make_row()
        for i, w in enumerate(self._board_rows):
            if i < len(rows):
                self._fill_row(w, rows[i])
                w['frame'].grid(row=i, column=0, sticky='we', pady=(px(9), 0))
            else:
                w['frame'].grid_remove()
        notes = []
        if self._board_open:
            if data.get('error'):
                notes.append(data['error'])
            if data.get('hidden'):
                notes.append('+%d 个项目未显示（休眠或超出行数）' % data['hidden'])
        if load_error:
            notes.append(load_error)
        self.lbl_board_foot.config(text='\n'.join(notes),
                                   fg='#ff453a' if (load_error or data.get('error')) else HUD['tertiary'])
        guard = data.get('guard') if self._board_open else None
        self.lbl_guard.config(text=guard['text'] if guard else '',
                              fg=board_colour(guard['colour']) if guard else HUD['tertiary'])
        more = ''
        if self._board_open and extra:
            more = '显示全部（另 %d 个）' % extra
        elif self._board_open and self._show_all and len(data.get('rows') or []) > ROWS_COLLAPSED:
            more = '只看前 %d 个' % ROWS_COLLAPSED
        self.lbl_more.config(text=more)
        for label in (self.lbl_more, self.lbl_board_foot, self.lbl_guard):
            label.pack_forget()
        for label in (self.lbl_more, self.lbl_board_foot, self.lbl_guard):
            if label.cget('text'):
                label.pack(fill='x', pady=(px(6), 0))
        self._paint_mini_badge()

    # ---------------------------------------------------------- layout/mode
    def set_mode(self, mode):
        if mode not in ('mini', 'card') or mode == self._mode and self.window is not None and self._mapped_mode == mode:
            return
        self._mode = mode
        if self.window is not None:
            self._relayout_keep_anchor()
        self._save_prefs()

    def _layout(self):
        self.mini.pack_forget()
        self.card.pack_forget()
        (self.mini if self._mode == 'mini' else self.card).pack(fill='both', expand=True)
        self._mapped_mode = self._mode
        if self._mode == 'card':
            self._refresh_board()

    def _relayout_keep_anchor(self):
        """Switch size without the window jumping: keep the edge it sits against."""
        win = self.window
        try:
            x, y, w0 = win.winfo_x(), win.winfo_y(), win.winfo_width()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        except Exception:
            self._layout()
            return
        right_anchored = x + w0 / 2 > sw / 2
        self._layout()
        win.update_idletasks()
        w1, h1 = win.winfo_reqwidth(), win.winfo_reqheight()
        nx = x + w0 - w1 if right_anchored else x
        nx = max(0, min(nx, sw - w1))
        ny = max(0, min(y, sh - h1))
        win.geometry('+%d+%d' % (nx, ny))

    def _initial_position(self, win):
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        if self._position:
            x, y = self._position
            return (max(0, min(x, sw - w)), max(0, min(y, sh - min(h, 120))))
        return self._default_position(win)

    def _default_position(self, win):
        try:
            win.update_idletasks()
            width = win.winfo_screenwidth()
            return (max(10, width - win.winfo_reqwidth() - self.theme.px(24)), self.theme.px(40))
        except Exception:
            return (60, 40)

    def _round_corners(self):
        """Win11: native rounded corners + hairline border on the frameless window."""
        import sys
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            self.window.update_idletasks()
            hwnd = int(self.window.wm_frame(), 16)
            dwm = ctypes.windll.dwmapi
            pref = ctypes.c_int(2)                        # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            r, g, b = (int(HUD['border'][i:i + 2], 16) for i in (1, 3, 5))
            colour = ctypes.c_int(r | (g << 8) | (b << 16))
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(colour), 4)
        except Exception:
            pass

    # ----------------------------------------------------------- menu
    def _build_menu(self):
        import tkinter as tk
        m = tk.Menu(self.window, tearoff=0, bg=HUD['raised'], fg=HUD['text'],
                    activebackground='#0a84ff', activeforeground='#ffffff', bd=0,
                    font=self._f_body)
        self._menu_mode = tk.StringVar(value=self._mode)
        self._menu_board = tk.BooleanVar(value=self._board_open)
        self._menu_snap = tk.BooleanVar(value=self._snap)
        self._menu_alpha = tk.DoubleVar(value=self._idle_alpha)
        m.add_radiobutton(label='迷你胶囊', value='mini', variable=self._menu_mode,
                          command=lambda: self.set_mode('mini'))
        m.add_radiobutton(label='状态卡片', value='card', variable=self._menu_mode,
                          command=lambda: self.set_mode('card'))
        m.add_checkbutton(label='显示项目进展', variable=self._menu_board,
                          command=lambda: self._set_board(self._menu_board.get()))
        m.add_separator()
        alpha = tk.Menu(m, tearoff=0, bg=HUD['raised'], fg=HUD['text'],
                        activebackground='#0a84ff', activeforeground='#ffffff', font=self._f_body)
        for value, text in ((1.0, '不淡出'), (0.82, '82%'), (0.65, '65%'), (0.45, '45%')):
            alpha.add_radiobutton(label=text, value=value, variable=self._menu_alpha,
                                  command=lambda v=value: self._set_idle_alpha(v))
        m.add_cascade(label='闲置时透明度', menu=alpha)
        m.add_checkbutton(label='靠近屏幕边缘时吸附', variable=self._menu_snap,
                          command=lambda: self._set_snap(self._menu_snap.get()))
        m.add_command(label='移回右上角', command=self._reset_position)
        if 'show_console' in self.actions:
            m.add_separator()
            m.add_command(label='打开控制台', command=self._do_console)
        m.add_separator()
        m.add_command(label='关闭悬浮窗', command=self.close)
        self.menu = m

    def _show_menu(self, event):
        if self.window is None:
            return
        self._menu_mode.set(self._mode)
        self._menu_board.set(self._board_open)
        self._menu_snap.set(self._snap)
        self._menu_alpha.set(self._idle_alpha)
        if event is not None:
            x, y = event.x_root, event.y_root
        else:
            x, y = self.window.winfo_pointerxy()
        try:
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()

    def _set_board(self, value):
        self._board_open = bool(value)
        if self._mode != 'card' and self._board_open:
            self.set_mode('card')
        self._refresh_board()
        self._save_prefs()

    def _set_idle_alpha(self, value):
        self._idle_alpha = float(value)
        self._save_prefs()

    def _set_snap(self, value):
        self._snap = bool(value)
        self._save_prefs()

    def _reset_position(self):
        if self.window is not None:
            self.window.geometry('+%d+%d' % self._default_position(self.window))
            self._save_prefs()

    # ----------------------------------------------------------- actions
    def _do_pause(self):
        callback = self.actions.get('toggle_pause')
        if callback:
            callback()
            self._ticks_since_action = 0
            if self.window is not None:
                self.window.after(50, self._tick_once)

    def _do_console(self):
        callback = self.actions.get('show_console')
        if callback:
            callback()

    def close(self):
        for attr in ('_after_id', '_anim_id', '_hover_id'):
            ident = getattr(self, attr, None)
            if ident is not None and self.window is not None:
                try:
                    self.window.after_cancel(ident)
                except Exception:
                    pass
            setattr(self, attr, None)
        if self.window is not None:
            self._save_prefs()
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
            callback = self.actions.get('on_close')
            if callback:
                try:
                    callback()
                except Exception:
                    pass
        self.window = None

    # --------------------------------------------------------------- drag
    def _draggable(self, widget):
        widget.bind('<Button-1>', self._press, add='+')
        widget.bind('<B1-Motion>', self._drag_move, add='+')
        widget.bind('<ButtonRelease-1>', self._release, add='+')

    def _press(self, event):
        self._drag = (event.x_root, event.y_root)
        self._drag_moved = False

    def _drag_move(self, event):
        if self.window is None:
            return
        dx = event.x_root - self._drag[0]
        dy = event.y_root - self._drag[1]
        if not self._drag_moved and abs(dx) + abs(dy) < 3:
            return
        self._drag_moved = True
        self._drag = (event.x_root, event.y_root)
        self.window.geometry('+%d+%d' % (self.window.winfo_x() + dx,
                                         self.window.winfo_y() + dy))

    def _release(self, _event):
        if self.window is None:
            return
        if not self._drag_moved:
            if self._mode == 'mini':          # a plain click on the capsule opens the card
                self.set_mode('card')
            return
        self._drag_moved = False
        if self._snap:
            win = self.window
            x, y, w, h = win.winfo_x(), win.winfo_y(), win.winfo_width(), win.winfo_height()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            margin, d = self.theme.px(10), self.theme.px(SNAP_DISTANCE)
            if x < d + margin:
                x = margin
            elif sw - (x + w) < d + margin:
                x = sw - w - margin
            if y < d + margin:
                y = margin
            elif sh - (y + h) < d + margin + self.theme.px(40):     # leave the taskbar alone
                y = sh - h - margin - self.theme.px(40)
            win.geometry('+%d+%d' % (x, y))
        self._save_prefs()

    # --------------------------------------------------------- hover/alpha
    def _hover_loop(self):
        """Fade in on hover / attention, out to the idle opacity otherwise."""
        win = self.window
        if win is None:
            return
        try:
            px, py = win.winfo_pointerxy()
            x, y = win.winfo_rootx(), win.winfo_rooty()
            inside = x <= px < x + win.winfo_width() and y <= py < y + win.winfo_height()
            target = 1.0 if inside or self._last_state in ATTENTION else self._idle_alpha
            if abs(target - self._alpha) > 0.01:
                self._alpha += (target - self._alpha) * (0.45 if target > self._alpha else 0.18)
                if abs(target - self._alpha) < 0.02:
                    self._alpha = target
                self._render_alpha(self._alpha)
        except Exception:
            pass
        self._hover_id = win.after(60, self._hover_loop)

    def _render_alpha(self, value):
        try:
            self.window.attributes('-alpha', max(0.2, min(1.0, value)))
        except Exception:
            pass

    # --------------------------------------------------------------- loop
    def _tick_once(self):
        try:
            snapshot, age, running = self.poll()
            self.apply(classify(snapshot, age, running=running), snapshot)
        except Exception:
            pass

    def _tick(self):
        if self.window is None:
            return
        try:
            snapshot, age, running = self.poll()
            verdict = classify(snapshot, age, running=running)
            self.apply(verdict, snapshot)
        except Exception as exc:                      # never kill the loop
            try:
                self.lbl_head.config(text='状态读取失败', fg='#ff453a')
                self.lbl_detail.config(text=str(exc)[:90])
                self.mini_text.config(text='状态读取失败')
            except Exception:
                pass
        if self._ticks % BOARD_EVERY == 0:
            self._refresh_board()                     # guards its own errors
        self._ticks += 1
        if self.window is not None:
            self._after_id = self.window.after(1000, self._tick)

    def _animate(self):
        """Breathing halo while something is running or needs attention."""
        self._anim_id = None
        if self.window is None or self._last_state not in ANIMATED:
            for dot in (self.mini_dot, self.card_dot):
                dot.phase = None
                dot.draw()
            return
        speed = 0.05 if self._last_state == STATE_RUNNING else 0.035
        self._phase = (self._phase + speed) % 1.0
        for dot in (self.mini_dot, self.card_dot):
            dot.phase = self._phase
            dot.draw()
        self._anim_id = self.window.after(50, self._animate)

    def _paint_mini_badge(self):
        """Capsule badge: the one cross-project thing worth interrupting for."""
        if not hasattr(self, 'mini_badge'):
            return
        data = self._board_data or {}
        counts = data.get('counts') or {}
        guard = data.get('guard') or {}
        text, colour = '', None
        if board_colour(guard.get('colour')) == '#ff453a':
            text, colour = '守护异常', '#ff453a'
        elif counts.get('waiting'):
            text, colour = '等你 %d' % counts['waiting'], '#ffd60a'
        if text:
            self.mini_badge.config(text=text, bg=colour)
            self.mini_badge.pack(side='left', padx=(self.theme.px(8), 0), before=self.mini_expand)
        else:
            self.mini_badge.pack_forget()

    def apply(self, verdict, snapshot=None):
        """Paint a verdict. Split out so tests can drive it headlessly."""
        state = verdict['state']
        colour = STATE_COLOURS.get(state, PRESENTATION[state][0])
        self._verdict = verdict
        for dot in (self.mini_dot, self.card_dot):
            if dot.colour != colour:
                dot.colour = colour
                dot.draw()
        short = PRESENTATION[state][2]
        self.lbl_state.config(text=short, fg=colour)
        age = verdict.get('age')
        self.lbl_age.config(text=_fmt_duration(age) if age is not None and state not in (STATE_RUNNING, STATE_LONG) else '')
        headline = verdict['headline']
        if headline.startswith(short + '  ·  '):     # the state label already says it
            headline = headline[len(short) + 5:]
        self.lbl_head.config(text=headline)
        self.lbl_detail.config(text=verdict['detail'])
        self.mini_text.config(text=mini_text(verdict),
                              fg=colour if state in ATTENTION else HUD['text'])
        project = active_project()
        self.lbl_project.config(text=('项目  ·  %s' % project['name']) if project else '未指派项目')
        if hasattr(self, 'btn_pause'):
            paused = bool(snapshot and snapshot.get('paused'))
            self.btn_pause.config(text='恢复 AI' if paused else '暂停 AI',
                                  variant='warning' if paused else 'secondary',
                                  state='disabled' if state == STATE_OFFLINE else 'normal')
        previous, self._last_state = self._last_state, state
        if state in ANIMATED and previous not in ANIMATED and self._anim_id is None:
            self._animate()
