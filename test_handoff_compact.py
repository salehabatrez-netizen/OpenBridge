import io
import os
import re
import tempfile
import unittest
from unittest import mock

import handoff_cli
from handoff import HandoffState, COMPACT_ASSETS, COMPACT_FACTS, COMPACT_ATTEMPTS


def big_state():
    state = HandoffState(os.path.join(tempfile.mkdtemp(), 'state.json'))
    state.set_focus('下一步：出第二轮关键帧', why='用户指派')
    for i in range(60):
        state.note('facts', '事实%02d：' % i + '很长的环境说明，' * 30)
    for i in range(8):
        state.note('decisions', '决定%d：' % i + '用户拍板的方案细节，' * 20)
    for i in range(40):
        state.add_attempt('尝试%02d：' % i + '做法描述' * 20, reason='原因' * 30, lesson='教训' * 30)
    for i in range(50):
        state.add_asset('a%02d' % i, 'art/out/a%02d.png' % i, status='pending_review', tool='nai', seed=i)
    for i in range(5):
        state.add_asset('r%d' % i, 'art/r%d.png' % i, status='pending_review')
        state.review_asset('r%d' % i, 'rejected', '不行' * 40)
    for i in range(30):
        state.add('d%02d' % i, '完成项 %d' % i, status='done', evidence='证据' * 80)
    for i in range(10):
        state.retire_note('F%d' % (i + 1), reason='过时')
    return state


class CompactViewTests(unittest.TestCase):
    def setUp(self):
        self.state = big_state()
        self.full = self.state.to_markdown()
        self.compact = self.state.compact_markdown()

    def test_bounded_and_much_smaller(self):
        self.assertLess(len(self.compact), 9000)
        self.assertLess(len(self.compact) * 4, len(self.full))

    def test_no_active_record_is_silently_dropped(self):
        for entry in self.state.active_notes('facts') + self.state.active_notes('decisions'):
            self.assertIn('`%s`' % entry['id'], self.compact)
        for attempt in self.state.data['attempts']:
            self.assertIn('`%s`' % attempt['id'], self.compact)
        for i in range(5):
            self.assertIn('`r%d`' % i, self.compact)

    def test_caps_and_pointers(self):
        self.assertIn('待用户审阅的资产（50 个，最近 %d 个）' % COMPACT_ASSETS, self.compact)
        self.assertIn('asset list --status pending_review', self.compact)
        self.assertIn('`a49`', self.compact)
        self.assertNotIn('`a00` — ', self.compact)
        self.assertIn('环境事实（重新发现代价很高；50 条）', self.compact)
        self.assertEqual(len(re.findall(r'(?m)^- `F\d+`', self.compact)), COMPACT_FACTS)
        self.assertEqual(len(re.findall(r'(?m)^- `A\d+`', self.compact)), COMPACT_ATTEMPTS)
        self.assertIn('另有 10 条已作废笔记', self.compact)
        self.assertIn('get <编号>', self.compact)

    def test_focus_and_brief_come_first(self):
        self.assertTrue(self.compact.startswith(self.state.brief_markdown()))
        self.assertIn('下一步：出第二轮关键帧', self.compact)

    def test_find_any(self):
        self.assertEqual(self.state.find_any('F20')[0][0], 'facts')
        self.assertEqual(self.state.find_any('A3')[0][0], 'attempts')
        self.assertEqual(self.state.find_any('a07')[0][0], 'assets')
        self.assertEqual(self.state.find_any('d01')[0][0], 'items')
        self.assertEqual(self.state.find_any('nope'), [])


class CliFilesTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='handoff-compact-')

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch('sys.stdout', out), mock.patch('sys.stderr', err):
            code = handoff_cli._main(['--root', self.root] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_write_produces_compact_doc_and_full_archive(self):
        self.assertEqual(self.run_cli('projects', 'add', 'demo', '--name', 'Demo', '--goal', 'g')[0], 0)
        self.run_cli('--project', 'demo', 'note', 'facts', '旧事实')
        self.run_cli('--project', 'demo', 'note', 'facts', '新事实')
        self.assertEqual(self.run_cli('--project', 'demo', 'retire', 'F1', '--by-id', 'F2')[0], 0)
        folder = os.path.join(self.root, 'projects', 'demo')
        with open(os.path.join(folder, 'HANDOFF.md'), encoding='utf-8') as fh:
            doc = fh.read()
        with open(os.path.join(folder, 'HANDOFF_FULL.md'), encoding='utf-8') as fh:
            full = fh.read()
        self.assertIn('紧凑视图', doc)
        self.assertNotIn('~~`F1`', doc)
        self.assertIn('~~`F1`', full)

    def test_render_regenerates_docs_without_touching_state(self):
        self.run_cli('projects', 'add', 'demo', '--name', 'Demo', '--goal', 'g')
        self.run_cli('--project', 'demo', 'focus', 'x')
        path = os.path.join(self.root, 'projects', 'demo', 'state.json')
        with open(path, 'rb') as fh:
            before = fh.read()
        os.remove(os.path.join(self.root, 'projects', 'demo', 'HANDOFF_FULL.md'))
        self.assertEqual(self.run_cli('--project', 'demo', 'render')[0], 0)
        with open(path, 'rb') as fh:
            self.assertEqual(fh.read(), before)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'projects', 'demo', 'HANDOFF_FULL.md')))

    def test_get_unknown_id_is_an_argument_error(self):
        self.run_cli('projects', 'add', 'demo', '--name', 'Demo', '--goal', 'g')
        code, _, err = self.run_cli('--project', 'demo', 'get', 'F99')
        self.assertEqual(code, 2)
        self.assertIn('F99', err)


if __name__ == '__main__':
    unittest.main()
