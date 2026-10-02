"""Command line for multi-project AI handoff documents (state schema 2).

Assignment model
----------------
The user assigns an assistant to one project. Every command then acts on that
project only. Three ways to say which, strongest first:

    --project <slug>            explicit, wins over everything
    HANDOFF_PROJECT=<slug>      environment variable for this shell/chat
    handoff_cli.py use X        standing assignment stored in registry.json

If none is set the command refuses rather than guessing. Guessing is how
notes from one project leak into another. With several chats open at once,
prefer --project: `use` changes the assignment for everybody.

Typical session for an incoming assistant::

    python handoff_cli.py projects                     # what exists
    python handoff_cli.py --project X brief            # one screen: focus, waits, claims
    python handoff_cli.py --project X show             # compact briefing (--full: everything)
    python handoff_cli.py --project X get F12          # one record in full
    python handoff_cli.py --project X session "my-name" --note "接手"
    python handoff_cli.py --project X claim keyframes --by "my-name" --minutes 90
    ... work, `heartbeat` every hour, record assets / attempts / facts ...
    python handoff_cli.py --project X done <item> --evidence "40/40 tests"
    python handoff_cli.py --project X release keyframes --by "my-name"
"""

import argparse
import contextlib
import json
import os
import sys
import time

from handoff import (ASSET_STATUS, ATTEMPT_RESULTS, AI_ASSET_STATUS, ClaimConflict,
                     DEFAULT_CLAIM_MINUTES, HandoffState, WORKSTREAM_STATUS)
from registry import Registry, slugify, validate_slug

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, 'HANDOFF')
ENV_PROJECT = 'HANDOFF_PROJECT'
LOCK_WAIT_SECONDS = 10.0
LOCK_STALE_SECONDS = 30.0

READ_ONLY = ('show', 'brief', 'status', 'whoami', 'check', 'get')

# Symbols used in briefings that legacy Windows consoles (GBK/cp936) cannot
# encode. HANDOFF.md on disk keeps them; only console output is translated.
_CONSOLE_FALLBACK = (('⚠️', '[!]'), ('\ufe0f', ''), ('🎯', '[焦点]'), ('⛔', '[等用户]'),
                     ('🔒', '[认领]'), ('🧭', '[工作流]'), ('✅', '[正典]'), ('⏳', '[待审]'),
                     ('🚫', '[否决]'), ('❌', '[别重复]'), ('⚠', '[!]'), ('ℹ', '[i]'), ('✖', '[x]'))


def _console_safe(text, encoding):
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        for symbol, plain in _CONSOLE_FALLBACK:
            text = text.replace(symbol, plain)
        try:
            return text.encode(encoding, errors='replace').decode(encoding, errors='replace')
        except LookupError:
            return text


class _SafeStream:
    """Wrap stdout/stderr so a GBK console never crashes on an emoji."""

    def __init__(self, stream):
        self._stream = stream
        self._encoding = getattr(stream, 'encoding', None) or 'utf-8'

    def write(self, text):
        return self._stream.write(_console_safe(text, self._encoding))

    def flush(self):
        return self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextlib.contextmanager
def _locked(path):
    """Serialise read-modify-write of one state file across concurrent chats.

    Two assistants running `note` at the same moment would otherwise each
    load the old file and the second save would silently drop the first
    note. An O_EXCL lock file works the same on Windows and POSIX; a lock
    older than LOCK_STALE_SECONDS is assumed to belong to a crashed process.
    """
    lock = path + '.lock'
    os.makedirs(os.path.dirname(os.path.abspath(lock)), exist_ok=True)
    deadline = time.time() + LOCK_WAIT_SECONDS
    handle = None
    while handle is None:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, str(os.getpid()).encode('ascii'))
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > LOCK_STALE_SECONDS:
                    os.unlink(lock)
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                raise RuntimeError('state.json 正被另一个进程写入（%s），稍后重试' % lock)
            time.sleep(0.1)
    try:
        yield
    finally:
        os.close(handle)
        try:
            os.unlink(lock)
        except OSError:
            pass


def _open(registry, slug):
    state = HandoffState(registry.state_path(slug))
    state.load()
    return state


def _persist(registry, slug, state, save=True):
    """Save JSON then regenerate the Markdown so the two never drift.

    HANDOFF.md is the compact view (what an agent naturally opens, bounded in
    size); HANDOFF_FULL.md is the complete archive next to it. `render` passes
    save=False: regenerating documents is not project activity and must not
    bump updated_at (the floating board reads that as "someone is working").
    """
    if save:
        state.save()
    doc = registry.doc_path(slug)
    os.makedirs(os.path.dirname(doc), exist_ok=True)
    with open(doc, 'w', encoding='utf-8') as handle:
        handle.write(state.compact_markdown())
    with open(os.path.join(os.path.dirname(doc), 'HANDOFF_FULL.md'), 'w', encoding='utf-8') as handle:
        handle.write(state.to_markdown())
    return doc


def _format_record(bucket, record):
    lines = ['[%s] %s' % (bucket, record.get('id'))]
    for key, value in record.items():
        if key == 'id' or value in ('', None, [], {}):
            continue
        if key in ('at', 'updated_at', 'started_at', 'expires_at', 'created_at') and isinstance(value, (int, float)):
            value = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(value))
        elif isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append('  %s: %s' % (key, value))
    return '\n'.join(lines)


def _banner(registry, slug):
    meta = registry.data['projects'].get(slug, {})
    return '[项目] %s (%s)' % (meta.get('name', slug), slug)


def _csv(text):
    return [part.strip() for part in (text or '').split(',') if part.strip()]


def cmd_projects(registry, args):
    if args.action == 'add':
        slug = validate_slug(args.slug if args.slug else slugify(args.name or ''))
        registry.register(slug, name=args.name or slug, goal=args.goal or '')
        state = _open(registry, slug)
        state.data['project'] = args.name or slug
        state.data['goal'] = args.goal or ''
        _persist(registry, slug, state)
        registry.save()
        print('已创建项目 %s' % slug)
        if registry.active == slug:
            print('（这是第一个项目，已自动指派为当前项目）')
        return 0
    if args.action == 'remove':
        registry.remove(args.slug)
        registry.save()
        print('已从注册表移除 %s（文件保留在 projects/%s/，未删除）' % (args.slug, args.slug))
        return 0
    rows = registry.listing()
    if not rows:
        print('尚未注册任何项目。')
        print('新建： python handoff_cli.py projects add <标识> --name "名称" --goal "目标"')
        return 0
    print('项目列表（★ = registry 中的默认指派；多窗口并行时请用 --project 显式指定）：')
    for row in rows:
        print('  %s %-20s %s' % ('★' if row['active'] else ' ', row['slug'], row['name']))
        if row['goal']:
            print('      目标：%s' % row['goal'][:80])
    return 0


def _read_only(registry, slug, state, args):
    if args.cmd == 'whoami':
        print(_banner(registry, slug))
        print('来源：%s' % args._resolved_from)
        print('状态文件：%s' % registry.state_path(slug))
        return 0
    if args.cmd == 'show':
        print(_banner(registry, slug))
        sys.stdout.write((state.to_markdown() if args.full else state.compact_markdown()) + '\n')
        return 0
    if args.cmd == 'get':
        hits = state.find_any(args.id)
        if not hits:
            sys.stderr.write('没有编号为 %s 的记录（笔记 D#/B#/F#、尝试 A#、条目、工作流、资产）\n' % args.id)
            return 2
        print('\n'.join(_format_record(bucket, record) for bucket, record in hits))
        return 0
    if args.cmd == 'brief':
        print(_banner(registry, slug))
        sys.stdout.write(state.brief_markdown() + '\n')
        return 0
    if args.cmd == 'status':
        summary = state.summary()
        counts = summary['counts']
        print(_banner(registry, slug))
        print('焦点: %s' % (summary['focus'] or '(未设置)'))
        print('当前: %s' % (summary['current'] or '(无进行中任务)'))
        print('进度: 完成 %d / 进行 %d / 待办 %d / 阻塞 %d'
              % (counts.get('done', 0), counts.get('doing', 0),
                 counts.get('todo', 0), counts.get('blocked', 0)))
        print('认领: 有效 %d / 过期 %d   资产: 已认可 %d / 待审阅 %d'
              % (summary['claims'], summary['expired_claims'],
                 summary['approved'], summary['pending_review']))
        if summary['stale']:
            print('警告: %d 项标记为进行中但已超过 30 分钟无更新' % summary['stale'])
        if summary['blockers']:
            print('等待用户决策: %d 条' % summary['blockers'])
        return 0
    if args.cmd == 'check':
        problems = state.lint(root=HERE if args.paths else None)
        print(_banner(registry, slug))
        if not problems:
            print('OK：没有发现问题')
        icons = {'error': '✖', 'warn': '⚠', 'info': 'ℹ'}
        for level, message in problems:
            print('%s %s' % (icons.get(level, '-'), message))
        return 1 if any(level == 'error' for level, _ in problems) else 0
    return 1


def _write(registry, slug, state, args):
    """Apply one mutating command. Returns (exit_code, message)."""
    cmd = args.cmd
    if cmd == 'goal':
        state.data['goal'] = args.text
        registry.data['projects'][slug]['goal'] = args.text
        registry.save()
        return 0, '已更新目标'
    if cmd == 'add':
        state.add(args.id, args.title, status=args.status, owner=args.owner,
                  detail=args.detail, evidence=args.evidence,
                  workstream=args.ws, depends_on=_csv(args.depends))
        return 0, '已添加条目 %s' % args.id
    if cmd in ('start', 'done', 'block', 'reopen'):
        mapping = {'start': 'doing', 'done': 'done', 'block': 'blocked', 'reopen': 'todo'}
        record = next((r for r in state.data['items'] if r['id'] == args.id), None)
        if record is None:
            return 2, ('项目 %s 中没有编号为 %r 的条目。先看： handoff_cli.py show' % (slug, args.id))
        if cmd == 'done' and not (args.evidence or record.get('evidence')):
            return 2, 'done 必须附实测证据：--evidence "40/40 测试通过" 之类；没实测就留在 doing'
        if cmd == 'block' and not (args.detail or record.get('detail')):
            return 2, 'block 必须写明在等什么：--detail "等用户选 A 还是 B"'
        fields = {'status': mapping[cmd]}
        if args.detail is not None:
            fields['detail'] = args.detail
        if args.evidence is not None:
            fields['evidence'] = args.evidence
        if args.owner is not None:
            fields['owner'] = args.owner
        state.update(args.id, **fields)
        return 0, '%s → %s' % (args.id, mapping[cmd])
    if cmd == 'note':
        entry = state.note(args.bucket, args.text, workstream=args.ws, by=args.by)
        return 0, '已记录 %s' % entry['id']
    if cmd == 'retire':
        entry = state.retire_note(args.note_id, status='resolved' if args.resolved else 'superseded',
                                  replaced_by=args.by_id, reason=args.reason)
        return 0, '%s → %s' % (entry['id'], entry['status'])
    if cmd == 'session':
        state.begin_session(args.assistant, note=args.note)
        return 0, '已登记会话 %s' % args.assistant
    if cmd == 'log':
        state.log(args.by, args.text)
        return 0, '已写入会话日志'
    if cmd == 'focus':
        state.set_focus(args.text, workstream=args.ws, why=args.why, by=args.by)
        return 0, '焦点已更新'
    if cmd == 'ws':
        if args.ws_cmd == 'add':
            state.add_workstream(args.id, args.title, goal=args.goal, entry=args.entry,
                                 accept=args.accept, next_step=args.next,
                                 status=args.status, owner=args.owner)
            return 0, '已建立工作流 %s' % args.id
        state.update_workstream(args.id, title=args.title, goal=args.goal, entry=args.entry,
                                accept=args.accept, next=args.next, status=args.status,
                                owner=args.owner)
        return 0, '已更新工作流 %s' % args.id
    if cmd == 'claim':
        record = state.claim(args.target, args.by, minutes=args.minutes,
                             note=args.note, force=args.force)
        return 0, '已认领 %s，至 %s' % (record['target'], time.strftime(
            '%H:%M', time.localtime(record['expires_at'])))
    if cmd == 'heartbeat':
        record = state.heartbeat(args.target, args.by, minutes=args.minutes)
        return 0, '已续期 %s，至 %s' % (record['target'], time.strftime(
            '%H:%M', time.localtime(record['expires_at'])))
    if cmd == 'release':
        state.release(args.target, by=args.by, force=args.force)
        return 0, '已释放 %s' % args.target
    if cmd == 'attempt':
        record = state.add_attempt(args.what, workstream=args.ws, result=args.result,
                                   reason=args.reason, lesson=args.lesson,
                                   by=args.by, refs=args.refs)
        return 0, '已记录尝试 %s' % record['id']
    if cmd == 'asset':
        if args.asset_cmd == 'add':
            state.add_asset(args.id, args.path, workstream=args.ws, kind=args.kind,
                            title=args.title, status=args.status, tool=args.tool,
                            model=args.model, seed=args.seed, prompt_ref=args.prompt_ref,
                            supersedes=args.supersedes, note=args.note, by=args.by)
            return 0, '已登记资产 %s' % args.id
        if args.asset_cmd == 'status':
            state.set_asset_status(args.id, args.status, note=args.note)
            return 0, '%s → %s' % (args.id, args.status)
        if args.asset_cmd == 'review':
            state.review_asset(args.id, args.verdict, args.feedback, by=args.by)
            return 0, '%s → %s（已记录用户意见）' % (args.id, args.verdict)
    return 1, '未知命令'


def _asset_list(state, args):
    rows = state.list_assets(status=args.status, workstream=args.ws)
    if not rows:
        print('（没有符合条件的资产）')
    for asset in rows:
        print('%-28s %-15s %-10s %s' % (asset['id'], asset.get('status'),
                                        asset.get('workstream') or '-', asset.get('path')))
    return 0


def _ws_list(state):
    if not state.data['workstreams']:
        print('（尚无工作流）')
    for ws in state.data['workstreams']:
        print('%-14s %-7s %-16s 下一步：%s' % (ws['id'], ws.get('status'),
                                            ws.get('owner') or '-', ws.get('next') or '-'))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description='AI 多项目交接状态管理（schema 2）')
    parser.add_argument('--root', default=ROOT)
    parser.add_argument('--project', default=None,
                        help='本次命令作用的项目标识（优先于 %s 与已指派项目）' % ENV_PROJECT)
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('projects', help='列出/新建/移除项目')
    p.add_argument('action', nargs='?', default='list', choices=('list', 'add', 'remove'))
    p.add_argument('slug', nargs='?', default=None)
    p.add_argument('--name', default='')
    p.add_argument('--goal', default='')

    p = sub.add_parser('use', help='设置 registry 中的默认指派（会影响所有窗口）')
    p.add_argument('slug')

    sub.add_parser('brief', help='一屏简报：焦点/等用户/认领/工作流（新 AI 先看这个）')
    p = sub.add_parser('show', help='交接简报（紧凑；--full 全文）')
    p.add_argument('--full', action='store_true', help='全部内容（含已作废笔记与全部证据）')
    p = sub.add_parser('get', help='按编号打印一条记录全文（D#/B#/F#/A#/条目/工作流/资产）')
    p.add_argument('id')
    sub.add_parser('status', help='几行摘要')
    sub.add_parser('render', help='重新生成 HANDOFF.md')
    sub.add_parser('whoami', help='显示当前作用的项目及其来源')
    p = sub.add_parser('check', help='体检：虚假完成、过期认领、缺焦点、资产缺审阅等')
    p.add_argument('--paths', action='store_true', help='同时检查资产文件是否存在')

    p = sub.add_parser('add', help='添加条目')
    p.add_argument('id')
    p.add_argument('title')
    p.add_argument('--status', default='todo')
    p.add_argument('--owner', default='')
    p.add_argument('--detail', default='')
    p.add_argument('--evidence', default='')
    p.add_argument('--ws', default='', help='所属工作流')
    p.add_argument('--depends', default='', help='依赖的条目 id，逗号分隔')

    for name in ('start', 'done', 'block', 'reopen'):
        q = sub.add_parser(name)
        q.add_argument('id')
        q.add_argument('--detail', default=None)
        q.add_argument('--evidence', default=None)
        q.add_argument('--owner', default=None)

    p = sub.add_parser('note', help='记录 decisions/blockers/facts，返回编号 D#/B#/F#')
    p.add_argument('bucket', choices=('decisions', 'blockers', 'facts'))
    p.add_argument('text')
    p.add_argument('--ws', default='')
    p.add_argument('--by', default='')

    p = sub.add_parser('retire', help='作废/解决一条笔记（保留追溯，不再出现在正文）')
    p.add_argument('note_id')
    p.add_argument('--by-id', default='', help='取代它的笔记/资产/尝试/条目 id')
    p.add_argument('--reason', default='')
    p.add_argument('--resolved', action='store_true', help='标为已解决（用于 blockers）')

    p = sub.add_parser('session', help='登记接手（同时写入会话日志）')
    p.add_argument('assistant')
    p.add_argument('--note', default='')

    p = sub.add_parser('log', help='写一行会话日志')
    p.add_argument('text')
    p.add_argument('--by', default='')

    p = sub.add_parser('goal')
    p.add_argument('text')

    p = sub.add_parser('focus', help='设置「现在做什么」')
    p.add_argument('text')
    p.add_argument('--ws', default='')
    p.add_argument('--why', default='')
    p.add_argument('--by', default='')

    p = sub.add_parser('ws', help='工作流 add/set/list')
    wsub = p.add_subparsers(dest='ws_cmd', required=True)
    q = wsub.add_parser('add')
    q.add_argument('id')
    q.add_argument('title')
    q.add_argument('--goal', default='')
    q.add_argument('--entry', default='', help='入口文档/目录（相对工作区）')
    q.add_argument('--accept', default='', help='验收标准')
    q.add_argument('--next', default='', help='下一步')
    q.add_argument('--status', default='active', choices=WORKSTREAM_STATUS)
    q.add_argument('--owner', default='')
    q = wsub.add_parser('set')
    q.add_argument('id')
    for flag in ('--title', '--goal', '--entry', '--accept', '--next', '--owner'):
        q.add_argument(flag, default=None)
    q.add_argument('--status', default=None, choices=WORKSTREAM_STATUS)
    wsub.add_parser('list')

    p = sub.add_parser('claim', help='认领工作流或条目（带有效期）')
    p.add_argument('target')
    p.add_argument('--by', required=True)
    p.add_argument('--minutes', type=float, default=DEFAULT_CLAIM_MINUTES)
    p.add_argument('--note', default='')
    p.add_argument('--force', action='store_true', help='接管别人仍有效的认领（先核实对方失联）')

    p = sub.add_parser('heartbeat', help='续期自己的认领')
    p.add_argument('target')
    p.add_argument('--by', required=True)
    p.add_argument('--minutes', type=float, default=DEFAULT_CLAIM_MINUTES)

    p = sub.add_parser('release', help='释放认领')
    p.add_argument('target')
    p.add_argument('--by', default='')
    p.add_argument('--force', action='store_true')

    p = sub.add_parser('attempt', help='记录被否决/失败的尝试，防止别人重复')
    p.add_argument('what')
    p.add_argument('--ws', default='')
    p.add_argument('--result', default='rejected', choices=ATTEMPT_RESULTS)
    p.add_argument('--reason', default='')
    p.add_argument('--lesson', default='')
    p.add_argument('--by', default='')
    p.add_argument('--refs', default='', help='相关文件/资产 id')

    p = sub.add_parser('asset', help='资产台账 add/status/review/list')
    asub = p.add_subparsers(dest='asset_cmd', required=True)
    q = asub.add_parser('add')
    q.add_argument('id')
    q.add_argument('path')
    q.add_argument('--ws', default='')
    q.add_argument('--kind', default='')
    q.add_argument('--title', default='')
    q.add_argument('--status', default='candidate', choices=AI_ASSET_STATUS)
    q.add_argument('--tool', default='')
    q.add_argument('--model', default='')
    q.add_argument('--seed', default=None)
    q.add_argument('--prompt-ref', dest='prompt_ref', default='')
    q.add_argument('--supersedes', default='')
    q.add_argument('--note', default='')
    q.add_argument('--by', default='')
    q = asub.add_parser('status', help='AI 可做的状态变更：candidate/pending_review/superseded')
    q.add_argument('id')
    q.add_argument('status', choices=AI_ASSET_STATUS)
    q.add_argument('--note', default='')
    q = asub.add_parser('review', help='记录用户的审阅结论（必须附用户原话）')
    q.add_argument('id')
    q.add_argument('verdict', choices=('approved', 'rejected'))
    q.add_argument('--feedback', required=True)
    q.add_argument('--by', default='user')
    q = asub.add_parser('list')
    q.add_argument('--ws', default=None)
    q.add_argument('--status', default=None, choices=ASSET_STATUS)
    return parser


def main(argv=None):
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _SafeStream(sys.stdout), _SafeStream(sys.stderr)
    try:
        return _main(argv)
    finally:
        sys.stdout, sys.stderr = saved


def _main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    registry = Registry(args.root)
    registry.load()

    if args.cmd == 'projects':
        return cmd_projects(registry, args)

    if args.cmd == 'use':
        registry.set_active(args.slug)
        registry.save()
        print('已指派当前项目：%s' % args.slug)
        print('提示：多个窗口并行时，这会改变所有未显式指定项目的 AI 的目标；优先用 --project。')
        return 0

    wanted = args.project or os.environ.get(ENV_PROJECT) or None
    args._resolved_from = ('--project' if args.project else
                           ('环境变量 %s' % ENV_PROJECT) if wanted else 'registry 默认指派')
    try:
        slug = registry.resolve(wanted)
    except (KeyError, RuntimeError, ValueError) as exc:
        sys.stderr.write(str(exc).strip("'\"") + '\n')
        return 2

    if args.cmd in READ_ONLY or (args.cmd == 'asset' and args.asset_cmd == 'list') or \
            (args.cmd == 'ws' and args.ws_cmd == 'list'):
        state = _open(registry, slug)
        if args.cmd == 'asset':
            return _asset_list(state, args)
        if args.cmd == 'ws':
            return _ws_list(state)
        return _read_only(registry, slug, state, args)

    state_path = registry.state_path(slug)
    try:
        with _locked(state_path):
            state = _open(registry, slug)
            if args.cmd == 'render':
                print(_persist(registry, slug, state, save=False))
                return 0
            code, message = _write(registry, slug, state, args)
            if code != 0:
                sys.stderr.write(message + '\n')
                return code
            doc = _persist(registry, slug, state)
    except ClaimConflict as exc:
        sys.stderr.write('认领冲突：%s\n' % exc)
        return 3
    except (KeyError, ValueError, RuntimeError) as exc:
        sys.stderr.write(str(exc).strip("'\"") + '\n')
        return 2
    print(message)
    print(doc)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
