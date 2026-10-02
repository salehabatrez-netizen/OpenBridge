"""Local owner trust policy for game-window approval.

Purpose: let the LOCAL owner pre-approve specific game processes (for example
Minecraft) so that a matching GameRequestControl no longer pops a GUI prompt.

Boundaries (unchanged safety model, only the click is skipped):
- The policy file lives OUTSIDE the MCP workspace, in the user's profile
  (%LOCALAPPDATA%\OpenBridge\game_trust.json). No MCP tool can write it.
- It is only consulted when the local GUI switch "自动批准受信游戏窗口" is ON
  (environment OPENBRIDGE_GAME_TRUST=1 is set by the GUI for the broker child).
- A rule matches by process executable name and/or window title prefix and/or
  window class. Matching a rule only skips the prompt; the lease still expires
  (600 s idle / 3600 s absolute / 1024 actions), still aborts on focus loss,
  still stops on F8 or Computer Use OFF.
"""
import json
import os
import time

FILE_NAME = 'game_trust.json'
DEFAULT_RULES = [
    {'name': 'Minecraft Java', 'process': ['javaw.exe', 'java.exe'], 'title_prefix': ['Minecraft'], 'class': ['GLFW30', 'LWJGL']},
]


def policy_path():
    base = os.environ.get('OPENBRIDGE_GAME_TRUST_DIR') or os.path.join(
        os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'OpenBridge')
    return os.path.join(base, FILE_NAME)


def enabled():
    return os.environ.get('OPENBRIDGE_GAME_TRUST') == '1'


def load_rules(path=None):
    path = path or policy_path()
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return []
    rules = data.get('rules') if isinstance(data, dict) else data
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []


def ensure_default(path=None):
    """Create the default policy file once (local GUI action only)."""
    path = path or policy_path()
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'version': 1, 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'rules': DEFAULT_RULES}, handle,
                  ensure_ascii=False, indent=1)
    return path


def process_name(pid):
    """Executable base name for pid via kernel32; empty string when unavailable."""
    if os.name != 'nt':
        return ''
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        handle = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ''
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ''
            return os.path.basename(buffer.value)
        finally:
            k.CloseHandle(handle)
    except Exception:
        return ''


def match(info, rules=None, exe=None):
    """Return the first matching rule (dict) or None. All listed criteria must hold."""
    rules = load_rules() if rules is None else rules
    if not rules:
        return None
    exe = (process_name(info.get('pid')) if exe is None else exe).lower()
    title = str(info.get('title', ''))
    cls = str(info.get('class', ''))
    for rule in rules:
        procs = [p.lower() for p in rule.get('process', [])]
        titles = rule.get('title_prefix', [])
        classes = rule.get('class', [])
        if not (procs or titles or classes):
            continue  # an empty rule never approves everything
        if procs and exe not in procs:
            continue
        if titles and not any(title.startswith(t) for t in titles):
            continue
        if classes and cls not in classes:
            continue
        return {'name': rule.get('name', 'unnamed'), 'process': exe or None, 'title': title[:80], 'class': cls}
    return None


def policy():
    """Callable for Controller(policy=...); None when the local switch is off."""
    if not enabled():
        return None
    return lambda info: match(info)
