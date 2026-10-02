"""Tests for handoff schema 2: focus, workstreams, claims, assets, attempts,
note lifecycle, lint, v1 migration and the CLI guard rails."""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import handoff_cli
from handoff import (ClaimConflict, HandoffState, SCHEMA_VERSION,
                     STALE_DOING_SECONDS, load)

V1_STATE = {
    'schema': 1,
    'project': 'Old project',
    'goal': 'g',
    'updated_at': 1.0,
    'session': {'assistant': 'old-agent', 'started_at': 1.0, 'note': ''},
    'items': [{'id': 'a', 'title': 'Old item', 'status': 'done', 'owner': '',
               'detail': '', 'evidence': 'seen', 'updated_at': 1.0}],
    'decisions': [{'text': '用户选了第二版脸', 'at': 1.0}],
    'blockers': [{'text': '等用户审阅', 'at': 1.0}],
    'facts': [{'text': 'CDN 403', 'at': 1.0}, {'text': 'write_file 仅文本', 'at': 2.0}],
}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='handoff-v2-')
        self.path = os.path.join(self.dir, 'state.json')

    def test_v1_file_gets_note_ids_and_new_sections(self):
        with open(self.path, 'w', encoding='utf-8') as handle:
            json.dump(V1_STATE, handle, ensure_ascii=False)
        state = load(self.path)
        self.assertEqual(state.data['schema'], SCHEMA_VERSION)
        self.assertEqual([e['id'] for e in state.data['facts']], ['F1', 'F2'])
        self.assertEqual(state.data['decisions'][0]['id'], 'D1')
        self.assertEqual(state.data['blockers'][0]['status'], 'active')
        for key in ('workstreams', 'claims', 'assets', 'attempts', 'journal'):
            self.assertEqual(state.data[key], [])
        self.assertEqual(state.data['items'][0]['workstream'], '')

    def test_migrated_ids_are_stable_across_save(self):
        with open(self.path, 'w', encoding='utf-8') as handle:
            json.dump(V1_STATE, handle, ensure_ascii=False)
        state = load(self.path)
        state.save()
        again = load(self.path)
        self.assertEqual([e['id'] for e in again.data['facts']], ['F1', 'F2'])
        self.assertEqual(again.note('facts', 'new')['id'], 'F3')

    def test_string_notes_and_bad_entries_survive(self):
        payload = dict(V1_STATE, facts=['plain string fact', 42, {'no': 'text'}])
        with open(self.path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle)
        state = load(self.path)
        self.assertEqual([e['text'] for e in state.data['facts']], ['plain string fact'])


class NoteLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.state = HandoffState(os.path.join(tempfile.mkdtemp(), 'state.json'))

    def test_ids_increment_per_bucket(self):
        self.assertEqual(self.state.note('facts', 'x')['id'], 'F1')
        self.assertEqual(self.state.note('facts', 'y')['id'], 'F2')
        self.assertEqual(self.state.note('decisions', 'z')['id'], 'D1')

    def test_retired_note_leaves_body_but_keeps_trace(self):
        self.state.note('facts', '插件连不上')
        self.state.note('facts', '根因是 Blender 没启动')
        self.state.retire_note('F1', replaced_by='F2', reason='根因已查明')
        text = self.state.to_markdown()
        body, _, retired = text.partition('已作废 / 已解决的笔记')
        self.assertNotIn('插件连不上', body)
        self.assertIn('插件连不上', retired)
        self.assertIn('F2', retired)
        self.assertEqual(len(self.state.active_notes('facts')), 1)

    def test_retire_requires_existing_replacement(self):
        self.state.note('facts', 'x')
        with self.assertRaises(ValueError):
            self.state.retire_note('F1', replaced_by='F9')
        with self.assertRaises(ValueError):
            self.state.retire_note('F1', replaced_by='F1')
        with self.assertRaises(KeyError):
            self.state.retire_note('F7')

    def test_replacement_may_be_an_asset(self):
        self.state.add_asset('pkg', 'a/b')
        self.state.note('facts', '阶段包在 a/b')
        self.state.retire_note('F1', replaced_by='pkg')
        self.assertEqual(self.state.data['facts'][0]['replaced_by'], 'pkg')

    def test_resolved_blocker_leaves_waiting_section(self):
        self.state.note('blockers', '等用户选 A/B')
        self.state.retire_note('B1', status='resolved', reason='用户选了 A')
        brief = self.state.brief_markdown()
        self.assertNotIn('等用户选 A/B', brief)


class WorkstreamAndFocusTests(unittest.TestCase):
    def setUp(self):
        self.state = HandoffState(os.path.join(tempfile.mkdtemp(), 'state.json'))
        self.state.add_workstream('keyframes', '关键帧', next_step='出首轮', entry='art/kf/README.md')

    def test_focus_comes_before_item_sections(self):
        self.state.set_focus('用 NovelAI 出首轮英雄帧', workstream='keyframes', why='用户指派')
        self.state.add('t', '一个待办')
        text = self.state.to_markdown()
        self.assertLess(text.index('用 NovelAI 出首轮英雄帧'), text.index('## 待办'))
        self.assertIn('art/kf/README.md', self.state.brief_markdown())

    def test_unknown_workstream_rejected_everywhere(self):
        with self.assertRaises(ValueError):
            self.state.set_focus('x', workstream='nope')
        with self.assertRaises(ValueError):
            self.state.add('t', 'x', workstream='nope')
        with self.assertRaises(ValueError):
            self.state.note('facts', 'x', workstream='nope')
        with self.assertRaises(ValueError):
            self.state.add_asset('a', 'p', workstream='nope')

    def test_ids_do_not_collide_between_items_and_workstreams(self):
        with self.assertRaises(ValueError):
            self.state.add('keyframes', 'clash')
        self.state.add('item1', 'x')
        with self.assertRaises(ValueError):
            self.state.add_workstream('item1', 'clash')

    def test_update_workstream_validates_status(self):
        with self.assertRaises(ValueError):
            self.state.update_workstream('keyframes', status='finished')
        self.state.update_workstream('keyframes', status='paused', next=None)
        self.assertEqual(self.state.data['workstreams'][0]['next'], '出首轮')


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.state = HandoffState(os.path.join(tempfile.mkdtemp(), 'state.json'))
        self.state.add_workstream('keyframes', '关键帧', next_step='n')
        self.t0 = 1_000_000.0

    def test_second_assistant_is_refused_while_claim_is_live(self):
        self.state.claim('keyframes', 'agent-a', minutes=60, now=self.t0)
        with self.assertRaises(ClaimConflict):
            self.state.claim('keyframes', 'agent-b', now=self.t0 + 60)
        self.assertEqual(self.state.data['workstreams'][0]['owner'], 'agent-a')

    def test_expired_claim_can_be_taken_over_and_is_logged(self):
        self.state.claim('keyframes', 'agent-a', minutes=10, now=self.t0)
        self.state.claim('keyframes', 'agent-b', now=self.t0 + 11 * 60)
        self.assertEqual(len(self.state.data['claims']), 1)
        self.assertEqual(self.state.data['claims'][0]['by'], 'agent-b')
        self.assertIn('接管认领', self.state.data['journal'][-1]['text'])

    def test_force_takes_over_live_claim(self):
        self.state.claim('keyframes', 'agent-a', now=self.t0)
        self.state.claim('keyframes', 'agent-b', force=True, now=self.t0 + 1)
        self.assertEqual(self.state.data['claims'][0]['by'], 'agent-b')

    def test_same_holder_renews(self):
        self.state.claim('keyframes', 'agent-a', minutes=10, now=self.t0)
        self.state.claim('keyframes', 'agent-a', minutes=30, now=self.t0 + 5)
        self.assertEqual(len(self.state.data['claims']), 1)
        self.assertAlmostEqual(self.state.data['claims'][0]['expires_at'], self.t0 + 5 + 1800)

    def test_heartbeat_and_release_rules(self):
        self.state.claim('keyframes', 'agent-a', minutes=10, now=self.t0)
        with self.assertRaises(ClaimConflict):
            self.state.heartbeat('keyframes', 'agent-b', now=self.t0 + 1)
        self.state.heartbeat('keyframes', 'agent-a', minutes=20, now=self.t0 + 60)
        self.assertAlmostEqual(self.state.data['claims'][0]['expires_at'], self.t0 + 60 + 1200)
        with self.assertRaises(ClaimConflict):
            self.state.release('keyframes', by='agent-b', now=self.t0 + 120)
        self.state.release('keyframes', by='agent-a', now=self.t0 + 120)
        self.assertEqual(self.state.data['claims'], [])

    def test_claim_needs_known_target_and_sane_duration(self):
        with self.assertRaises(ValueError):
            self.state.claim('ghost', 'agent-a')
        with self.assertRaises(ValueError):
            self.state.claim('keyframes', 'agent-a', minutes=0)
        with self.assertRaises(ValueError):
            self.state.claim('keyframes', '')

    def test_brief_shows_live_and_expired_claims(self):
        self.state.add('item', 'x')
        now = time.time()
        self.state.claim('keyframes', 'agent-a', minutes=30, now=now)
        self.state.claim('item', 'agent-b', minutes=1, now=now - 600)
        brief = self.state.brief_markdown(now=now)
        self.assertIn('`keyframes` ← agent-a', brief)
        self.assertIn('已过期', brief)


class AssetAndAttemptTests(unittest.TestCase):
    def setUp(self):
        self.state = HandoffState(os.path.join(tempfile.mkdtemp(), 'state.json'))
        self.state.add_workstream('keyframes', '关键帧', next_step='n')

    def test_cannot_register_as_approved_or_self_approve(self):
        with self.assertRaises(ValueError):
            self.state.add_asset('k1', 'k1.png', status='approved')
        self.state.add_asset('k1', 'k1.png', workstream='keyframes')
        with self.assertRaises(ValueError):
            self.state.set_asset_status('k1', 'approved')
        with self.assertRaises(ValueError):
            self.state.review_asset('k1', 'approved', '')

    def test_approval_supersedes_previous_version(self):
        self.state.add_asset('k1v1', 'v1.png', workstream='keyframes')
        self.state.add_asset('k1v2', 'v2.png', workstream='keyframes', supersedes='k1v1',
                             status='pending_review', tool='novelai', model='nai-diffusion-5-full', seed=7)
        self.assertIn('k1v2', self.state.brief_markdown())
        self.state.review_asset('k1v2', 'approved', '用户：第二张可以')
        self.assertEqual(self.state.list_assets(status='superseded')[0]['id'], 'k1v1')
        text = self.state.to_markdown()
        self.assertIn('用户：第二张可以', text)
        self.assertIn('已认可资产', text)

    def test_rejected_asset_is_listed_as_do_not_use(self):
        self.state.add_asset('h05', 'h05.png', status='pending_review')
        self.state.review_asset('h05', 'rejected', '帽子完全不对')
        self.assertIn('不要再当参考', self.state.to_markdown())

    def test_attempt_ids_and_rendering(self):
        first = self.state.add_attempt('纯文生图走廊两版', workstream='keyframes',
                                       reason='木墙裙不对', lesson='先定母版')
        second = self.state.add_attempt('白盒合成两版', result='failed')
        self.assertEqual((first['id'], second['id']), ('A1', 'A2'))
        text = self.state.to_markdown()
        self.assertIn('不要重复', text)
        self.assertIn('教训：先定母版', text)
        with self.assertRaises(ValueError):
            self.state.add_attempt('x', result='meh')


class LintAndSummaryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = HandoffState(os.path.join(self.dir, 'state.json'))

    def test_lint_flags_lies_and_gaps(self):
        self.state.add('fake', '假完成', status='done')
        self.state.add('lost', '没人负责', status='doing')
        self.state.add('why', '不知为何阻塞', status='blocked')
        self.state.add_workstream('kf', '关键帧')
        self.state.add_asset('ghost', 'missing/file.png')
        self.state.data['assets'].append({'id': 'hand', 'path': 'x', 'status': 'approved', 'review': {}})
        self.state.claim('kf', 'agent', minutes=1, now=time.time() - 3600)
        levels = {}
        for level, message in self.state.lint(root=self.dir):
            levels.setdefault(level, []).append(message)
        joined = '\n'.join(sum(levels.values(), []))
        self.assertIn('fake', '\n'.join(levels['error']))
        self.assertIn('hand', '\n'.join(levels['error']))
        for needle in ('焦点', 'lost', 'why', '已过期', 'kf', 'missing/file.png'):
            self.assertIn(needle, joined)

    def test_lint_reports_stale_and_duplicates(self):
        self.state.set_focus('x')
        self.state.add('s', 'stale', status='doing', owner='a')
        self.state.data['items'][0]['updated_at'] = time.time() - STALE_DOING_SECONDS - 60
        self.state.note('facts', '同一句话')
        self.state.note('facts', '同一句话')
        messages = [m for _, m in self.state.lint()]
        self.assertTrue(any('30 分钟' in m for m in messages))
        self.assertTrue(any('重复' in m for m in messages))

    def test_summary_keeps_v1_keys(self):
        summary = self.state.summary()
        for key in ('project', 'goal', 'counts', 'current', 'blockers', 'stale', 'updated_at',
                    'focus', 'claims', 'expired_claims', 'pending_review', 'approved'):
            self.assertIn(key, summary)

    def test_brief_excludes_history(self):
        self.state.add('d', '历史项', status='done', evidence='ok')
        self.assertNotIn('历史项', self.state.brief_markdown())
        self.assertIn('历史项', self.state.to_markdown())

    def test_done_list_is_capped(self):
        for index in range(20):
            self.state.add('d%d' % index, 'done %d' % index, status='done', evidence='ok')
        text = self.state.to_markdown()
        self.assertIn('另有 8 项', text)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='handoff-cli-')

    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = handoff_cli.main(['--root', self.root] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def make_project(self, slug='demo'):
        self.assertEqual(self.run_cli('projects', 'add', slug, '--name', 'Demo')[0], 0)

    def test_full_flow(self):
        self.make_project()
        self.assertEqual(self.run_cli('--project', 'demo', 'ws', 'add', 'kf', '关键帧',
                                      '--next', '出图', '--entry', 'art/README.md')[0], 0)
        self.assertEqual(self.run_cli('--project', 'demo', 'focus', '先出首轮', '--ws', 'kf')[0], 0)
        self.assertEqual(self.run_cli('--project', 'demo', 'claim', 'kf', '--by', 'a1')[0], 0)
        code, _, err = self.run_cli('--project', 'demo', 'claim', 'kf', '--by', 'a2')
        self.assertEqual(code, 3)
        self.assertIn('认领冲突', err)
        self.assertEqual(self.run_cli('--project', 'demo', 'asset', 'add', 'K01', 'k.png',
                                      '--ws', 'kf', '--status', 'pending_review',
                                      '--tool', 'novelai', '--seed', '42')[0], 0)
        code, out, _ = self.run_cli('--project', 'demo', 'brief')
        self.assertEqual(code, 0)
        self.assertIn('先出首轮', out)
        self.assertIn('K01', out)
        code, out, _ = self.run_cli('--project', 'demo', 'asset', 'list', '--status', 'pending_review')
        self.assertIn('K01', out)
        self.assertEqual(self.run_cli('--project', 'demo', 'asset', 'review', 'K01', 'approved',
                                      '--feedback', '用户：可以')[0], 0)
        self.assertEqual(self.run_cli('--project', 'demo', 'release', 'kf', '--by', 'a1')[0], 0)
        with open(os.path.join(self.root, 'projects', 'demo', 'HANDOFF.md'), encoding='utf-8') as handle:
            self.assertIn('用户：可以', handle.read())

    def test_done_requires_evidence_and_block_requires_reason(self):
        self.make_project()
        self.run_cli('--project', 'demo', 'add', 't1', '任务')
        code, _, err = self.run_cli('--project', 'demo', 'done', 't1')
        self.assertEqual(code, 2)
        self.assertIn('evidence', err)
        self.assertEqual(self.run_cli('--project', 'demo', 'done', 't1', '--evidence', '3/3')[0], 0)
        self.run_cli('--project', 'demo', 'add', 't2', '任务2')
        self.assertEqual(self.run_cli('--project', 'demo', 'block', 't2')[0], 2)

    def test_env_variable_selects_project(self):
        self.make_project('one')
        self.make_project('two')
        code, out, _ = self.run_cli('whoami', env={handoff_cli.ENV_PROJECT: 'two'})
        self.assertEqual(code, 0)
        self.assertIn('(two)', out)
        self.assertIn('环境变量', out)
        code, out, _ = self.run_cli('--project', 'one', 'whoami', env={handoff_cli.ENV_PROJECT: 'two'})
        self.assertIn('(one)', out)

    def test_note_returns_id_and_retire_works(self):
        self.make_project()
        code, out, _ = self.run_cli('--project', 'demo', 'note', 'facts', '旧事实')
        self.assertIn('F1', out)
        self.run_cli('--project', 'demo', 'note', 'facts', '新事实')
        self.assertEqual(self.run_cli('--project', 'demo', 'retire', 'F1', '--by-id', 'F2')[0], 0)
        code, out, _ = self.run_cli('--project', 'demo', 'show', '--full')
        self.assertIn('~~`F1`', out)
        code, out, _ = self.run_cli('--project', 'demo', 'show')       # compact: count only
        self.assertNotIn('~~`F1`', out)
        self.assertIn('另有 1 条已作废笔记', out)
        code, out, _ = self.run_cli('--project', 'demo', 'get', 'F1')
        self.assertEqual(code, 0)
        self.assertIn('旧事实', out)
        self.assertIn('superseded', out)

    def test_check_exit_code_reflects_errors(self):
        self.make_project()
        self.run_cli('--project', 'demo', 'focus', 'x')
        self.assertEqual(self.run_cli('--project', 'demo', 'check')[0], 0)
        self.run_cli('--project', 'demo', 'add', 'z', 'fake', '--status', 'done')
        self.assertEqual(self.run_cli('--project', 'demo', 'check')[0], 1)

    def test_errors_are_reported_not_raised(self):
        self.make_project()
        code, _, err = self.run_cli('--project', 'demo', 'ws', 'set', 'ghost', '--next', 'x')
        self.assertEqual(code, 2)
        code, _, err = self.run_cli('--project', 'demo', 'add', 'x', 't', '--ws', 'ghost')
        self.assertEqual(code, 2)
        self.assertIn('未知工作流', err)

    def test_lock_file_is_cleaned_up_and_stale_lock_is_broken(self):
        self.make_project()
        state_path = os.path.join(self.root, 'projects', 'demo', 'state.json')
        lock = state_path + '.lock'
        with open(lock, 'w') as handle:
            handle.write('999999')
        old = time.time() - handoff_cli.LOCK_STALE_SECONDS - 5
        os.utime(lock, (old, old))
        self.assertEqual(self.run_cli('--project', 'demo', 'note', 'facts', 'x')[0], 0)
        self.assertFalse(os.path.exists(lock))

    def test_gbk_console_does_not_crash_on_emoji(self):
        # Windows consoles often use GBK; emoji in the briefing used to raise
        # UnicodeEncodeError and kill `brief` / `check`.
        self.make_project()
        self.run_cli('--project', 'demo', 'focus', '中文焦点')
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='gbk')
        with contextlib.redirect_stdout(stream):
            code = handoff_cli.main(['--root', self.root, '--project', 'demo', 'brief'])
            self.assertEqual(handoff_cli.main(['--root', self.root, '--project', 'demo', 'check']), 0)
        stream.flush()
        text = raw.getvalue().decode('gbk')
        self.assertEqual(code, 0)
        self.assertIn('[焦点]', text)
        self.assertIn('中文焦点', text)

    def test_fresh_foreign_lock_times_out_cleanly(self):
        self.make_project()
        lock = os.path.join(self.root, 'projects', 'demo', 'state.json.lock')
        with open(lock, 'w') as handle:
            handle.write('1')
        with mock.patch.object(handoff_cli, 'LOCK_WAIT_SECONDS', 0.2):
            code, _, err = self.run_cli('--project', 'demo', 'note', 'facts', 'x')
        self.assertEqual(code, 2)
        self.assertIn('正被另一个进程写入', err)
        os.unlink(lock)


if __name__ == '__main__':
    unittest.main()
