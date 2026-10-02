"""
OpenBridge GUI - 本地 MCP 桥接控制台（Apple 风格界面，浅色/深色跟随系统）
提供直观易用的图形界面：
- 一键启动/停止本地 MCP 服务与 Cloudflare HTTP/2 极速隧道
- 一键复制结构化开工提示词（含公网 URL、工具清单、操作纪律、准备就绪引导）
- 项目工作区快速选择与打开
- 实时工具调用与终端执行日志流展示
"""

import os
import sys
import time
import webbrowser
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# 确保工作目录与模块导入路径正确
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# 启用高 DPI 缩放支持（Windows）
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# 导入核心服务逻辑
from bridge import BridgeService, DEFAULT_PROMPT_TEMPLATE, DEFAULT_WORKSPACE, find_cloudflared
import local_cu_preferences
from floating_status import FloatingStatusWindow
from ui_kit import (Theme, RoundBox, PillButton, Switch, Dot, Segmented, style_window_chrome, Anim,
                    fade_in_window, fade_in_text)

class OpenBridgeApp:
    # Class-level default: _drain_ui_events can run against a partially built
    # app (tests construct one with object.__new__), so never assume __init__ ran.
    _activity_rendered_at = 0.0

    def __init__(self, root):
        self.root = root
        self.root.title("OpenBridge")
        self.root.geometry('1080x960')
        self.root.minsize(980, 800)
        self._ui_events = queue.SimpleQueue()
        self._ui_logs = queue.Queue(maxsize=1500)
        self._dropped_logs = 0
        self._closing = False

        # 状态变量
        self.workspace_var = tk.StringVar(value=DEFAULT_WORKSPACE)
        self.mcp_url_var = tk.StringVar(value="启动后生成安全的公网 MCP 地址")
        self.status_var = tk.StringVar(value="●  未启动")
        self.auto_scroll_var = tk.BooleanVar(value=True)
        # Live "is the AI touching my machine right now" indicator text.
        self.activity_var = tk.StringVar(value='AI 操作：服务未启动')
        self._activity_rendered_at = 0.0
        self._floating = None
        # Only a preference previously approved in this local GUI can enable at launch.
        remembered = local_cu_preferences.load()
        self.cu_enabled = tk.BooleanVar(value=remembered)
        self.cu_startup = tk.BooleanVar(value=remembered)
        # Local-only: skip the per-request prompt for windows matching game_trust.json.
        self.game_trust = tk.BooleanVar(value=local_cu_preferences.load_game_trust())
        self._cu_busy = False

        self.service = None
        self.current_status = "STOPPED"
        self.current_url = ""

        self._init_theme()
        self._build_ui()
        self.set_status('STOPPED')
        self.root.after(50, self._drain_ui_events)

        # 窗口关闭安全拦截
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _init_theme(self):
        # Apple-style palette (light/dark follows Windows; OPENBRIDGE_THEME overrides).
        self.theme = Theme(self.root)
        c = self.theme.c
        self.c_bg, self.c_card, self.c_field = c['bg'], c['card'], c['field']
        self.c_primary, self.c_success, self.c_danger = c['accent'], c['green_text'], c['red_text']
        self.c_warning, self.c_info = c['orange_text'], c['blue_text']
        self.c_text, self.c_muted, self.c_border = c['text'], c['muted'], c['separator']
        self.c_input = c['field']
        t = self.theme
        self.f_title, self.f_section = t.f_display, t.f_section
        self.f_body, self.f_body_b, self.f_caption = t.f_body, t.f_body_b, t.f_caption
        self.f_btn, self.f_mono = t.f_btn, t.f_mono
        self.px = t.px
        self.root.configure(bg=self.c_bg)
        self.root.option_add('*Font', self.f_body)
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=self.c_card, foreground=self.c_text,
                        bordercolor=self.c_card, lightcolor=self.c_card,
                        darkcolor=self.c_card, troughcolor=self.c_field)
        # Notebook is only a page container; navigation is the segmented control.
        style.layout('Flat.TNotebook', [('Notebook.client', {'sticky': 'nswe'})])
        style.layout('Flat.TNotebook.Tab', [])
        style.configure('Flat.TNotebook', background=self.c_card, borderwidth=0, padding=0,
                        bordercolor=self.c_card, lightcolor=self.c_card, darkcolor=self.c_card)
        # Thin overlay-like scrollbar: no arrows, rounded-looking thumb on the field color.
        style.layout('Thin.Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {
            'sticky': 'ns', 'children': [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        thumb = c['tertiary']
        style.configure('Thin.Vertical.TScrollbar', troughcolor=self.c_field, background=thumb,
                        bordercolor=self.c_field, lightcolor=thumb, darkcolor=thumb,
                        arrowsize=self.px(7), gripcount=0, relief='flat', borderwidth=0)
        style.map('Thin.Vertical.TScrollbar', background=[('active', c['muted'])],
                  lightcolor=[('active', c['muted'])], darkcolor=[('active', c['muted'])])

    # ── small builders ────────────────────────────────────────────────
    def _label(self, parent, text='', muted=False, bold=False, kind=None, **kwargs):
        font = {'section': self.f_section, 'caption': self.f_caption,
                'title': self.f_title}.get(kind, self.f_body_b if bold else self.f_body)
        color = self.c_muted if (muted or kind == 'caption') else self.c_text
        return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color, font=font,
                        anchor='w', justify='left', bd=0, **kwargs)

    def _button(self, parent, text, command, primary=False, variant=None, small=False, **kwargs):
        return PillButton(parent, self.theme, text=text, command=command,
                          variant=variant or ('primary' if primary else 'secondary'),
                          font=self.theme.f_btn_small if small else self.f_btn,
                          height=28 if small else 34, padx=12 if small else 18, **kwargs)

    def _card(self, parent, expand=False, pad=(22, 18)):
        border = None if self.theme.dark else '#e8e8ed'
        card = RoundBox(parent, self.theme, fill=self.c_card, radius=16, pad=pad,
                        border=border, expand=expand)
        card.pack(fill='both' if expand else 'x', expand=expand, pady=(0, self.px(14)))
        return card.body

    def _field(self, parent, variable, readonly=False):
        box = RoundBox(parent, self.theme, fill=self.c_field, radius=10, pad=(12, 8))
        entry = tk.Entry(box.body, textvariable=variable, font=self.f_mono, relief='flat', bd=0,
                         highlightthickness=0, bg=self.c_field, fg=self.c_text,
                         readonlybackground=self.c_field, disabledbackground=self.c_field,
                         disabledforeground=self.c_muted, insertbackground=self.c_text,
                         selectbackground=self.theme.c['select'], selectforeground=self.c_text,
                         state='readonly' if readonly else 'normal')
        entry.pack(fill='x')
        return box, entry

    def _separator(self, parent, pady=(12, 12)):
        tk.Frame(parent, bg=self.c_border, height=1).pack(fill='x', pady=pady)

    def _setting_row(self, parent, title, caption, variable, command):
        row = tk.Frame(parent, bg=parent.cget('bg'))
        row.pack(fill='x')
        text = tk.Frame(row, bg=row.cget('bg'))
        text.pack(side='left', fill='x', expand=True)
        self._label(text, title).pack(anchor='w')
        self._label(text, caption, kind='caption').pack(anchor='w', pady=(self.px(2), 0))
        switch = Switch(row, self.theme, variable, command)
        switch.pack(side='right', padx=(self.px(12), 0))
        return switch

    def _text_area(self, parent, mono=True):
        box = RoundBox(parent, self.theme, fill=self.c_field, radius=12, pad=(4, 4), expand=True)
        box.pack(fill='both', expand=True)
        text = ScrolledText(box.body, wrap='word', font=self.theme.f_mono_small if mono else self.f_body,
            bg=self.c_field, fg=self.c_text, insertbackground=self.c_text,
            selectbackground=self.theme.c['select'], selectforeground=self.c_text,
            relief='flat', bd=0, padx=self.px(12), pady=self.px(10), height=6,
            highlightthickness=0, spacing1=self.px(1), spacing3=self.px(1))
        text.frame.configure(bg=self.c_field)
        text.pack(fill='both', expand=True)
        text.vbar.pack_forget()
        bar = ttk.Scrollbar(text.frame, orient='vertical', command=text.yview,
                            style='Thin.Vertical.TScrollbar')
        bar.pack(side='right', fill='y', pady=self.px(6), padx=(0, self.px(4)))
        text.configure(yscrollcommand=bar.set)
        return text

    def _section_header(self, parent, title, caption=None):
        row = tk.Frame(parent, bg=parent.cget('bg'))
        row.pack(fill='x')
        left = tk.Frame(row, bg=row.cget('bg'))
        left.pack(side='left', fill='x', expand=True)
        self._label(left, title, kind='section').pack(anchor='w')
        if caption:
            self._label(left, caption, kind='caption').pack(anchor='w', pady=(self.px(3), 0))
        return row

    # ── layout ────────────────────────────────────────────────────────
    def _build_ui(self):
        px = self.px
        # Header: large title + live status pill (always visible).
        header = tk.Frame(self.root, bg=self.c_bg, padx=px(30), pady=px(20))
        header.pack(fill='x')
        branding = tk.Frame(header, bg=self.c_bg)
        branding.pack(side='left')
        self._label(branding, 'OpenBridge', kind='title').pack(anchor='w')
        self._label(branding, '本地 MCP 桥接控制台  ·  让远程 AI 安全地使用这台电脑',
                    kind='caption').pack(anchor='w', pady=(px(2), 0))
        self.lbl_status = PillButton(header, self.theme, textvariable=self.status_var,
                                     variant='secondary', interactive=False, height=30,
                                     padx=14, font=self.theme.f_btn_small)
        self.lbl_status.pack(side='right')

        main = tk.Frame(self.root, bg=self.c_bg, padx=px(30))
        main.pack(fill='both', expand=True)

        # ① Connection
        connection = self._card(main)
        head = self._section_header(connection, '公网连接', '自动探测 · 故障自恢复 · 地址包含密钥，仅分享给可信的 AI')
        actions = tk.Frame(connection, bg=self.c_card)
        actions.pack(fill='x', pady=(px(14), px(12)))
        self.btn_toggle_bridge = self._button(actions, '启动 Bridge', self.toggle_bridge,
                                              primary=True, minwidth=128)
        self.btn_toggle_bridge.pack(side='left', padx=(0, px(10)))
        self.btn_copy_prompt = self._button(actions, '复制连接提示词', self.copy_prompt_to_clipboard,
                                            primary=True)
        self.btn_copy_prompt.pack(side='left', padx=(0, px(10)))
        self.btn_repair = self._button(actions, '修复连接', self.repair_connection, state='disabled')
        self.btn_repair.pack(side='left')
        self._button(actions, '重置密钥', self.reset_mcp_url, variant='plain').pack(side='right')
        self._button(actions, '健康检测', self.open_health_test, variant='plain').pack(side='right')
        row = tk.Frame(connection, bg=self.c_card)
        row.pack(fill='x')
        url_box, self.url_entry = self._field(row, self.mcp_url_var, readonly=True)
        url_box.pack(side='left', fill='x', expand=True, padx=(0, px(10)))
        self.btn_copy_url = self._button(row, '复制 URL', self.copy_url)
        self.btn_copy_url.pack(side='left')
        self.connection_detail = tk.StringVar(value='尚未建立公网连接')
        self._label(connection, textvariable=self.connection_detail,
                    kind='caption').pack(anchor='w', pady=(px(10), 0))
        self.btn_copy_url.config(state='disabled')
        self.btn_copy_prompt.config(state='disabled')

        # ② Workspace (one compact row)
        workspace = self._card(main, pad=(22, 14))
        row = tk.Frame(workspace, bg=self.c_card)
        row.pack(fill='x')
        title = tk.Frame(row, bg=self.c_card)
        title.pack(side='left', padx=(0, px(18)))
        self._label(title, '工作区', kind='section').pack(anchor='w')
        self._label(title, '文件工具的操作范围', kind='caption').pack(anchor='w', pady=(px(2), 0))
        ws_box, self.ws_entry = self._field(row, self.workspace_var)
        ws_box.pack(side='left', fill='x', expand=True, padx=(0, px(10)))
        self.btn_browse = self._button(row, '选择…', self.browse_workspace)
        self.btn_browse.pack(side='left', padx=(0, px(8)))
        self._button(row, '打开', self.open_workspace_folder).pack(side='left')

        # ③ Computer Use
        capabilities = self._card(main)
        head = self._section_header(capabilities, 'Computer Use',
                                    '桌面 · 浏览器 · Blender · 游戏输入    F8 紧急松键')
        self.btn_computer_use = self._button(head, '开启 Computer Use', self.toggle_computer_use)
        self.btn_computer_use.pack(side='right')
        self._separator(capabilities, pady=(px(14), px(12)))
        settings = tk.Frame(capabilities, bg=self.c_card)
        settings.pack(fill='x')
        settings.columnconfigure(0, weight=1, uniform='set')
        settings.columnconfigure(2, weight=1, uniform='set')
        left = tk.Frame(settings, bg=self.c_card)
        left.grid(row=0, column=0, sticky='nsew')
        tk.Frame(settings, bg=self.c_border, width=1).grid(row=0, column=1, sticky='ns', padx=px(18))
        right = tk.Frame(settings, bg=self.c_card)
        right.grid(row=0, column=2, sticky='nsew')
        self.sw_startup = self._setting_row(left, '启动时默认开启',
            '记住本机授权，下次打开自动启用', self.cu_startup, self.remember_computer_use)
        self.sw_trust = self._setting_row(right, '自动批准受信游戏窗口',
            '如 Minecraft · 规则见 game_trust.json', self.game_trust, self.toggle_game_trust)
        # Live activity strip: answers "is the AI working right now?" at a glance.
        strip = RoundBox(capabilities, self.theme, fill=self.c_field, radius=12, pad=(14, 10))
        strip.pack(fill='x', pady=(px(14), 0))
        self.activity_dot = Dot(strip.body, self.theme, self.theme.c['tertiary'], halo=5)
        self.activity_dot.pack(side='left', padx=(0, px(5)))
        self.lbl_activity = tk.Label(strip.body, textvariable=self.activity_var,
                                     bg=self.c_field, fg=self.c_muted, font=self.f_body,
                                     anchor='w', justify='left')
        self.lbl_activity.pack(side='left', fill='x', expand=True)
        self.btn_pause_cu = self._button(strip.body, '暂停 AI 操作', self.toggle_cu_pause,
                                         state='disabled', small=True, surface=self.c_field)
        self.btn_pause_cu.pack(side='right')
        self.btn_floating = self._button(strip.body, 'AI 状态悬浮窗', self.toggle_floating_status,
                                         small=True, surface=self.c_field)
        self.btn_floating.pack(side='right', padx=(0, px(8)))

        # ④ Prompt / log, switched by a segmented control
        content = self._card(main, expand=True, pad=(18, 16))
        top = tk.Frame(content, bg=self.c_card)
        top.pack(fill='x', pady=(0, px(12)))
        # 选中滑块在两段之间滑动，文字颜色随之过渡（ui_kit.Segmented）
        self.segment = Segmented(top, self.theme, ('连接提示词', '运行日志'),
                                 command=lambda i: self.notebook.select(i),
                                 font=self.theme.f_btn_small, height=34, padx=18, minwidth=96,
                                 radius=10, inset=3)
        self.segment.pack(side='left')
        self._tab_index = None
        self._tab_tools = tk.Frame(top, bg=self.c_card)
        self._tab_tools.pack(side='right')
        self.notebook = ttk.Notebook(content, style='Flat.TNotebook')
        self.notebook.pack(fill='both', expand=True)
        self.notebook.bind('<<NotebookTabChanged>>', self._sync_tabs)
        tab_prompt = tk.Frame(self.notebook, bg=self.c_card)
        tab_log = tk.Frame(self.notebook, bg=self.c_card)
        self.notebook.add(tab_prompt, text='连接提示词')
        self.notebook.add(tab_log, text='运行日志')
        # Tab-specific tools live in the header row, next to the segmented control.
        self._prompt_tools = tk.Frame(self._tab_tools, bg=self.c_card)
        self._label(self._prompt_tools, '复制给新对话即可独立初始化', kind='caption').pack(side='left', padx=(0, px(12)))
        self._button(self._prompt_tools, '恢复默认', self.reset_prompt_template, small=True).pack(side='left')
        self._log_tools = tk.Frame(self._tab_tools, bg=self.c_card)
        self._label(self._log_tools, '自动滚动', kind='caption').pack(side='left', padx=(0, px(8)))
        Switch(self._log_tools, self.theme, self.auto_scroll_var).pack(side='left', padx=(0, px(14)))
        self._button(self._log_tools, '日志目录', self.open_connection_logs, small=True).pack(side='left', padx=(0, px(8)))
        self._button(self._log_tools, '清空', self.clear_logs, small=True).pack(side='left')
        self.txt_prompt = self._text_area(tab_prompt, mono=False)
        self.txt_log = self._text_area(tab_log)
        c = self.theme.c
        for tag, color in [('tool', c['log_tool']), ('exec', c['log_exec']),
                           ('success', c['log_success']), ('warning', c['log_warning']),
                           ('time', c['log_time']), ('cu', c['log_cu'])]:
            self.txt_log.tag_config(tag, foreground=color)

        # Footer: hint on the left, transient toast floats in the centre.
        footer = tk.Frame(self.root, bg=self.c_bg, height=px(46))
        footer.pack(fill='x', padx=px(30))
        footer.pack_propagate(False)
        self._label(footer, '共享本机状态  ·  同一时间仅让一个对话执行操作',
                    kind='caption').pack(side='right')
        self._footer = footer
        self.lbl_copy_toast = PillButton(footer, self.theme, text='', variant='toast',
                                         interactive=False, height=30, padx=16,
                                         font=self.theme.f_btn_small)
        self._toast_after = None
        self._sync_tabs()
        self.update_prompt_preview()
        self._initial_check()
        style_window_chrome(self.root, self.theme)

    def _sync_tabs(self, _event=None):
        try:
            selected = self.notebook.index('current')
        except tk.TclError:
            selected = 0
        previous = getattr(self, '_tab_index', None)
        self._tab_index = selected
        segment = getattr(self, 'segment', None)
        if segment is not None:
            segment.select(selected, animate=previous is not None)
        self._prompt_tools.pack_forget()
        self._log_tools.pack_forget()
        (self._prompt_tools if selected == 0 else self._log_tools).pack(side='right')
        if previous is not None and previous != selected:
            # 新页面的文字从底色淡入，和滑块同步
            fade_in_text(self.txt_prompt if selected == 0 else self.txt_log, self.c_field)

    def repair_connection(self):
        if not self.service or not self.service.is_running:
            return
        if messagebox.askyesno('修复公网连接',
            '将只重建公网隧道，保留本地 MCP 和 Computer Use 权限。\n\n'
            'Quick Tunnel 域名可能变化；恢复后请重新复制提示词给 AI。继续？'):
            self.service.request_reconnect()
            self.notebook.select(1)

    def open_connection_logs(self):
        path = os.path.join(script_dir, 'logs')
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def _post_ui(self, callback, *args):
        # Workers never call Tk. Keep verbose cloudflared output off the critical UI queue.
        if callback in (self.append_log, self._deliver_log):
            try:
                self._ui_logs.put_nowait((callback, args))
            except queue.Full:
                self._dropped_logs += 1
        else:
            self._ui_events.put((callback, args))

    def _drain_ui_events(self):
        for events, limit in ((self._ui_events, 100), (self._ui_logs, 80)):
            for _ in range(limit):
                try:
                    callback, args = events.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback(*args)
                except Exception:
                    self.root.report_callback_exception(*sys.exc_info())
        self._refresh_activity()
        if self._dropped_logs:
            count, self._dropped_logs = self._dropped_logs, 0
            self.append_log('[日志] 界面省略 %d 条高频消息；连接诊断仍写入磁盘。' % count, 'warning')
        if self.service:
            last = getattr(self.service, 'last_healthy_at', None)
            verified = time.strftime('%H:%M:%S', time.localtime(last)) if last else '尚未通过'
            self.connection_detail.set('最近公网验证：%s  ·  自动恢复：%d 次  ·  域名变化后需重新复制提示词' %
                (verified, getattr(self.service, 'reconnect_count', 0)))
        if self.service and not getattr(self, '_game_consent_busy', False):
            request = self.service.computer_use.pop_game_consent()
            if request:
                self._confirm_game_window(self.service, request)
        self.root.after(50, self._drain_ui_events)

    def _confirm_game_window(self, service, request):
        self._game_consent_busy = True
        window = request['window']
        try:
            approved = messagebox.askyesno('批准限时游戏输入？',
                '仅绑定此窗口（不是操作系统沙箱）：\n%s\nPID: %s · 窗口: %s\n\n'
                '每批输入最多 2 秒；失焦即中止并松键。按 F8 或关闭 Computer Use 可紧急停止。'
                '租约闲置 10 分钟失效，单次批准最多 60 分钟。请先关闭敏感窗口。是否批准？' %
                (window['title'][:160], window['pid'], window['window_id']), parent=self.root)
            def decide():
                try:
                    result = service.computer_use.approve_game_request(request['request_id'], approved)
                    self._post_ui(self.append_log, '[游戏输入] 本机确认结果: ' + str(result))
                except Exception as exc:
                    self._post_ui(self.append_log, '[游戏输入] 确认失效: ' + str(exc))
            threading.Thread(target=decide, daemon=True).start()
        finally:
            self._game_consent_busy = False

    def _initial_check(self):
        cf = find_cloudflared()
        if cf:
            self.append_log(f"[系统自检] 检测到 Cloudflare 客户端: {cf}", "success")
        else:
            self.append_log("[系统自检] 未在标准目录找到 cloudflared.exe，若启动公网穿透可能需要先安装。", "warning")
        self.append_log("[就绪] 点击「启动 Bridge」即可开启服务并自动穿透公网。", "success")

    def browse_workspace(self):
        if self.current_status != "STOPPED":
            messagebox.showinfo("切换工作区", "请先停止 Bridge，再选择工作区。重新启动后必须使用新地址。")
            return
        chosen = filedialog.askdirectory(initialdir=self.workspace_var.get(), title="选择项目工作区文件夹")
        if chosen:
            self.workspace_var.set(os.path.abspath(chosen))
            self.update_prompt_preview()
            self.append_log(f"[*] 工作区已切换为: {chosen}")

    def open_workspace_folder(self):
        path = self.workspace_var.get()
        if os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showwarning("提示", f"路径不存在: {path}")

    def toggle_bridge(self):
        if self.current_status == "STOPPED":
            self.start_bridge()
        else:
            self.stop_bridge()

    def start_bridge(self):
        if self.current_status != "STOPPED" or self.service is not None:
            return
        ws = self.workspace_var.get()
        if not os.path.isdir(ws):
            messagebox.showerror("错误", f"工作区目录无效或不存在：\n{ws}")
            return

        self._generation = getattr(self, "_generation", 0) + 1
        generation = self._generation
        self.current_url = ""
        self.update_prompt_preview()
        self.btn_toggle_bridge.config(state="disabled")
        self.set_status("TUNNELING")
        self.mcp_url_var.set("正在获取 Cloudflare HTTP/2 极速隧道地址...")

        self.service = BridgeService(
            workspace_dir=ws,
            use_tunnel=True,
            enable_desktop=self.cu_enabled.get(),
            enable_browser=self.cu_enabled.get(),
            enable_blender=self.cu_enabled.get(),
            log_callback=lambda value: self._service_event(generation, self.append_log, value),
            url_callback=lambda value: self._service_event(generation, self.set_url, value),
            status_callback=lambda value: self._service_event(generation, self.set_status, value)
        )
        # Apply the locally remembered trust switch before the first broker start.
        self.service.computer_use.game_trust = bool(self.game_trust.get())

        # 异步启动服务
        service = self.service
        def start_service():
            try:
                service.start()
            except Exception as exc:
                service.stop()
                self._service_event(generation, self.append_log, "[启动失败] %s" % exc)
                self._service_event(generation, lambda _: self._startup_failed(), None)
        t = threading.Thread(target=start_service, daemon=True)
        t.start()

    def _refresh_computer_use_button(self):
        self._refresh_activity(force=True)
        blocked = self._cu_busy or self.current_status == "STOPPING"
        # Do not enable between service construction and its initial server start.
        if self.service is not None and not self.service.is_running:
            blocked = True
        self.btn_computer_use.config(
            state="disabled" if blocked else "normal",
            text="关闭 Computer Use" if self.cu_enabled.get() else "开启 Computer Use",
            variant='success' if self.cu_enabled.get() else 'secondary')

    def _activity_monitor(self):
        """Local in-process monitor, or None before the Bridge is built."""
        return getattr(getattr(self.service, 'computer_use', None), 'activity', None)

    def _last_tool_call_age(self):
        """Seconds since ANY tools/call, not just Computer Use ones.

        bridge.py refreshes McpHandler.last_client_activity on every tool call,
        so this catches an assistant that is busy reading files or running
        commands - work the Computer Use monitor never sees. Without it the
        floating window would cry "stuck" during perfectly normal file work.
        """
        try:
            from bridge import McpHandler
            stamp = getattr(McpHandler, 'last_client_activity', None)
            if not stamp:
                return None
            return max(0.0, time.monotonic() - stamp)
        except Exception:
            return None

    def _poll_floating_status(self):
        monitor = self._activity_monitor()
        snapshot = monitor.snapshot() if monitor is not None else None
        running = bool(self.service is not None and self.service.is_running)
        return snapshot, self._last_tool_call_age(), running

    def toggle_floating_status(self):
        """Show/hide the always-on-top indicator."""
        if self._floating is None:
            from floating_status import default_prefs_path
            self._floating = FloatingStatusWindow(
                self.root, self._poll_floating_status,
                actions={'toggle_pause': self._floating_pause,
                         'show_console': self._show_console,
                         'on_close': self._on_floating_closed},
                prefs_path=default_prefs_path())
        shown = self._floating.toggle()
        self._sync_floating_button(shown)
        self.append_log('[悬浮窗] %s' % ('已显示：单击胶囊展开，右键更多选项，可拖动。' if shown else '已关闭。'),
                        'success' if shown else 'info')

    def _sync_floating_button(self, shown):
        self.btn_floating.config(text='关闭悬浮窗' if shown else 'AI 状态悬浮窗',
                                 variant='success' if shown else 'secondary')

    def _on_floating_closed(self):
        # Closed from the floating window itself (✕ or menu): keep the console button honest.
        self.root.after_idle(lambda: self._sync_floating_button(False))

    def _floating_pause(self):
        if self._activity_monitor() is not None:
            self.toggle_cu_pause()

    def _show_console(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _refresh_activity(self, force=False):
        """Poll local state only. Never touches the network or the adapters.

        _drain_ui_events runs every 50 ms; repainting the strip that often is
        wasteful, so throttle to ~5 Hz unless a click needs instant feedback.
        """
        now = time.monotonic()
        if not force and (now - self._activity_rendered_at) < 0.2:
            return
        if not hasattr(self, 'lbl_activity'):
            return  # UI not built yet; nothing to paint.
        self._activity_rendered_at = now
        monitor = self._activity_monitor()
        c = self.theme.c
        if monitor is None:
            self.activity_var.set('AI 操作：服务未启动')
            self.lbl_activity.config(fg=self.c_muted)
            self.activity_dot.set_color(c['tertiary'])
            self.activity_dot.set_pulse(None)
            self.btn_pause_cu.config(state='disabled', text='暂停 AI 操作', variant='secondary')
            return
        snap = monitor.snapshot()
        self.btn_pause_cu.config(
            state='normal',
            text='恢复 AI 操作' if snap['paused'] else '暂停 AI 操作',
            variant='warning' if snap['paused'] else 'secondary')
        if snap['paused']:
            text, color, dot = '已暂停  ·  AI 的操作请求会被拒绝，连接与子进程保留', self.c_warning, c['orange']
        elif snap['busy']:
            queued = '  (+%d 排队)' % (snap['pending'] - 1) if snap['pending'] > 1 else ''
            text = 'AI 正在操作：%s  ·  已用 %.1f 秒%s' % (
                snap['current'], snap['elapsed'], queued)
            color, dot = self.c_text, c['green']
        elif snap['last']:
            last = snap['last']
            text = '空闲  ·  上次 %s %s %.1f 秒  ·  累计成功 %d / 失败 %d' % (
                last['label'], '完成' if last['ok'] else '失败', last['elapsed'],
                snap['counters']['ok'], snap['counters']['error'])
            color, dot = self.c_muted, c['tertiary']
        else:
            text, color, dot = '空闲  ·  尚未执行任何 Computer Use 操作', self.c_muted, c['tertiary']
        if self.activity_var.get() != text:
            self.activity_var.set(text)
        self.lbl_activity.config(fg=color)
        self.activity_dot.set_color(dot)
        self.activity_dot.set_pulse('fast' if (snap['busy'] and not snap['paused']) else None)

    def toggle_cu_pause(self):
        """Local soft pause: refuse new AI actions, keep adapters and tunnel alive."""
        monitor = self._activity_monitor()
        if monitor is None:
            return
        paused = monitor.set_paused(not monitor.paused)
        if paused:
            self.append_log('[Computer Use] 本机已暂停 AI 操作；后续请求会被拒绝，'
                            '子进程与公网连接保留。正在进行的动作不会被强行打断。', 'warning')
            self.show_toast('已暂停 AI 操作 · 点击「恢复 AI 操作」继续')
        else:
            self.append_log('[Computer Use] 本机已恢复 AI 操作。', 'success')
            self.show_toast('已恢复 AI 操作')
        self._refresh_activity(force=True)

    def remember_computer_use(self):
        enabled = self.cu_startup.get()
        if enabled and not messagebox.askyesno("记住本机 Computer Use 授权",
            "以后每次启动此 GUI，将默认授权桌面、独立浏览器、Blender。持有 MCP 地址者均可使用，权限不受工作区限制。"
            "游戏窗口仍需单独确认。取消勾选可撤销启动授权；关闭 Computer Use 也会取消记忆。是否记住？"):
            self.cu_startup.set(False)
            return
        try:
            local_cu_preferences.save(enabled)
        except OSError as exc:
            self.cu_startup.set(local_cu_preferences.load())
            messagebox.showerror("启动授权未保存", str(exc))
            return
        # This preference does not remotely/hot-enable a running service.
        if self.service is None and enabled:
            self.cu_enabled.set(True)
            self._refresh_computer_use_button()
        self.append_log("[Computer Use] 本机启动授权已" + ("记住；正在运行的权限未改变。" if enabled else "撤销；当前权限未改变。"))

    def toggle_game_trust(self):
        enabled = bool(self.game_trust.get())
        if enabled:
            try:
                from game_control import trust
                path = trust.ensure_default()
                rules = trust.load_rules(path)
            except Exception as exc:
                self.game_trust.set(False)
                messagebox.showerror("规则文件不可用", str(exc))
                return
            names = '、'.join(str(r.get('name', '?')) for r in rules) or '（空）'
            if not messagebox.askyesno("自动批准受信游戏窗口",
                "开启后，凡是匹配本机规则文件的窗口（当前规则：%s）在 GameRequestControl 时将不再弹窗，"
                "由本机策略直接签发租约。仍保留：单窗口绑定、失焦中止、F8 紧急松键、租约闲置 10 分钟/最长 60 分钟、"
                "关闭 Computer Use 立即撤权。规则文件位于：\n%s\n\n持有 MCP 地址的任何对话都可利用此自动批准。是否开启？"
                % (names, path)):
                self.game_trust.set(False)
                return
        try:
            local_cu_preferences.save_game_trust(enabled)
        except OSError as exc:
            self.game_trust.set(local_cu_preferences.load_game_trust())
            messagebox.showerror("设置未保存", str(exc))
            return
        service = self.service
        if service is not None:
            def change():
                try:
                    service.computer_use.set_game_trust(enabled)
                except Exception as exc:
                    self._post_ui(self.append_log, "[游戏输入] 切换自动批准失败: " + str(exc))
            threading.Thread(target=change, daemon=True).start()
        else:
            self.append_log("[游戏输入] 受信游戏窗口自动批准已" + ("开启" if enabled else "关闭") + "（启动 Bridge 后生效）")

    def toggle_computer_use(self):
        if self._cu_busy or self.current_status == "STOPPING":
            return
        enable = not self.cu_enabled.get()
        if not enable and self.cu_startup.get():
            try:
                local_cu_preferences.save(False)
                self.cu_startup.set(False)
            except OSError as exc:
                messagebox.showwarning("启动授权撤销失败", "本次仍会停用，但请在关闭前取消启动授权：" + str(exc))
        if enable and not messagebox.askyesno("开启全部 Computer Use",
            "将同时授权桌面、独立浏览器、Blender 和游戏输入。游戏窗口仍需本机单独确认；F8 可松键。持有此 MCP 地址的其他对话也可使用。"
            "桌面权限不受工作区限制，请关闭敏感窗口；同一时间仅让一个对话执行操作。继续？"):
            return
        service = self.service
        if service is None:
            self.cu_enabled.set(enable)
            self._refresh_computer_use_button()
            return
        if not service.is_running:
            return
        if enable and not service.require_auth:
            messagebox.showerror("拒绝开启", "必须先启用 MCP 鉴权。")
            return
        self._cu_busy = True
        self.cu_enabled.set(enable)
        self._refresh_computer_use_button()
        if not enable:
            for target in service.computer_use.enabled:
                service.computer_use.enabled[target] = False
        def change():
            error = None
            try:
                service.computer_use.set_enabled(enable)
            except Exception as exc:
                error = str(exc)
            self._post_ui(finish, error)
        def finish(error):
            self._cu_busy = False
            if self.service is service:
                self.cu_enabled.set(any(service.computer_use.enabled.values()))
            else:
                self.cu_enabled.set(False)
            self._refresh_computer_use_button()
            if error:
                self.append_log("[Computer Use] 切换失败: " + error)
            else:
                self.append_log("[Computer Use] 权限已切换；MCP 地址未改变，新对话也可连接。")
        threading.Thread(target=change, daemon=True).start()

    def stop_bridge(self):
        if self.service:
            self._generation = getattr(self, "_generation", 0) + 1
            self.set_status("STOPPING")
            self.btn_toggle_bridge.config(state="disabled")
            self.append_log("[*] 正在安全停止 Bridge 与穿透进程...")
            threading.Thread(target=self._async_stop, daemon=True).start()

    def _async_stop(self):
        if self.service:
            self.service.stop()
            self.service = None
        self._post_ui(self._on_stopped_cleanup)

    def _on_stopped_cleanup(self):
        self.cu_enabled.set(False)
        self.set_status("STOPPED")
        self.mcp_url_var.set("服务已停止 · 点击「启动 Bridge」重新运行")
        self.current_url = ""
        self.btn_toggle_bridge.config(text='启动 Bridge', variant='primary', state='normal')
        self.ws_entry.config(state="normal")
        self.update_prompt_preview()
        if self._closing:
            self.root.destroy()

    def _startup_failed(self):
        self.service = None
        self._on_stopped_cleanup()

    def _deliver_log(self, generation, value):
        if generation == getattr(self, '_generation', 0):
            self.append_log(value)

    def _service_event(self, generation, callback, value):
        # 丢弃停止/重启之前排队的旧服务回调。
        if callback == self.append_log:
            self._post_ui(self._deliver_log, generation, value)
            return
        def deliver():
            if generation == getattr(self, '_generation', 0):
                callback(value)
        self._post_ui(deliver)

    # 服务回调仅向 Python 队列投递；Tk 操作全部在主线程执行。
    def on_service_log(self, text):
        self._post_ui(self.append_log, text)

    def on_service_url(self, url):
        self._post_ui(self.set_url, url)

    def on_service_status(self, status):
        self._post_ui(self.set_status, status)

    def set_url(self, url):
        previous = self.current_url
        self.current_url = url
        self.mcp_url_var.set(url or '公网地址待验证；正在自动恢复…')
        if previous != url:
            self.update_prompt_preview()
        self._update_copy_state()
        if url:
            self.append_log(f"[地址更新] {url}", "success")

    def _can_copy(self):
        return (self.current_status in ('RUNNING_ONLINE', 'DEGRADED')
                and self.current_url.startswith("https://"))

    def _update_copy_state(self):
        state = "normal" if self._can_copy() else "disabled"
        self.btn_copy_prompt.config(state=state)
        self.btn_copy_url.config(state=state)

    def set_status(self, status):
        self.current_status = status
        self._refresh_computer_use_button()
        stopped = status == 'STOPPED'
        self.btn_browse.config(state='normal' if stopped else 'disabled')
        self.ws_entry.config(state='normal' if stopped else 'disabled')
        self.btn_repair.config(state='normal' if self.service and self.service.is_running
            and status not in ('STOPPING', 'STOPPED') else 'disabled')
        if status not in ('RUNNING_ONLINE', 'DEGRADED'):
            if self.current_url:
                self.current_url = ''
                self.update_prompt_preview()
            self.mcp_url_var.set('启动后生成安全的公网 MCP 地址' if stopped else '正在连接与验证，请稍候…')
        self._update_copy_state()
        c = self.theme.c
        neutral = (c['secondary'], c['muted'])
        states = {
            'STOPPED': ('●  未启动', neutral),
            'STOPPING': ('●  正在安全停止', neutral),
            'TUNNELING': ('●  建立连接中', (c['orange_tint'], c['orange_text'])),
            'RUNNING_LOCAL': ('●  本地服务就绪', (c['blue_tint'], c['blue_text'])),
            'RUNNING_ONLINE': ('●  公网已验证', (c['green_tint'], c['green_text'])),
            'DEGRADED': ('●  自检波动 · 保留已验证入口', (c['orange_tint'], c['orange_text'])),
            'RECONNECTING': ('●  正在自动恢复', (c['orange_tint'], c['orange_text'])),
        }
        text, (tint, color) = states.get(status, ('●  ' + str(status), neutral))
        self.status_var.set(text)
        self.lbl_status.config(bg=tint, fg=color)
        # 稳定在线：缓慢呼吸；连接 / 恢复中：快速脉动；其余静止
        self.lbl_status.set_pulse('slow' if status == 'RUNNING_ONLINE' else
                                  'fast' if status in ('TUNNELING', 'RECONNECTING', 'STOPPING') else None)
        self.btn_toggle_bridge.config(text='启动 Bridge' if stopped else '停止 Bridge',
            variant='primary' if stopped else 'danger',
            state='disabled' if status == 'STOPPING' else 'normal')
        if stopped:
            self.connection_detail.set('服务已停止  ·  启动后自动建立 Cloudflare 隧道并持续自检')

    def update_prompt_preview(self):
        url = self.current_url or "https://<启动后自动生成公网地址>.trycloudflare.com/mcp"
        ws = (self.service.workspace if self.service is not None
              else self.workspace_var.get())
        content = DEFAULT_PROMPT_TEMPLATE.format(url=url, workspace=ws)

        self.txt_prompt.delete("1.0", tk.END)
        self.txt_prompt.insert(tk.END, content)

    def reset_prompt_template(self):
        self.update_prompt_preview()
        self.show_toast("已恢复默认提示词")

    def copy_prompt_to_clipboard(self):
        if not self._can_copy():
            messagebox.showinfo("提示", "公网 MCP 尚未验证就绪，请稍后再复制。")
            return
        text = self.txt_prompt.get("1.0", tk.END).strip()
        if not text:
            return
        
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

        self.btn_copy_prompt.flash('已复制 ✓')
        self.show_toast("提示词已复制 · 直接粘贴给 AI 即可")
        self.append_log("[操作] 开工提示词已复制到剪贴板。", "success")

    def copy_url(self):
        if not self._can_copy():
            messagebox.showinfo("提示", "公网 MCP 尚未验证就绪，请稍后再复制。")
            return
        url = self.mcp_url_var.get()
        if not url or "未启动" in url or "正在" in url:
            messagebox.showinfo("提示", "请先启动 Bridge，获取公网 MCP 接口地址后再复制。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.root.update()
        self.btn_copy_url.flash('已复制 ✓')
        self.show_toast("MCP 地址已复制")

    def reset_mcp_url(self):
        """重置秘密路径，旧链接立即失效（地址泄露时使用）。"""
        if not self.service or not self.service.is_running:
            messagebox.showinfo("提示", "请先启动 Bridge 再重置地址。")
            return
        if not getattr(self.service, "require_auth", False):
            messagebox.showinfo("提示", "当前未启用认证，无需重置。")
            return
        if not messagebox.askyesno(
                "重置 MCP 地址",
                "重置后旧链接立即失效，已发给 AI 的地址将无法继续使用。\n\n"
                "重置完成后请重新复制提示词发给对方。\n\n确定要重置吗？"):
            return
        self.service.reset_secret()
        self.append_log("[安全] MCP 地址已重置，旧链接立即失效。", "success")
        self.show_toast("地址已重置 · 请重新复制提示词")

    def open_health_test(self):
        url = self.current_url
        if not url:
            port = self.service.port if self.service and self.service.port else 8765
            url = f"http://127.0.0.1:{port}/health"
        else:
            base = url.rsplit("/mcp", 1)[0]
            url = f"{base}/health"
        webbrowser.open(url)

    def show_toast(self, msg):
        """Transient pill in the footer centre; a newer toast replaces the old one."""
        toast = self.lbl_copy_toast
        was_visible = bool(toast.cget('text')) and bool(toast.place_info())
        toast.config(text=msg)
        if getattr(self, '_toast_after', None):
            self.root.after_cancel(self._toast_after)
        if getattr(self, '_footer', None) is not None:
            # 自下而上滑入并淡入；已显示时只换文字，不重复入场
            rise = self.theme.px(10)
            slide = getattr(self, '_toast_slide', None)
            if slide is None:
                def move():
                    if toast.cget('text'):
                        toast.place_configure(y=int(round(slide_anim.value)))
                slide_anim = self._toast_slide = Anim(toast, move, value=0.0)
                slide = slide_anim
            if not was_visible:
                slide.set(rise)
                toast.place(relx=0.5, rely=0.5, anchor='center', y=rise)
                toast.appear(True, 0.24)
                slide.to(0, 0.28)
            else:
                slide.to(0, 0.18)   # 淡出途中来了新提示：config 已让颜色回到可见
        def hide():
            self._toast_after = None
            def gone():
                if self._toast_after is None:   # 期间没有新的提示
                    toast.config(text='')
                    toast.place_forget()
            if getattr(self, '_footer', None) is not None and toast.place_info():
                toast.appear(False, 0.22, on_done=gone)
                if getattr(self, '_toast_slide', None) is not None:
                    self._toast_slide.to(self.theme.px(6), 0.22)
            else:
                gone()
        self._toast_after = self.root.after(3200, hide)

    def append_log(self, text, tag=None):
        ts = time.strftime("[%H:%M:%S] ")
        self.txt_log.insert(tk.END, ts, "time")

        if "[TOOL]" in text:
            self.txt_log.insert(tk.END, text + "\n", "tool")
        elif "[EXEC]" in text:
            self.txt_log.insert(tk.END, text + "\n", "exec")
        elif ("[COMPUTER USE]" in text or "[GAME INPUT]" in text
              or "[BACKGROUND INPUT]" in text):
            self.txt_log.insert(tk.END, text + "\n", "cu")
        elif tag:
            self.txt_log.insert(tk.END, text + "\n", tag)
        else:
            self.txt_log.insert(tk.END, text + "\n")

        if int(self.txt_log.index('end-1c').split('.')[0]) > 4000:
            self.txt_log.delete('1.0', '501.0')
        if self.auto_scroll_var.get():
            self.txt_log.see(tk.END)

    def clear_logs(self):
        self.txt_log.delete("1.0", tk.END)

    def on_close(self):
        if self.service and self.current_status != "STOPPED":
            if messagebox.askyesno("退出确认", "Bridge 服务正在运行中，退出将停止外部 AI 连接，是否确定退出？"):
                self._closing = True
                self.stop_bridge()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = OpenBridgeApp(root)
    fade_in_window(root)
    root.mainloop()

if __name__ == "__main__":
    main()
