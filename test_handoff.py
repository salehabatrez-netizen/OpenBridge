import json
import os
import tempfile
import time
import unittest

from handoff import HandoffState, SCHEMA_VERSION, STALE_DOING_SECONDS, load


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='handoff-test-')
        self.path = os.path.join(self.dir, 'state.json')

    def test_missing_file_loads_blank_instead_of_raising(self):
        # The whole point is surviving a bad handover, including no file.
        state = load(os.path.join(self.dir, 'nope.json'))
        self.assertEqual(state.data['items'], [])
        self.assertEqual(state.data['schema'], SCHEMA_VERSION)

    def test_corrupt_file_does_not_crash(self):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write('{not json at all')
        state = load(self.path)
        self.assertEqual(state.data['items'], [])

    def test_non_dict_json_is_rejected(self):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write('[1, 2, 3]')
        state = load(self.path)
        self.assertEqual(state.data['items'], [])

    def test_roundtrip_preserves_items(self):
        state = HandoffState(self.path)
        state.add('a', 'First task', status='done', evidence='tests pass')
        state.add('b', 'Second task', status='doing', owner='agent-1')
        state.save()
        again = load(self.path)
        self.assertEqual(len(again.data['items']), 2)
        self.assertEqual(again.by_status('done')[0]['title'], 'First task')

    def test_duplicate_id_rejected(self):
        state = HandoffState(self.path)
        state.add('a', 'First')
        with self.assertRaises(ValueError):
            state.add('a', 'Clashing')

    def test_bad_status_rejected(self):
        state = HandoffState(self.path)
        with self.assertRaises(ValueError):
            state.add('a', 'First', status='sort-of-done')

    def test_update_changes_status_and_timestamp(self):
        state = HandoffState(self.path)
        record = state.add('a', 'Task')
        before = record['updated_at']
        time.sleep(0.01)
        updated = state.update('a', status='done', evidence='verified')
        self.assertEqual(updated['status'], 'done')
        self.assertEqual(updated['evidence'], 'verified')
        self.assertGreater(updated['updated_at'], before)

    def test_update_unknown_id_raises(self):
        state = HandoffState(self.path)
        with self.assertRaises(KeyError):
            state.update('ghost', status='done')

    def test_stale_doing_flags_abandoned_work(self):
        # An assistant that died mid-task leaves `doing` behind; the next one
        # must be told not to trust the label.
        state = HandoffState(self.path)
        state.add('a', 'Interrupted task', status='doing')
        state.data['items'][0]['updated_at'] = time.time() - (STALE_DOING_SECONDS + 60)
        self.assertEqual(len(state.stale_doing()), 1)

    def test_fresh_doing_is_not_stale(self):
        state = HandoffState(self.path)
        state.add('a', 'Active task', status='doing')
        self.assertEqual(state.stale_doing(), [])

    def test_summary_counts(self):
        state = HandoffState(self.path)
        state.add('a', 'One', status='done')
        state.add('b', 'Two', status='doing')
        state.add('c', 'Three', status='todo')
        state.add('d', 'Four', status='todo')
        summary = state.summary()
        self.assertEqual(summary['counts']['todo'], 2)
        self.assertEqual(summary['counts']['done'], 1)
        self.assertEqual(summary['current'], 'Two')

    def test_atomic_save_leaves_no_temp_files(self):
        state = HandoffState(self.path)
        state.add('a', 'Task')
        state.save()
        leftovers = [n for n in os.listdir(self.dir) if n.startswith('.handoff-')]
        self.assertEqual(leftovers, [])

    def test_saved_file_is_valid_json_and_human_readable(self):
        state = HandoffState(self.path)
        state.add('a', '中文任务标题')
        state.save()
        with open(self.path, encoding='utf-8') as handle:
            raw = handle.read()
        self.assertIn('中文任务标题', raw)  # not \uXXXX escaped
        json.loads(raw)

    def test_markdown_contains_all_sections(self):
        state = HandoffState(self.path)
        state.data['project'] = 'Skykeep'
        state.data['goal'] = '通关并建城堡'
        state.begin_session('agent-x', note='接手中')
        state.add('a', '已完成项', status='done', evidence='13/13 tests')
        state.add('b', '进行中项', status='doing')
        state.add('c', '待办项', status='todo')
        state.note('blockers', '等待用户选 A 还是 B')
        state.note('decisions', '放弃 prismarine-viewer')
        state.note('facts', '本机没有 mss/pywin32')
        text = state.to_markdown()
        for needle in ('Skykeep', '通关并建城堡', 'agent-x', '已完成项',
                       '进行中项', '待办项', '等待用户选 A 还是 B',
                       '放弃 prismarine-viewer', '本机没有 mss/pywin32'):
            self.assertIn(needle, text)

    def test_markdown_marks_stale_item(self):
        state = HandoffState(self.path)
        state.add('a', '卡住的任务', status='doing')
        state.data['items'][0]['updated_at'] = time.time() - (STALE_DOING_SECONDS + 60)
        self.assertIn('请先核实真实状态', state.to_markdown())

    def test_markdown_handles_empty_state(self):
        state = HandoffState(self.path)
        text = state.to_markdown()
        self.assertIn('_（无）_', text)

    def test_note_rejects_unknown_bucket(self):
        state = HandoffState(self.path)
        with self.assertRaises(ValueError):
            state.note('whatever', 'text')

    def test_by_status_returns_copies(self):
        # Callers must not be able to mutate internal state by accident.
        state = HandoffState(self.path)
        state.add('a', 'Task')
        got = state.by_status('todo')
        got[0]['title'] = 'mutated'
        self.assertEqual(state.data['items'][0]['title'], 'Task')


if __name__ == '__main__':
    unittest.main()
