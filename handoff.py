"""Handoff state for switching between AI assistants mid-project.

Why this exists
---------------
Work on this workspace is spread across many chat sessions and more than one
assistant. When a session ends (context exhausted, a switch to a different
model, or the user simply opening a new chat) everything the outgoing
assistant knew is lost unless it was written down. Chat history is not a
handover document: it is long, contradictory, and full of abandoned attempts.

This module keeps a single machine-readable file per project,
``HANDOFF/projects/<slug>/state.json``, and renders the briefing
``HANDOFF.md`` from it.

Schema 1 (still readable) held three item lists (done / doing / todo) plus
three free-text note buckets. Real projects outgrew that: one project piled up
20+ "facts" that mixed deliverable paths, tool quirks, rejected attempts and
user permissions; nobody could tell which images were approved, which were
rejected, or whether another chat was already doing the same thing.

Schema 2 adds, without breaking schema-1 files:

    focus        the single next action, so a newcomer knows where to start
    workstreams  parallel tracks (keyframes, character, ...) with an entry
                 document, acceptance criteria and the next step
    claims       time-limited locks so two chats never do the same work
    assets       deliverables with review status and provenance; only the
                 user's verdict can approve, and approved assets are canon
    attempts     rejected / failed approaches with the reason and the lesson,
                 so nobody repeats them
    note ids     D1/B1/F1... with a lifecycle (active -> superseded/resolved)
                 so stale facts stop polluting the briefing
    journal      one line per session / notable change

Everything is plain stdlib and the file is human-editable JSON: the user can
fix it by hand without running anything.
"""

import copy
import json
import os
import tempfile
import time

SCHEMA_VERSION = 2
VALID_STATUS = ('todo', 'doing', 'done', 'blocked')
WORKSTREAM_STATUS = ('active', 'paused', 'done')
ASSET_STATUS = ('candidate', 'pending_review', 'approved', 'rejected', 'superseded')
# Transitions an assistant may make on its own. `approved` / `rejected` record
# the user's verdict and must go through review_asset() with the user's words.
AI_ASSET_STATUS = ('candidate', 'pending_review', 'superseded')
ATTEMPT_RESULTS = ('rejected', 'failed', 'abandoned', 'partial')
NOTE_BUCKETS = ('decisions', 'blockers', 'facts')
NOTE_PREFIX = {'decisions': 'D', 'blockers': 'B', 'facts': 'F'}
NOTE_RETIRED = ('superseded', 'resolved')

# An item in `doing` older than this is probably an assistant that died
# mid-task rather than one that is still working.
STALE_DOING_SECONDS = 30 * 60
DEFAULT_CLAIM_MINUTES = 90
MAX_CLAIM_MINUTES = 24 * 60
BRIEF_DONE_LIMIT = 12
BRIEF_JOURNAL_LIMIT = 8
# Compact L2 (`show`, HANDOFF.md): bounded no matter how old the project is.
# Nothing is dropped silently - clipped records keep their id for `get <id>`,
# and the full archive stays in `show --full` / HANDOFF_FULL.md.
COMPACT_CLIP = 120
COMPACT_FACTS = 12
COMPACT_ATTEMPTS = 10
COMPACT_ASSETS = 6
COMPACT_DONE = 6
COMPACT_JOURNAL = 5
COMPACT_INDEX_CLIP = 22

_LIST_KEYS = ('items', 'decisions', 'blockers', 'facts', 'workstreams',
              'claims', 'assets', 'attempts', 'journal')

_STATUS_ZH = {'todo': '待办', 'doing': '进行中', 'done': '完成', 'blocked': '阻塞',
              'active': '活跃', 'paused': '暂停',
              'candidate': '候选', 'pending_review': '待审阅', 'approved': '已认可',
              'rejected': '已否决', 'superseded': '已取代',
              'failed': '失败', 'abandoned': '放弃', 'partial': '部分可用',
              'resolved': '已解决'}


class ClaimConflict(RuntimeError):
    """Another assistant holds a live claim on the same target."""


def _now():
    return time.time()


def _fmt(stamp, pattern='%Y-%m-%d %H:%M'):
    return time.strftime(pattern, time.localtime(stamp)) if stamp else '—'


def _cell(text):
    """Make text safe inside a Markdown table cell."""
    return str(text or '—').replace('|', '/').replace('\r', ' ').replace('\n', ' ')


def _short(text, limit=70):
    text = str(text or '').replace('\n', ' ')
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _blank():
    return {
        'schema': SCHEMA_VERSION,
        'project': '',
        'goal': '',
        'updated_at': 0.0,
        'session': {'assistant': '', 'started_at': 0.0, 'note': ''},
        'focus': {},
        'workstreams': [],
        'claims': [],
        'items': [],
        'assets': [],
        'attempts': [],
        'decisions': [],
        'blockers': [],
        'facts': [],
        'journal': [],
    }


class HandoffState:
    """Read/modify/write the handoff document.

    Writes are atomic (temp file + os.replace) because the GUI floating
    window polls this file continuously; a half-written file would make the
    indicator flicker into an error state.
    """

    def __init__(self, path):
        self.path = str(path)
        self.data = _blank()

    # ---------------------------------------------------------------- io
    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            # A missing or corrupt file must not crash a caller; the point of
            # this module is to survive bad handovers, including its own.
            self.data = _blank()
            return self.data
        if not isinstance(loaded, dict):
            self.data = _blank()
            return self.data
        merged = _blank()
        merged.update(loaded)
        for key in _LIST_KEYS:
            if not isinstance(merged.get(key), list):
                merged[key] = []
        if not isinstance(merged.get('session'), dict):
            merged['session'] = {'assistant': '', 'started_at': 0.0, 'note': ''}
        if not isinstance(merged.get('focus'), dict):
            merged['focus'] = {}
        self.data = merged
        self._migrate()
        return self.data

    def _migrate(self):
        """Bring older files up to the current schema, in memory, losslessly."""
        data = self.data
        for bucket in NOTE_BUCKETS:
            cleaned = []
            for entry in data[bucket]:
                if isinstance(entry, str):
                    entry = {'text': entry, 'at': 0.0}
                if not isinstance(entry, dict) or not entry.get('text'):
                    continue
                entry.setdefault('status', 'active')
                entry.setdefault('workstream', '')
                cleaned.append(entry)
            data[bucket] = cleaned
            for entry in cleaned:
                if not entry.get('id'):
                    entry['id'] = self._next_id(NOTE_PREFIX[bucket], cleaned)
        items = []
        for index, record in enumerate(data['items'], 1):
            if not isinstance(record, dict):
                continue
            record.setdefault('id', 'item-%d' % index)
            record.setdefault('title', record['id'])
            if record.get('status') not in VALID_STATUS:
                record['status'] = 'todo'
            record.setdefault('workstream', '')
            record.setdefault('depends_on', [])
            record.setdefault('updated_at', 0.0)
            items.append(record)
        data['items'] = items
        for key in ('workstreams', 'claims', 'assets', 'attempts', 'journal'):
            data[key] = [r for r in data[key] if isinstance(r, dict)]
        data['schema'] = SCHEMA_VERSION

    def save(self):
        self.data['updated_at'] = _now()
        self.data['schema'] = SCHEMA_VERSION
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            'w', encoding='utf-8', dir=parent or None,
            prefix='.handoff-', suffix='.tmp', delete=False)
        try:
            with handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
        return self.path

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _next_id(prefix, entries):
        highest = 0
        for entry in entries:
            ident = str(entry.get('id', ''))
            if ident.startswith(prefix) and ident[len(prefix):].isdigit():
                highest = max(highest, int(ident[len(prefix):]))
        return '%s%d' % (prefix, highest + 1)

    @staticmethod
    def _find(records, ident):
        for record in records:
            if record.get('id') == ident:
                return record
        return None

    def _require_workstream(self, ws_id):
        if ws_id and self._find(self.data['workstreams'], ws_id) is None:
            raise ValueError('未知工作流 %r：先用 `ws add` 建立，或检查拼写' % ws_id)

    def _exists_anywhere(self, ident):
        if self._find(self.data['workstreams'], ident) is not None:
            return True
        for key in ('items', 'assets', 'attempts', 'decisions', 'blockers', 'facts'):
            if self._find(self.data[key], ident) is not None:
                return True
        return False

    # ------------------------------------------------------ session/log
    def begin_session(self, assistant, note=''):
        self.data['session'] = {'assistant': assistant,
                                'started_at': _now(), 'note': note}
        self.log(assistant, '接手' + ('：' + note if note else ''))
        return self.data['session']

    def log(self, by, text):
        if not text:
            raise ValueError('日志内容不能为空')
        entry = {'at': _now(), 'by': by or '', 'text': text}
        self.data['journal'].append(entry)
        return entry

    # ------------------------------------------------------------- focus
    def set_focus(self, text, workstream='', why='', by=''):
        if not text:
            raise ValueError('焦点（下一步做什么）不能为空')
        self._require_workstream(workstream)
        self.data['focus'] = {'text': text, 'workstream': workstream,
                              'why': why, 'by': by, 'at': _now()}
        return dict(self.data['focus'])

    # ------------------------------------------------------- workstreams
    def add_workstream(self, ws_id, title, goal='', entry='', accept='',
                       next_step='', status='active', owner=''):
        if not ws_id or not title:
            raise ValueError('工作流需要 id 和标题')
        if status not in WORKSTREAM_STATUS:
            raise ValueError('Unknown workstream status: %s' % status)
        if self._find(self.data['workstreams'], ws_id) is not None:
            raise ValueError('Duplicate workstream id: %s' % ws_id)
        if self._find(self.data['items'], ws_id) is not None:
            raise ValueError('工作流 id %r 与条目重名，认领时会产生歧义' % ws_id)
        record = {'id': ws_id, 'title': title, 'goal': goal, 'entry': entry,
                  'accept': accept, 'next': next_step, 'status': status,
                  'owner': owner, 'updated_at': _now()}
        self.data['workstreams'].append(record)
        return record

    def update_workstream(self, ws_id, **fields):
        record = self._find(self.data['workstreams'], ws_id)
        if record is None:
            raise KeyError(ws_id)
        if fields.get('status') is not None and fields['status'] not in WORKSTREAM_STATUS:
            raise ValueError('Unknown workstream status: %s' % fields['status'])
        allowed = ('title', 'goal', 'entry', 'accept', 'next', 'status', 'owner')
        record.update({k: v for k, v in fields.items() if k in allowed and v is not None})
        record['updated_at'] = _now()
        return record

    # ------------------------------------------------------------- items
    def add(self, item_id, title, status='todo', owner='', detail='', evidence='',
            workstream='', depends_on=None):
        if not item_id or not title:
            raise ValueError('An item needs both an id and a title.')
        if status not in VALID_STATUS:
            raise ValueError('Unknown status: %s' % status)
        if any(existing['id'] == item_id for existing in self.data['items']):
            raise ValueError('Duplicate item id: %s' % item_id)
        if self._find(self.data['workstreams'], item_id) is not None:
            raise ValueError('条目 id %r 与工作流重名，认领时会产生歧义' % item_id)
        self._require_workstream(workstream)
        record = {'id': item_id, 'title': title, 'status': status,
                  'owner': owner, 'detail': detail, 'evidence': evidence,
                  'workstream': workstream, 'depends_on': list(depends_on or []),
                  'updated_at': _now()}
        self.data['items'].append(record)
        return record

    def update(self, item_id, **fields):
        for record in self.data['items']:
            if record['id'] != item_id:
                continue
            if 'status' in fields and fields['status'] not in VALID_STATUS:
                raise ValueError('Unknown status: %s' % fields['status'])
            if fields.get('workstream'):
                self._require_workstream(fields['workstream'])
            allowed = ('title', 'status', 'owner', 'detail', 'evidence',
                       'workstream', 'depends_on')
            record.update({k: v for k, v in fields.items() if k in allowed})
            record['updated_at'] = _now()
            return record
        raise KeyError(item_id)

    # ------------------------------------------------------------- notes
    def note(self, bucket, text, workstream='', by=''):
        if bucket not in NOTE_BUCKETS:
            raise ValueError('Unknown bucket: %s' % bucket)
        if not text:
            raise ValueError('笔记内容不能为空')
        self._require_workstream(workstream)
        entry = {'id': self._next_id(NOTE_PREFIX[bucket], self.data[bucket]),
                 'text': text, 'at': _now(), 'status': 'active',
                 'workstream': workstream, 'by': by}
        self.data[bucket].append(entry)
        return entry

    def find_note(self, note_id):
        for bucket in NOTE_BUCKETS:
            for entry in self.data[bucket]:
                if entry.get('id') == note_id:
                    return bucket, entry
        raise KeyError(note_id)

    def retire_note(self, note_id, status='superseded', replaced_by='', reason=''):
        """Stop showing a note as current without deleting it.

        `replaced_by` may point at any note, asset, attempt, item or
        workstream id, so a fact that moved into the asset registry keeps a
        trail to where it went.
        """
        if status not in NOTE_RETIRED:
            raise ValueError('status must be one of %s' % (NOTE_RETIRED,))
        _bucket, entry = self.find_note(note_id)
        if replaced_by:
            if replaced_by == note_id:
                raise ValueError('笔记不能被自己取代')
            if not self._exists_anywhere(replaced_by):
                raise ValueError('取代者 %r 不存在' % replaced_by)
        entry.update({'status': status, 'retired_at': _now(),
                      'replaced_by': replaced_by, 'reason': reason})
        return entry

    def active_notes(self, bucket, workstream=None):
        return [copy.deepcopy(e) for e in self.data[bucket]
                if e.get('status', 'active') == 'active'
                and (workstream is None or e.get('workstream', '') == workstream)]

    # ------------------------------------------------------------ claims
    def _claim_index(self, target):
        for index, claim in enumerate(self.data['claims']):
            if claim.get('target') == target:
                return index
        return None

    def claim(self, target, by, minutes=DEFAULT_CLAIM_MINUTES, note='',
              force=False, now=None):
        """Lock a workstream or item for `minutes` so others keep off it."""
        if not by:
            raise ValueError('认领必须写明是谁（by）')
        ws = self._find(self.data['workstreams'], target)
        item = self._find(self.data['items'], target)
        if ws is None and item is None:
            raise ValueError('只能认领已存在的工作流或条目：%r' % target)
        minutes = float(minutes)
        if not 0 < minutes <= MAX_CLAIM_MINUTES:
            raise ValueError('认领时长需在 0-%d 分钟之间' % MAX_CLAIM_MINUTES)
        current = _now() if now is None else now
        index = self._claim_index(target)
        if index is not None:
            existing = self.data['claims'][index]
            live = existing.get('expires_at', 0) > current
            if live and existing.get('by') != by and not force:
                raise ClaimConflict(
                    '%s 已被 %s 认领，至 %s（还剩 %d 分钟）。不要并行做同一件事；'
                    '确认对方已失联再用 --force 接管，并写明原因。'
                    % (target, existing.get('by'), _fmt(existing.get('expires_at'), '%H:%M'),
                       int((existing.get('expires_at', 0) - current) // 60)))
            if existing.get('by') != by:
                self.log(by, '接管认领 %s（原持有人 %s，%s）' % (
                    target, existing.get('by'), '强制' if live else '已过期'))
            del self.data['claims'][index]
        record = {'target': target, 'by': by, 'at': current,
                  'expires_at': current + minutes * 60, 'note': note}
        self.data['claims'].append(record)
        if ws is not None:
            ws['owner'] = by
            ws['updated_at'] = current
        if item is not None:
            item['owner'] = by
            item['updated_at'] = current
        return dict(record)

    def heartbeat(self, target, by, minutes=DEFAULT_CLAIM_MINUTES, now=None):
        index = self._claim_index(target)
        if index is None:
            raise KeyError(target)
        record = self.data['claims'][index]
        if record.get('by') != by:
            raise ClaimConflict('%s 的认领属于 %s，不是 %s' % (target, record.get('by'), by))
        current = _now() if now is None else now
        record['expires_at'] = current + float(minutes) * 60
        record['beat_at'] = current
        return dict(record)

    def release(self, target, by='', force=False, now=None):
        index = self._claim_index(target)
        if index is None:
            raise KeyError(target)
        record = self.data['claims'][index]
        current = _now() if now is None else now
        live = record.get('expires_at', 0) > current
        if by and record.get('by') != by and live and not force:
            raise ClaimConflict('%s 的认领属于 %s，仍然有效；不要替别人释放' % (target, record.get('by')))
        del self.data['claims'][index]
        return record

    def live_claims(self, now=None):
        current = _now() if now is None else now
        return [dict(c) for c in self.data['claims'] if c.get('expires_at', 0) > current]

    def expired_claims(self, now=None):
        current = _now() if now is None else now
        return [dict(c) for c in self.data['claims'] if c.get('expires_at', 0) <= current]

    # ------------------------------------------------------------ assets
    def add_asset(self, asset_id, path, workstream='', kind='', title='',
                  status='candidate', tool='', model='', seed=None, prompt_ref='',
                  supersedes='', note='', by=''):
        if not asset_id or not path:
            raise ValueError('资产需要 id 和路径')
        if status not in ASSET_STATUS:
            raise ValueError('Unknown asset status: %s' % status)
        if status in ('approved', 'rejected'):
            raise ValueError('新资产不能直接登记为 %s；用户的结论要用 asset review 记录（附用户原话）' % status)
        if self._find(self.data['assets'], asset_id) is not None:
            raise ValueError('Duplicate asset id: %s' % asset_id)
        self._require_workstream(workstream)
        if supersedes and self._find(self.data['assets'], supersedes) is None:
            raise ValueError('被取代的资产 %r 不存在' % supersedes)
        current = _now()
        record = {'id': asset_id, 'path': path, 'workstream': workstream,
                  'kind': kind, 'title': title, 'status': status,
                  'tool': tool, 'model': model, 'seed': seed,
                  'prompt_ref': prompt_ref, 'supersedes': supersedes,
                  'note': note, 'by': by, 'review': {},
                  'created_at': current, 'updated_at': current}
        self.data['assets'].append(record)
        return record

    def set_asset_status(self, asset_id, status, note=''):
        if status not in AI_ASSET_STATUS:
            raise ValueError('%s 只能由用户决定：用 asset review 记录用户原话' % status)
        record = self._find(self.data['assets'], asset_id)
        if record is None:
            raise KeyError(asset_id)
        record['status'] = status
        if note:
            record['note'] = note
        record['updated_at'] = _now()
        return record

    def review_asset(self, asset_id, verdict, feedback, by='user'):
        """Record the user's verdict. Approved assets become canon."""
        if verdict not in ('approved', 'rejected'):
            raise ValueError('verdict must be approved or rejected')
        if not feedback:
            raise ValueError('审阅结论必须附用户原话或出处（feedback）')
        record = self._find(self.data['assets'], asset_id)
        if record is None:
            raise KeyError(asset_id)
        current = _now()
        record['status'] = verdict
        record['review'] = {'verdict': verdict, 'feedback': feedback,
                            'by': by or 'user', 'at': current}
        record['updated_at'] = current
        if verdict == 'approved' and record.get('supersedes'):
            old = self._find(self.data['assets'], record['supersedes'])
            if old is not None and old.get('status') != 'superseded':
                old['status'] = 'superseded'
                old['superseded_by'] = asset_id
                old['updated_at'] = current
        return record

    def list_assets(self, status=None, workstream=None):
        return [copy.deepcopy(a) for a in self.data['assets']
                if (status is None or a.get('status') == status)
                and (workstream is None or a.get('workstream', '') == workstream)]

    # ---------------------------------------------------------- attempts
    def add_attempt(self, what, workstream='', result='rejected', reason='',
                    lesson='', by='', refs=''):
        if not what:
            raise ValueError('尝试记录需要写明做了什么')
        if result not in ATTEMPT_RESULTS:
            raise ValueError('Unknown attempt result: %s' % result)
        self._require_workstream(workstream)
        record = {'id': self._next_id('A', self.data['attempts']), 'what': what,
                  'workstream': workstream, 'result': result, 'reason': reason,
                  'lesson': lesson, 'by': by, 'refs': refs, 'at': _now()}
        self.data['attempts'].append(record)
        return record

    # ------------------------------------------------------------ views
    def by_status(self, status):
        return [copy.deepcopy(r) for r in self.data['items']
                if r['status'] == status]

    def stale_doing(self, limit=STALE_DOING_SECONDS, now=None):
        """Items claimed as in-progress that have gone quiet.

        This is the signal that an assistant was interrupted: the next one
        should verify the real state rather than trust the label.
        """
        current = _now() if now is None else now
        return [copy.deepcopy(r) for r in self.data['items']
                if r['status'] == 'doing'
                and (current - r.get('updated_at', 0)) > limit]

    def summary(self, now=None):
        counts = {status: 0 for status in VALID_STATUS}
        for record in self.data['items']:
            counts[record['status']] = counts.get(record['status'], 0) + 1
        doing = self.by_status('doing')
        focus = self.data.get('focus') or {}
        return {'project': self.data.get('project', ''),
                'goal': self.data.get('goal', ''),
                'counts': counts,
                'current': doing[0]['title'] if doing else None,
                'blockers': len(self.active_notes('blockers')),
                'stale': len(self.stale_doing(now=now)),
                'updated_at': self.data.get('updated_at', 0.0),
                'focus': focus.get('text') or None,
                'claims': len(self.live_claims(now=now)),
                'expired_claims': len(self.expired_claims(now=now)),
                'pending_review': len(self.list_assets(status='pending_review')),
                'approved': len(self.list_assets(status='approved')),
                'workstreams_active': sum(1 for w in self.data['workstreams']
                                          if w.get('status') == 'active')}

    def lint(self, now=None, root=None):
        """Health check. Returns a list of (level, message); level is
        'error' (the handover is lying), 'warn' (likely trouble) or 'info'."""
        current = _now() if now is None else now
        problems = []
        focus = self.data.get('focus') or {}
        if not focus.get('text'):
            problems.append(('warn', '没有设置焦点（focus）：新 AI 不知道从哪里开始'))
        elif focus.get('workstream'):
            ws = self._find(self.data['workstreams'], focus['workstream'])
            if ws is None:
                problems.append(('error', '焦点指向不存在的工作流 %s' % focus['workstream']))
            elif ws.get('status') == 'done':
                problems.append(('warn', '焦点指向已完成的工作流 %s' % focus['workstream']))
        for record in self.stale_doing(now=current):
            problems.append(('warn', '条目 %s 进行中但超过 30 分钟无更新' % record['id']))
        ids = {r['id'] for r in self.data['items']}
        for record in self.data['items']:
            if record['status'] == 'done' and not record.get('evidence'):
                problems.append(('error', '条目 %s 标为完成却没有实测证据' % record['id']))
            if record['status'] == 'doing' and not record.get('owner'):
                problems.append(('warn', '条目 %s 进行中但没有负责人' % record['id']))
            if record['status'] == 'blocked' and not record.get('detail'):
                problems.append(('warn', '条目 %s 阻塞但没写原因' % record['id']))
            for dep in record.get('depends_on') or []:
                if dep not in ids:
                    problems.append(('warn', '条目 %s 依赖不存在的 %s' % (record['id'], dep)))
        for claim in self.expired_claims(now=current):
            problems.append(('warn', '认领 %s（%s）已过期 %d 分钟' % (
                claim['target'], claim.get('by'), int((current - claim.get('expires_at', 0)) // 60))))
        for ws in self.data['workstreams']:
            if ws.get('status') == 'active' and not ws.get('next'):
                problems.append(('warn', '工作流 %s 活跃但没写下一步' % ws['id']))
        for asset in self.data['assets']:
            if asset.get('status') in ('approved', 'rejected') and \
                    not (asset.get('review') or {}).get('feedback'):
                problems.append(('error', '资产 %s 为%s，却没有用户审阅记录' % (
                    asset['id'], _STATUS_ZH.get(asset['status'], asset['status']))))
            if root and asset.get('path'):
                path = asset['path']
                full = path if os.path.isabs(path) else os.path.join(root, path)
                if not os.path.exists(full):
                    problems.append(('warn', '资产 %s 的文件不存在：%s' % (asset['id'], path)))
        seen = {}
        for bucket in NOTE_BUCKETS:
            for entry in self.data[bucket]:
                if entry.get('status', 'active') != 'active':
                    continue
                key = entry['text'].strip()
                if key in seen:
                    problems.append(('warn', '笔记 %s 与 %s 内容重复' % (entry['id'], seen[key])))
                else:
                    seen[key] = entry['id']
        pending = self.list_assets(status='pending_review')
        if pending:
            problems.append(('info', '%d 个资产等待用户审阅' % len(pending)))
        return problems

    # ---------------------------------------------------------- markdown
    @staticmethod
    def _ws_tag(record):
        return ('  `[%s]`' % record['workstream']) if record.get('workstream') else ''

    def find_any(self, ident):
        """Every record with this id: notes, attempts, items, workstreams, assets."""
        hits = []
        for bucket in NOTE_BUCKETS + ('attempts', 'items', 'workstreams', 'assets'):
            for record in self.data.get(bucket, []):
                if record.get('id') == ident:
                    hits.append((bucket, copy.deepcopy(record)))
        return hits

    @staticmethod
    def _index_line(records, text_key):
        return ' · '.join('`%s` %s' % (r['id'], _short(r.get(text_key), COMPACT_INDEX_CLIP))
                          for r in records)

    def brief_markdown(self, now=None):
        """The first screen: what to do, what waits on the user, who holds what."""
        current = _now() if now is None else now
        data = self.data
        lines = []
        add = lines.append
        add('# 交接简报 · %s' % (data.get('project') or '(未命名项目)'))
        add('')
        stamp = data.get('updated_at', 0)
        add('> 自动生成，请勿手工编辑正文；用 `handoff_cli.py` 修改 `state.json` 后会自动重新生成。')
        add('> 最后更新：%s' % (_fmt(stamp, '%Y-%m-%d %H:%M:%S') if stamp else '尚未更新'))
        add('')
        if data.get('goal'):
            add('## 总目标')
            add('')
            add(data['goal'])
            add('')

        add('## 🎯 现在做什么')
        add('')
        focus = data.get('focus') or {}
        if focus.get('text'):
            add('- **下一步：%s**' % focus['text'])
            if focus.get('workstream'):
                add('- 工作流：`%s`' % focus['workstream'])
            if focus.get('why'):
                add('- 原因：%s' % focus['why'])
            add('- 设定：%s · %s' % (focus.get('by') or '—', _fmt(focus.get('at'))))
        else:
            add('_（未设置焦点：先向用户确认要做什么，再用 `focus` 写下来）_')
        add('')

        blockers = self.active_notes('blockers')
        pending = self.list_assets(status='pending_review')
        add('## ⛔ 等待用户（不要替用户做决定）')
        add('')
        if not blockers and not pending:
            add('_（无）_')
        for entry in blockers:
            add('- `%s` %s' % (entry['id'], entry['text']))
        if pending:
            shown = '、'.join('`%s`' % a['id'] for a in pending[:12])
            more = ' 等' if len(pending) > 12 else ''
            add('- 待审阅资产 %d 个：%s%s（路径见下方「待用户审阅的资产」）' % (len(pending), shown, more))
        add('')

        live = self.live_claims(now=current)
        expired = self.expired_claims(now=current)
        add('## 🔒 认领中（别人认领的东西不要碰）')
        add('')
        if not live and not expired:
            add('_（无）_')
        for claim in live:
            left = int((claim['expires_at'] - current) // 60)
            add('- `%s` ← %s，至 %s（剩 %d 分钟）%s' % (
                claim['target'], claim.get('by'), _fmt(claim['expires_at'], '%H:%M'), left,
                ('：' + claim['note']) if claim.get('note') else ''))
        for claim in expired:
            ago = int((current - claim.get('expires_at', 0)) // 60)
            add('- ⚠️ `%s` ← %s，**已过期 %d 分钟**：先核实对方是否还在做，再 `claim --force` 接管' % (
                claim['target'], claim.get('by'), ago))
        add('')

        if data['workstreams']:
            holders = {c['target']: c.get('by') for c in live}
            add('## 🧭 工作流')
            add('')
            add('| 工作流 | 状态 | 负责/认领 | 下一步 | 入口 |')
            add('|---|---|---|---|---|')
            for ws in data['workstreams']:
                holder = holders.get(ws['id']) or ws.get('owner') or '—'
                entry = ('`%s`' % _cell(ws['entry'])) if ws.get('entry') else '—'
                add('| `%s` %s | %s | %s | %s | %s |' % (
                    ws['id'], _cell(ws.get('title')), _STATUS_ZH.get(ws.get('status'), ws.get('status')),
                    _cell(holder), _cell(ws.get('next')), entry))
            add('')
        return '\n'.join(lines)

    def compact_markdown(self, now=None):
        """L2 for agents (`show`, HANDOFF.md): everything that steers the work, bounded.

        Long-running projects accumulate dozens of facts, attempts and review
        assets; printing all of them in full cost the next agent 10-20k tokens
        before it did anything. Here every list is capped and every line
        clipped, but every record keeps its id: `get <id>` prints one in full,
        `show --full` / HANDOFF_FULL.md prints everything.
        """
        current = _now() if now is None else now
        data = self.data
        lines = [self.brief_markdown(now=current)]
        add = lines.append
        clip = _short

        session = data.get('session') or {}
        if session.get('assistant'):
            add('> 上一位：%s · %s%s' % (session['assistant'], _fmt(session.get('started_at')),
                                        ('：' + clip(session['note'], 80)) if session.get('note') else ''))
            add('')

        accepts = [ws for ws in data['workstreams'] if ws.get('accept') or ws.get('goal')]
        if accepts:
            add('## 工作流目标与验收标准')
            add('')
            for ws in accepts:
                add('- `%s` %s%s%s' % (ws['id'], ws.get('title', ''),
                                       ('｜目标：' + clip(ws['goal'], 90)) if ws.get('goal') else '',
                                       ('｜验收：' + clip(ws['accept'], 120)) if ws.get('accept') else ''))
            add('')

        stale = {r['id'] for r in self.stale_doing(now=current)}
        add('## 正在进行')
        add('')
        doing = self.by_status('doing')
        if not doing:
            add('_（无）_')
        for record in doing:
            flag = '  ⚠️ **超过 30 分钟无更新，先核实**' if record['id'] in stale else ''
            extra = [x for x in (('负责：' + record['owner']) if record.get('owner') else '',
                                 ('细节：' + clip(record['detail'], 100)) if record.get('detail') else '') if x]
            add('- **%s**%s  `%s`%s%s' % (record['title'], self._ws_tag(record), record['id'], flag,
                                          ('｜' + '｜'.join(extra)) if extra else ''))
        add('')

        add('## 待办（按优先级）')
        add('')
        todo = self.by_status('todo')
        if not todo:
            add('_（无）_')
        for index, record in enumerate(todo, 1):
            tail = ''
            if record.get('depends_on'):
                tail += '｜依赖：' + '、'.join(record['depends_on'])
            if record.get('detail'):
                tail += '｜' + clip(record['detail'], 100)
            add('%d. %s%s  `%s`%s' % (index, record['title'], self._ws_tag(record), record['id'], tail))
        add('')

        blocked = self.by_status('blocked')
        if blocked:
            add('## 被阻塞')
            add('')
            for record in blocked:
                add('- %s%s  `%s`%s' % (record['title'], self._ws_tag(record), record['id'],
                                        ('｜' + clip(record['detail'], 100)) if record.get('detail') else ''))
            add('')

        approved = self.list_assets(status='approved')
        add('## ✅ 已认可资产（正典：只能在这些基础上继续）')
        add('')
        if not approved:
            add('_（无）_')
        for asset in approved:
            review = asset.get('review') or {}
            add('- `%s`%s — `%s`%s' % (asset['id'], self._ws_tag(asset), asset['path'],
                                      ('  ·  用户：%s' % clip(review['feedback'], 80)) if review.get('feedback') else ''))
        add('')

        pending = self.list_assets(status='pending_review')
        if pending:
            add('## ⏳ 待用户审阅的资产（%d 个，最近 %d 个）' % (len(pending), min(len(pending), COMPACT_ASSETS)))
            add('')
            for asset in pending[-COMPACT_ASSETS:]:
                add('- `%s`%s — `%s`' % (asset['id'], self._ws_tag(asset), asset['path']))
            if len(pending) > COMPACT_ASSETS:
                add('- …全部：`asset list --status pending_review`')
            add('')

        rejected = self.list_assets(status='rejected')
        if rejected:
            add('## 🚫 已被用户否决的资产（不要再当参考；%d 个）' % len(rejected))
            add('')
            for asset in rejected[-COMPACT_ASSETS:]:
                review = asset.get('review') or {}
                add('- `%s` — 用户：%s' % (asset['id'], clip(review.get('feedback', '—'), 60)))
            if len(rejected) > COMPACT_ASSETS:
                add('- …更早 %d 个：%s' % (len(rejected) - COMPACT_ASSETS,
                                          '、'.join('`%s`' % a['id'] for a in rejected[:-COMPACT_ASSETS])))
            add('')

        attempts = data['attempts']
        if attempts:
            add('## ❌ 已否决 / 失败的尝试（不要重复；%d 条）' % len(attempts))
            add('')
            for attempt in attempts[-COMPACT_ATTEMPTS:]:
                line = '- `%s`%s %s → **%s**' % (
                    attempt['id'], self._ws_tag(attempt), clip(attempt['what'], 60),
                    _STATUS_ZH.get(attempt.get('result'), attempt.get('result')))
                if attempt.get('lesson'):
                    line += '；教训：%s' % clip(attempt['lesson'], 80)
                elif attempt.get('reason'):
                    line += '：%s' % clip(attempt['reason'], 80)
                add(line)
            if len(attempts) > COMPACT_ATTEMPTS:
                add('- 更早：' + self._index_line(attempts[:-COMPACT_ATTEMPTS], 'what'))
            add('')

        decisions = self.active_notes('decisions')
        if decisions:
            add('## 已定方案（不要推翻重来）')
            add('')
            for entry in decisions:
                add('- `%s`%s %s' % (entry['id'], self._ws_tag(entry), clip(entry['text'], COMPACT_CLIP)))
            add('')
        facts = self.active_notes('facts')
        if facts:
            add('## 环境事实（重新发现代价很高；%d 条）' % len(facts))
            add('')
            for entry in facts[-COMPACT_FACTS:]:
                add('- `%s`%s %s' % (entry['id'], self._ws_tag(entry), clip(entry['text'], COMPACT_CLIP)))
            if len(facts) > COMPACT_FACTS:
                add('- 更早：' + self._index_line(facts[:-COMPACT_FACTS], 'text'))
            add('')

        done = sorted(self.by_status('done'), key=lambda r: r.get('updated_at', 0), reverse=True)
        if done:
            add('## 已完成（%d 项，最近 %d 项）' % (len(done), min(len(done), COMPACT_DONE)))
            add('')
            for record in done[:COMPACT_DONE]:
                add('- %s%s  `%s`' % (record['title'], self._ws_tag(record), record['id']))
            add('')

        journal = data['journal'][-COMPACT_JOURNAL:]
        if journal:
            add('## 最近会话日志')
            add('')
            for entry in reversed(journal):
                add('- %s · %s：%s' % (_fmt(entry.get('at')), entry.get('by') or '—',
                                      clip(entry.get('text', ''), 100)))
            add('')

        retired = sum(1 for bucket in NOTE_BUCKETS for e in data[bucket]
                      if e.get('status', 'active') != 'active')
        add('> 紧凑视图%s。单条全文：`get <编号>`；全部内容：`show --full` 或 HANDOFF_FULL.md。' % (
            '（另有 %d 条已作废笔记未列出）' % retired if retired else ''))
        return '\n'.join(lines)

    def to_markdown(self, now=None):
        """Render the full briefing a human (or the next assistant) reads first."""
        current = _now() if now is None else now
        data = self.data
        lines = [self.brief_markdown(now=current)]
        add = lines.append

        session = data.get('session') or {}
        if session.get('assistant'):
            add('## 上一位负责的 AI')
            add('')
            add('- 身份：%s' % session['assistant'])
            if session.get('started_at'):
                add('- 接手时间：%s' % _fmt(session['started_at'], '%Y-%m-%d %H:%M:%S'))
            if session.get('note'):
                add('- 备注：%s' % session['note'])
            add('')

        accepts = [ws for ws in data['workstreams'] if ws.get('accept') or ws.get('goal')]
        if accepts:
            add('## 工作流目标与验收标准')
            add('')
            for ws in accepts:
                add('- `%s` %s' % (ws['id'], ws.get('title', '')))
                if ws.get('goal'):
                    add('  - 目标：%s' % ws['goal'])
                if ws.get('accept'):
                    add('  - 验收：%s' % ws['accept'])
            add('')

        stale = {r['id'] for r in self.stale_doing(now=current)}
        add('## 正在进行')
        add('')
        doing = self.by_status('doing')
        if not doing:
            add('_（无）_')
        for record in doing:
            flag = '  ⚠️ **超过 30 分钟无更新，接手前请先核实真实状态**' if record['id'] in stale else ''
            add('- **%s**%s  `%s`%s' % (record['title'], self._ws_tag(record), record['id'], flag))
            if record.get('owner'):
                add('  - 负责：%s' % record['owner'])
            if record.get('detail'):
                add('  - 细节：%s' % record['detail'])
            if record.get('evidence'):
                add('  - 证据：%s' % record['evidence'])
        add('')

        add('## 待办（按优先级）')
        add('')
        todo = self.by_status('todo')
        if not todo:
            add('_（无）_')
        for index, record in enumerate(todo, 1):
            add('%d. %s%s  `%s`' % (index, record['title'], self._ws_tag(record), record['id']))
            if record.get('depends_on'):
                add('   - 依赖：%s' % '、'.join(record['depends_on']))
            if record.get('detail'):
                add('   - %s' % record['detail'])
        add('')

        blocked = self.by_status('blocked')
        if blocked:
            add('## 被阻塞')
            add('')
            for record in blocked:
                add('- %s%s  `%s`' % (record['title'], self._ws_tag(record), record['id']))
                if record.get('detail'):
                    add('  - %s' % record['detail'])
            add('')

        approved = self.list_assets(status='approved')
        add('## ✅ 已认可资产（正典：只能在这些基础上继续）')
        add('')
        if not approved:
            add('_（无）_')
        for asset in approved:
            review = asset.get('review') or {}
            add('- `%s`%s %s — `%s`%s' % (
                asset['id'], self._ws_tag(asset), asset.get('title', ''), asset['path'],
                ('  ·  用户：%s' % review['feedback']) if review.get('feedback') else ''))
        add('')

        pending = self.list_assets(status='pending_review')
        if pending:
            add('## ⏳ 待用户审阅的资产')
            add('')
            for asset in pending:
                origin = ' / '.join(str(x) for x in (asset.get('tool'), asset.get('model'),
                                                    ('seed %s' % asset['seed']) if asset.get('seed') not in (None, '') else '')
                                    if x)
                add('- `%s`%s %s — `%s`%s' % (
                    asset['id'], self._ws_tag(asset), asset.get('title', ''), asset['path'],
                    ('  ·  来源：%s' % origin) if origin else ''))
            add('')

        rejected = self.list_assets(status='rejected')
        if rejected:
            add('## 🚫 已被用户否决的资产（不要再当参考）')
            add('')
            for asset in rejected:
                review = asset.get('review') or {}
                add('- `%s`%s %s — `%s`  ·  用户：%s' % (
                    asset['id'], self._ws_tag(asset), asset.get('title', ''), asset['path'],
                    review.get('feedback', '—')))
            add('')

        if data['attempts']:
            add('## ❌ 已否决 / 失败的尝试（不要重复）')
            add('')
            for attempt in data['attempts']:
                line = '- `%s`%s %s → **%s**' % (
                    attempt['id'], self._ws_tag(attempt), attempt['what'],
                    _STATUS_ZH.get(attempt.get('result'), attempt.get('result')))
                if attempt.get('reason'):
                    line += '：%s' % attempt['reason']
                if attempt.get('lesson'):
                    line += '；教训：%s' % attempt['lesson']
                add(line)
            add('')

        decisions = self.active_notes('decisions')
        if decisions:
            add('## 已定方案（不要推翻重来）')
            add('')
            for entry in decisions:
                add('- `%s`%s %s' % (entry['id'], self._ws_tag(entry), entry['text']))
            add('')
        facts = self.active_notes('facts')
        if facts:
            add('## 环境事实（重新发现代价很高）')
            add('')
            for entry in facts:
                add('- `%s`%s %s' % (entry['id'], self._ws_tag(entry), entry['text']))
            add('')

        add('## 已完成')
        add('')
        done = sorted(self.by_status('done'), key=lambda r: r.get('updated_at', 0), reverse=True)
        if not done:
            add('_（无）_')
        for record in done[:BRIEF_DONE_LIMIT]:
            line = '- %s%s' % (record['title'], self._ws_tag(record))
            if record.get('evidence'):
                line += '  ·  证据：%s' % record['evidence']
            add(line)
        if len(done) > BRIEF_DONE_LIMIT:
            add('- …另有 %d 项较早的已完成条目，见 state.json' % (len(done) - BRIEF_DONE_LIMIT))
        add('')

        journal = data['journal'][-BRIEF_JOURNAL_LIMIT:]
        if journal:
            add('## 最近会话日志')
            add('')
            for entry in reversed(journal):
                add('- %s · %s：%s' % (_fmt(entry.get('at')), entry.get('by') or '—', entry.get('text', '')))
            add('')

        retired = [e for bucket in NOTE_BUCKETS for e in data[bucket]
                   if e.get('status', 'active') != 'active']
        if retired:
            add('## 已作废 / 已解决的笔记（仅供追溯，不要再引用）')
            add('')
            for entry in retired:
                tail = ' → %s' % _STATUS_ZH.get(entry['status'], entry['status'])
                if entry.get('replaced_by'):
                    tail += '，见 `%s`' % entry['replaced_by']
                if entry.get('reason'):
                    tail += '（%s）' % entry['reason']
                add('- ~~`%s` %s~~%s' % (entry['id'], _short(entry['text']), tail))
            add('')
        return '\n'.join(lines)


def load(path):
    state = HandoffState(path)
    state.load()
    return state
