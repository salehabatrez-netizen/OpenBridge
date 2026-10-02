"""Multi-project progress for the always-on-top "AI 状态" window.

Pure data, no Tk: floating_status paints whatever ``board()`` returns.
floating_status hot-reloads this file whenever its mtime changes, so the board
can evolve without restarting the GUI (a restart changes the MCP URL and drops
every connected agent).

Read-only by contract: it never writes HANDOFF/ or _coord/, never spawns a
process and never raises. A broken project becomes a row that says so and the
other projects keep rendering.
"""

import json
import os
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
HANDOFF_ROOT = os.path.join(HERE, 'HANDOFF')
COORD_ROOT = os.path.join(HERE, '_coord')

WORKING_WINDOW = 15 * 60      # a HANDOFF write this recent = someone is on it
STALE_DOING = 30 * 60         # mirrors handoff.STALE_DOING_SECONDS
EXPIRED_CLAIM_WINDOW = 86400  # expired claims older than this are just history
DORMANT_AFTER = 7 * 86400     # folded into the "+N 个休眠" footer
GUARDIAN_FRESH = 30           # guardian_status.json older than this = not running
INCIDENT_WINDOW = 3600        # guardian incidents surfaced on the board

TITLE_WIDTH = 24              # display columns (CJK = 2)
SUB_WIDTH = 46

H_WORKING, H_WAITING, H_STALE, H_ERROR, H_IDLE, H_DORMANT = (
    'working', 'waiting', 'stale', 'error', 'idle', 'dormant')

# Text only (no emoji): Tk labels on Windows render non-BMP glyphs as boxes
# unless the font has them. The window draws the status dot on a Canvas.
PRESENTATION = {              # colour, glyph (console fallback), short label
    H_WORKING: ('#42d392', '\u25cf', '进行中'),
    H_WAITING: ('#efc775', '\u25cf', '等你'),
    H_STALE:   ('#f0a742', '\u25cf', '需核实'),
    H_ERROR:   ('#ff6b6b', '\u25cf', '读取失败'),
    H_IDLE:    ('#7fb4ff', '\u25cf', '空闲'),
    H_DORMANT: ('#6b7280', '\u25cb', '休眠'),
}
ORDER = (H_WORKING, H_WAITING, H_STALE, H_ERROR, H_IDLE, H_DORMANT)
MUTED = '#8b9bb4'

_cache = {}


# ------------------------------------------------------------------ helpers
def _load_json(path):
    """json.load with an (mtime, size) cache: the window polls every few seconds."""
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    _cache[path] = (key, data)
    return data


def _col(ch):
    return 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1


def display_width(text):
    return sum(_col(ch) for ch in str(text))


def clip(text, width):
    """Collapse whitespace and cut to ``width`` display columns with an ellipsis."""
    text = ' '.join(str(text or '').split())
    if display_width(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        if used + _col(ch) > width - 1:
            break
        out.append(ch)
        used += _col(ch)
    return ''.join(out) + '\u2026'


def ago(seconds):
    if seconds is None:
        return '无记录'
    seconds = max(0, int(seconds))
    if seconds < 60:
        return '刚刚'
    if seconds < 3600:
        return '%d 分钟前' % (seconds // 60)
    if seconds < 86400:
        return '%d 小时前' % (seconds // 3600)
    return '%d 天前' % (seconds // 86400)


def remaining(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return '剩 %d 分' % max(1, seconds // 60)
    return '剩 %dh%02dm' % (seconds // 3600, (seconds % 3600) // 60)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dicts(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


# ---------------------------------------------------------------- summaries
def summarize(slug, meta, state, now=None):
    """Everything the board needs about one project, as plain data."""
    now = time.time() if now is None else now
    items = _dicts(state.get('items'))
    counts = {'todo': 0, 'doing': 0, 'done': 0, 'blocked': 0}
    for item in items:
        if item.get('status') in counts:
            counts[item['status']] += 1
    claims = _dicts(state.get('claims'))
    live = sorted((c for c in claims if _num(c.get('expires_at')) > now),
                  key=lambda c: _num(c.get('expires_at')))
    recent_expired = [c for c in claims
                      if 0 < now - _num(c.get('expires_at')) <= EXPIRED_CLAIM_WINDOW]
    held = {c.get('target') for c in live}
    stale_doing = [i for i in items if i.get('status') == 'doing'
                   and now - _num(i.get('updated_at')) > STALE_DOING
                   and i.get('id') not in held
                   and (i.get('workstream') or None) not in held]
    waiting = [b for b in _dicts(state.get('blockers')) if b.get('status', 'active') == 'active']
    review = [a for a in _dicts(state.get('assets')) if a.get('status') == 'pending_review']
    session = state.get('session') if isinstance(state.get('session'), dict) else {}
    journal = _dicts(state.get('journal'))
    stamps = [_num(state.get('updated_at')), _num(session.get('started_at'))]
    stamps += [_num(c.get('at')) for c in claims]
    if journal:
        stamps.append(_num(journal[-1].get('at')))
    last = max(stamps) or None
    age = (now - last) if last else None
    focus = state.get('focus') if isinstance(state.get('focus'), dict) else {}

    if live or (age is not None and age < WORKING_WINDOW):
        health = H_WORKING
    elif waiting:
        health = H_WAITING
    elif age is None or age >= DORMANT_AFTER:
        health = H_DORMANT
    elif stale_doing or recent_expired:
        health = H_STALE
    else:
        health = H_IDLE
    return {
        'slug': slug,
        'name': meta.get('name') or state.get('project') or slug,
        'health': health,
        'done': counts['done'], 'total': len(items), 'counts': counts,
        'live_claims': live, 'recent_expired': recent_expired,
        'stale_doing': stale_doing, 'waiting': waiting, 'review': review,
        'focus': (focus.get('text') or '').strip(),
        'last': last, 'age': age,
        'last_by': (journal[-1].get('by') if journal else '') or session.get('assistant') or '',
        'error': None,
    }


def _error_summary(slug, meta, exc):
    return {'slug': slug, 'name': meta.get('name') or slug, 'health': H_ERROR,
            'done': 0, 'total': 0, 'counts': {}, 'live_claims': [], 'recent_expired': [],
            'stale_doing': [], 'waiting': [], 'review': [], 'focus': '', 'last': None,
            'age': None, 'last_by': '', 'error': '%s: %s' % (type(exc).__name__, exc)}


def _subline(s, now):
    """The one thing the user most needs to know about this project."""
    if s['error']:
        return '读取失败：' + s['error'], PRESENTATION[H_ERROR][0]
    if s['live_claims']:
        c = s['live_claims'][0]
        more = '（+%d 个认领）' % (len(s['live_claims']) - 1) if len(s['live_claims']) > 1 else ''
        return ('认领：%s · %s · %s%s' % (c.get('by') or '?', c.get('target') or '?',
                                               remaining(_num(c.get('expires_at')) - now), more),
                PRESENTATION[H_WORKING][0])
    if s['waiting']:
        more = '（共 %d 项）' % len(s['waiting']) if len(s['waiting']) > 1 else ''
        return '等你决定：%s%s' % (s['waiting'][0].get('text', ''), more), PRESENTATION[H_WAITING][0]
    if s['health'] == H_STALE:
        if s['stale_doing']:
            i = s['stale_doing'][0]
            return ('需核实：%s 进行中但 %s 未更新' % (i.get('id'), ago(now - _num(i.get('updated_at')))),
                    PRESENTATION[H_STALE][0])
        c = s['recent_expired'][0]
        return '需核实：%s 的认领已过期（%s）' % (c.get('by') or '?', c.get('target') or '?'), PRESENTATION[H_STALE][0]
    if s['focus']:
        return '\u2192 ' + s['focus'], MUTED
    return '（未设焦点）', MUTED


def row(s, now, default=False):
    colour, glyph, short = PRESENTATION[s['health']]
    sub, sub_colour = _subline(s, now)
    badges = []
    if s['waiting'] and s['live_claims']:
        badges.append('待决 %d' % len(s['waiting']))   # waiting is not the subline: keep it visible
    if s['review']:
        badges.append('待审 %d' % len(s['review']))
    return {
        'slug': s['slug'], 'health': s['health'], 'glyph': glyph, 'colour': colour, 'short': short,
        'title': clip(('\u2605 ' if default else '') + s['name'], TITLE_WIDTH),
        'right': ('%d/%d' % (s['done'], s['total'])) if s['total'] else '\u2014',
        'progress': (float(s['done']) / s['total']) if s['total'] else None,
        'badges': ' '.join(badges),
        'age': ago(s['age']) if s['last'] else '',
        'sub': clip(sub, SUB_WIDTH), 'sub_colour': sub_colour,
    }


# ------------------------------------------------------------------ guardian
def _tail_lines(path, max_bytes=16384):
    with open(path, 'rb') as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        data = fh.read().decode('utf-8', 'replace')
    lines = data.splitlines()
    return lines[1:] if size > max_bytes else lines


def guardian_line(now=None, root=None):
    """Runtime-resource leases (_coord) at a glance, or None if _coord is absent.

    Red only while a protected program is actually down (lease status 'down',
    maintained by the guardian). An exit that already recovered - typically
    the user restarting OpenBridge - is reported calmly for an hour instead of
    looking like an ongoing emergency. Without a live guardian nothing can be
    vouched for, so that is what the line says.
    """
    now = time.time() if now is None else now
    root = COORD_ROOT if root is None else root
    lease_dir = os.path.join(root, 'leases')
    if not os.path.isdir(lease_dir):
        return None
    leases = []
    try:
        names = sorted(n for n in os.listdir(lease_dir) if n.endswith('.json'))
    except OSError:
        names = []
    for name in names:
        try:
            lease = _load_json(os.path.join(lease_dir, name))
        except Exception:
            lease = None
        leases.append(lease if isinstance(lease, dict) else {})
    status = None
    try:
        status = _load_json(os.path.join(root, 'guardian_status.json'))
    except Exception:
        pass
    exits = []
    try:
        for line in _tail_lines(os.path.join(root, 'incidents.jsonl')):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get('kind') in ('exit', 'restart') \
                    and now - _num(rec.get('at')) <= INCIDENT_WINDOW:
                exits.append(rec)
    except OSError:
        pass
    running = isinstance(status, dict) and now - _num(status.get('updated')) <= GUARDIAN_FRESH
    down = [l for l in leases if l.get('status') == 'down']
    if not running:
        text, colour = '守护未运行 · %d 项登记（只防误杀，不自动恢复）' % len(leases), PRESENTATION[H_STALE][0]
    elif down:
        first = min(down, key=lambda l: _num(l.get('down_since')) or now)
        since = _num(first.get('down_since'))
        text = '异常：%s 已退出，尚未恢复（%s）' % (first.get('resource') or '?',
                                            ago(now - since) if since else '刚刚')
        if len(down) > 1:
            text += ' 等 %d 项' % len(down)
        colour = PRESENTATION[H_ERROR][0]
    elif status.get('paused'):
        text, colour = '守护已暂停 · %d 项受保护' % len(leases), PRESENTATION[H_WAITING][0]
    elif exits:
        rec = exits[-1]
        when = ago(now - _num(rec.get('at')))
        if len(exits) > 1:
            when += '，共 %d 次' % len(exits)
        text, colour = '%s 退出后已恢复（%s）' % (rec.get('resource') or '?', when), MUTED
    else:
        text, colour = '守护运行中 · %d 项受保护 · 1 小时内无异常' % len(leases), MUTED
    return {'text': clip(text, SUB_WIDTH + 6), 'colour': colour}


# --------------------------------------------------------------------- board
def board(now=None, handoff_root=None, coord_root=None, limit=6):
    """Rows for the floating window, most urgent first. Never raises."""
    now = time.time() if now is None else now
    handoff_root = HANDOFF_ROOT if handoff_root is None else handoff_root
    out = {'rows': [], 'hidden': 0, 'header': '项目进展', 'counts': {}, 'guard': None, 'error': None}
    try:
        out['guard'] = guardian_line(now, coord_root)
    except Exception as exc:
        out['guard'] = {'text': '守护状态读取失败：%s' % exc, 'colour': PRESENTATION[H_STALE][0]}
    try:
        registry = _load_json(os.path.join(handoff_root, 'registry.json'))
        projects = registry.get('projects')
        if not isinstance(projects, dict):
            raise ValueError('projects 不是对象')
        default = registry.get('active')
    except FileNotFoundError:
        out['error'] = '未找到 HANDOFF/registry.json'
        return out
    except Exception as exc:
        out['error'] = 'registry.json 读取失败：%s' % exc
        return out
    summaries = []
    for slug, meta in projects.items():
        meta = meta if isinstance(meta, dict) else {}
        try:
            state = _load_json(os.path.join(handoff_root, 'projects', slug, 'state.json'))
            if not isinstance(state, dict):
                raise ValueError('state.json 不是对象')
            summaries.append(summarize(slug, meta, state, now))
        except Exception as exc:
            summaries.append(_error_summary(slug, meta, exc))
    summaries.sort(key=lambda s: (ORDER.index(s['health']), -(s['last'] or 0)))
    visible = [s for s in summaries if s['health'] != H_DORMANT or s['slug'] == default][:limit]
    counts = {}
    for s in summaries:
        counts[s['health']] = counts.get(s['health'], 0) + 1
    out['counts'] = counts
    out['rows'] = [row(s, now, default=(s['slug'] == default)) for s in visible]
    out['hidden'] = len(summaries) - len(visible)
    parts = ['项目进展 · %d 个' % len(summaries)]
    for health in (H_WORKING, H_WAITING, H_STALE, H_ERROR):
        if counts.get(health):
            parts.append('%s %d' % (PRESENTATION[health][2], counts[health]))
    out['header'] = ' · '.join(parts)
    return out


def render_text(data):
    """Plain-text board (CLI / logs / tests): same content as the window."""
    lines = [data.get('header', '项目进展')]
    if data.get('error'):
        lines.append('  ' + data['error'])
    for r in data.get('rows', []):
        tail = '  '.join(x for x in (r['badges'], r['right'], r['age']) if x)
        lines.append('%s %s  %s' % (r['glyph'], r['title'], tail))
        lines.append('   ' + r['sub'])
    if data.get('hidden'):
        lines.append('  +%d 个项目未显示（休眠或超出行数）' % data['hidden'])
    if data.get('guard'):
        lines.append(data['guard']['text'])
    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print(render_text(board()))
