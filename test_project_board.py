import json
import os
import shutil
import tempfile
import time
import unittest

import project_board as pb

NOW = 1_800_000_000.0


def state(**kw):
    base = {'schema': 2, 'project': 'P', 'updated_at': NOW - 3 * 86400, 'session': {},
            'focus': {}, 'claims': [], 'items': [], 'assets': [], 'blockers': [], 'journal': []}
    base.update(kw)
    return base


def item(i, status, age=10):
    return {'id': i, 'title': i, 'status': status, 'updated_at': NOW - age, 'workstream': ''}


class SummarizeTests(unittest.TestCase):
    def test_counts_and_progress(self):
        s = pb.summarize('p', {'name': 'Proj'}, state(items=[
            item('a', 'done'), item('b', 'done'), item('c', 'doing'), item('d', 'todo'),
            item('e', 'blocked')]), NOW)
        self.assertEqual((s['done'], s['total']), (2, 5))
        self.assertEqual(s['counts']['blocked'], 1)
        r = pb.row(s, NOW)
        self.assertEqual(r['right'], '2/5')
        self.assertAlmostEqual(r['progress'], 0.4)

    def test_live_claim_means_working_even_when_quiet(self):
        s = pb.summarize('p', {}, state(claims=[
            {'target': 'ws1', 'by': 'agent-7', 'at': NOW - 3600, 'expires_at': NOW + 1800}]), NOW)
        self.assertEqual(s['health'], pb.H_WORKING)
        r = pb.row(s, NOW)
        self.assertIn('agent-7', r['sub'])
        self.assertIn('ws1', r['sub'])
        self.assertIn('剩 30 分', r['sub'])

    def test_recent_write_means_working(self):
        s = pb.summarize('p', {}, state(updated_at=NOW - 60), NOW)
        self.assertEqual(s['health'], pb.H_WORKING)

    def test_waiting_on_user_beats_idle_and_dormant(self):
        s = pb.summarize('p', {}, state(updated_at=NOW - 30 * 86400, blockers=[
            {'id': 'B1', 'text': '选哪张封面', 'status': 'active'},
            {'id': 'B2', 'text': 'old', 'status': 'resolved'}]), NOW)
        self.assertEqual(s['health'], pb.H_WAITING)
        self.assertEqual(len(s['waiting']), 1)
        self.assertIn('选哪张封面', pb.row(s, NOW)['sub'])

    def test_stale_doing_without_claim(self):
        s = pb.summarize('p', {}, state(updated_at=NOW - 7200,
                                        items=[item('x', 'doing', age=7200)]), NOW)
        self.assertEqual(s['health'], pb.H_STALE)
        self.assertIn('x 进行中', pb.row(s, NOW)['sub'])

    def test_doing_covered_by_live_workstream_claim_is_not_stale(self):
        it = item('x', 'doing', age=7200)
        it['workstream'] = 'ws'
        s = pb.summarize('p', {}, state(updated_at=NOW - 7200, items=[it], claims=[
            {'target': 'ws', 'by': 'a', 'at': NOW - 7200, 'expires_at': NOW + 60}]), NOW)
        self.assertEqual(s['stale_doing'], [])

    def test_recently_expired_claim_is_stale_old_one_is_history(self):
        fresh = pb.summarize('p', {}, state(updated_at=NOW - 7200, claims=[
            {'target': 't', 'by': 'a', 'at': NOW - 9000, 'expires_at': NOW - 3600}]), NOW)
        self.assertEqual(fresh['health'], pb.H_STALE)
        old = pb.summarize('p', {}, state(updated_at=NOW - 3 * 86400, claims=[
            {'target': 't', 'by': 'a', 'at': NOW - 4 * 86400, 'expires_at': NOW - 3 * 86400}]), NOW)
        self.assertEqual(old['health'], pb.H_IDLE)

    def test_dormant_after_a_week(self):
        s = pb.summarize('p', {}, state(updated_at=NOW - 8 * 86400), NOW)
        self.assertEqual(s['health'], pb.H_DORMANT)

    def test_focus_is_the_default_subline(self):
        s = pb.summarize('p', {}, state(focus={'text': '下一步：做 X'}), NOW)
        self.assertIn('下一步：做 X', pb.row(s, NOW)['sub'])

    def test_last_activity_uses_latest_signal(self):
        s = pb.summarize('p', {}, state(updated_at=NOW - 5000, journal=[
            {'at': NOW - 100, 'by': 'agent-9', 'text': 'x'}]), NOW)
        self.assertEqual(s['last'], NOW - 100)
        self.assertEqual(s['last_by'], 'agent-9')

    def test_garbage_fields_do_not_raise(self):
        s = pb.summarize('p', {}, {'items': 'nope', 'claims': [1, None, {'expires_at': 'x'}],
                                   'focus': 'str', 'session': [], 'journal': {}}, NOW)
        self.assertEqual(s['total'], 0)


class TextTests(unittest.TestCase):
    def test_clip_counts_cjk_as_two_columns(self):
        self.assertEqual(pb.display_width('ab中文'), 6)
        clipped = pb.clip('一二三四五六七八九十', 9)
        self.assertLessEqual(pb.display_width(clipped), 9)
        self.assertTrue(clipped.endswith('\u2026'))

    def test_clip_keeps_short_text_and_collapses_whitespace(self):
        self.assertEqual(pb.clip('a  b\nc', 20), 'a b c')

    def test_ago_and_remaining(self):
        self.assertEqual(pb.ago(30), '刚刚')
        self.assertEqual(pb.ago(125), '2 分钟前')
        self.assertEqual(pb.ago(7200), '2 小时前')
        self.assertEqual(pb.ago(3 * 86400), '3 天前')
        self.assertEqual(pb.remaining(5000), '剩 1h23m')


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='pb-')
        self.handoff = os.path.join(self.root, 'HANDOFF')
        self.coord = os.path.join(self.root, '_coord')
        os.makedirs(os.path.join(self.handoff, 'projects'))
        pb._cache.clear()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _project(self, slug, st):
        d = os.path.join(self.handoff, 'projects', slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'state.json'), 'w', encoding='utf-8') as fh:
            json.dump(st, fh)

    def _registry(self, projects, active=None):
        with open(os.path.join(self.handoff, 'registry.json'), 'w', encoding='utf-8') as fh:
            json.dump({'schema': 1, 'active': active,
                       'projects': {k: {'name': v} for k, v in projects.items()}}, fh)

    def test_sorting_default_star_and_dormant_folding(self):
        self._registry({'idle': '空闲项目', 'work': '忙碌项目', 'old': '旧项目',
                        'wait': '等待项目', 'star': '默认项目'}, active='star')
        self._project('idle', state(updated_at=NOW - 3600))
        self._project('work', state(updated_at=NOW - 60))
        self._project('old', state(updated_at=NOW - 30 * 86400))
        self._project('wait', state(blockers=[{'id': 'B1', 'text': 'q', 'status': 'active'}]))
        self._project('star', state(updated_at=NOW - 30 * 86400))
        data = pb.board(NOW, self.handoff, self.coord)
        slugs = [r['slug'] for r in data['rows']]
        self.assertEqual(slugs[:3], ['work', 'wait', 'idle'])
        self.assertIn('star', slugs)            # default shown even when dormant
        self.assertNotIn('old', slugs)
        self.assertEqual(data['hidden'], 1)
        star = [r for r in data['rows'] if r['slug'] == 'star'][0]
        self.assertTrue(star['title'].startswith('\u2605'))
        self.assertIn('进行中 1', data['header'])
        self.assertIn('等你 1', data['header'])

    def test_one_broken_project_does_not_hide_the_others(self):
        self._registry({'ok': 'OK', 'bad': 'Bad', 'missing': 'Missing'})
        self._project('ok', state(updated_at=NOW - 60))
        d = os.path.join(self.handoff, 'projects', 'bad')
        os.makedirs(d)
        with open(os.path.join(d, 'state.json'), 'w') as fh:
            fh.write('{broken')
        data = pb.board(NOW, self.handoff, self.coord)
        health = {r['slug']: r['health'] for r in data['rows']}
        self.assertEqual(health['ok'], pb.H_WORKING)
        self.assertEqual(health['bad'], pb.H_ERROR)
        self.assertEqual(health['missing'], pb.H_ERROR)

    def test_missing_registry_is_reported_not_raised(self):
        data = pb.board(NOW, os.path.join(self.root, 'nope'), self.coord)
        self.assertIn('registry.json', data['error'])
        self.assertEqual(data['rows'], [])

    def test_limit(self):
        self._registry({'p%d' % i: 'P%d' % i for i in range(9)})
        for i in range(9):
            self._project('p%d' % i, state(updated_at=NOW - 100 - i))
        data = pb.board(NOW, self.handoff, self.coord, limit=4)
        self.assertEqual(len(data['rows']), 4)
        self.assertEqual(data['hidden'], 5)
        self.assertEqual(data['rows'][0]['slug'], 'p0')   # most recent first

    def test_cache_reparses_only_after_change(self):
        self._registry({'a': 'A'})
        self._project('a', state(focus={'text': 'one'}))
        self.assertIn('one', pb.board(NOW, self.handoff, self.coord)['rows'][0]['sub'])
        path = os.path.join(self.handoff, 'projects', 'a', 'state.json')
        key_before = pb._cache[path][0]
        self._project('a', state(focus={'text': 'two-longer'}))
        self.assertIn('two-longer', pb.board(NOW, self.handoff, self.coord)['rows'][0]['sub'])
        self.assertNotEqual(pb._cache[path][0], key_before)

    def test_render_text(self):
        self._registry({'a': 'Alpha'}, active='a')
        self._project('a', state(updated_at=NOW - 60, items=[item('x', 'done'), item('y', 'todo')]))
        text = pb.render_text(pb.board(NOW, self.handoff, self.coord))
        self.assertIn('Alpha', text)
        self.assertIn('1/2', text)


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='pbg-')
        pb._cache.clear()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _lease(self, name, **fields):
        os.makedirs(os.path.join(self.root, 'leases'), exist_ok=True)
        rec = {'resource': name, 'status': 'up', 'down_since': None}
        rec.update(fields)
        with open(os.path.join(self.root, 'leases', name + '.json'), 'w') as fh:
            json.dump(rec, fh)

    def _guardian(self, age=3, paused=False):
        with open(os.path.join(self.root, 'guardian_status.json'), 'w') as fh:
            json.dump({'updated': NOW - age, 'paused': paused}, fh)

    def _incidents(self, *recs):
        with open(os.path.join(self.root, 'incidents.jsonl'), 'w', encoding='utf-8') as fh:
            for rec in recs:
                fh.write((json.dumps(rec) if isinstance(rec, dict) else rec) + '\n')

    def test_absent_coord_hides_line(self):
        self.assertIsNone(pb.guardian_line(NOW, os.path.join(self.root, 'none')))

    def test_not_running(self):
        self._lease('infra.gui')
        self._guardian(age=600)
        g = pb.guardian_line(NOW, self.root)
        self.assertIn('守护未运行', g['text'])
        self.assertIn('1 项', g['text'])

    def test_running_quiet(self):
        self._lease('infra.gui')
        self._lease('cad.rhino')
        self._guardian()
        self.assertIn('守护运行中 · 2 项受保护 · 1 小时内无异常', pb.guardian_line(NOW, self.root)['text'])

    def test_recovered_exit_is_calm_not_red(self):
        # the OpenBridge restart case: exit + recovered within seconds, lease back to 'up'
        self._lease('infra.tunnel')
        self._guardian()
        self._incidents({'at': NOW - 7200, 'kind': 'exit', 'resource': 'old'}, 'not json',
                        {'at': NOW - 240, 'kind': 'exit', 'resource': 'infra.openbridge'},
                        {'at': NOW - 240, 'kind': 'exit', 'resource': 'infra.tunnel'},
                        {'at': NOW - 230, 'kind': 'recovered', 'resource': 'infra.openbridge'},
                        {'at': NOW - 220, 'kind': 'recovered', 'resource': 'infra.tunnel'})
        g = pb.guardian_line(NOW, self.root)
        self.assertIn('infra.tunnel 退出后已恢复', g['text'])
        self.assertIn('共 2 次', g['text'])
        self.assertEqual(g['colour'], pb.MUTED)

    def test_still_down_is_red(self):
        self._lease('cad.rhino', status='down', down_since=NOW - 120)
        self._lease('infra.gui')
        self._guardian()
        self._incidents({'at': NOW - 120, 'kind': 'exit', 'resource': 'cad.rhino'})
        g = pb.guardian_line(NOW, self.root)
        self.assertIn('cad.rhino 已退出，尚未恢复（2 分钟前）', g['text'])
        self.assertEqual(g['colour'], pb.PRESENTATION[pb.H_ERROR][0])

    def test_paused(self):
        self._lease('infra.gui')
        self._guardian(paused=True)
        g = pb.guardian_line(NOW, self.root)
        self.assertIn('守护已暂停', g['text'])
        self.assertEqual(g['colour'], pb.PRESENTATION[pb.H_WAITING][0])


if __name__ == '__main__':
    unittest.main()
