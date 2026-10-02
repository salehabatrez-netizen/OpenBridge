"""Floating window project board: hot loader always; Tk rendering on request.

The Tk tests open a real (off-screen) window, so they only run with
OB_TK_TESTS=1 - routine test runs by other agents must not pop windows up on
the user's desktop.
"""
import os
import shutil
import tempfile
import time
import unittest

import floating_status as fs

GOOD = '''
def board(limit=8, **kw):
    return {'header': 'H%s', 'rows': [], 'hidden': 0, 'guard': None, 'error': None}
'''


class HotLoaderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='fsb-')
        self.path = os.path.join(self.dir, 'project_board.py')
        self.saved = dict(fs._BOARD)
        fs._BOARD.update(mod=None, mtime=None, path=None, error=None)

    def tearDown(self):
        fs._BOARD.clear()
        fs._BOARD.update(self.saved)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text, bump):
        with open(self.path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        stamp = time.time() + bump            # distinct mtimes even on coarse filesystems
        os.utime(self.path, (stamp, stamp))

    def test_reload_on_change_and_keep_last_good(self):
        self._write(GOOD % 1, 1)
        self.assertEqual(fs.board_module(self.path).board()['header'], 'H1')
        self._write(GOOD % 2, 2)
        self.assertEqual(fs.board_module(self.path).board()['header'], 'H2')
        self._write('def board(:\n', 3)                       # syntax error
        self.assertEqual(fs.board_module(self.path).board()['header'], 'H2')
        self.assertIn('加载失败', fs._BOARD['error'])
        self._write('x = 1\n', 4)                              # no board()
        self.assertEqual(fs.board_module(self.path).board()['header'], 'H2')
        self._write(GOOD % 3, 5)
        self.assertEqual(fs.board_module(self.path).board()['header'], 'H3')
        self.assertIsNone(fs._BOARD['error'])

    def test_unchanged_file_is_not_reimported(self):
        self._write(GOOD % 1, 1)
        first = fs.board_module(self.path)
        self.assertIs(fs.board_module(self.path), first)

    def test_missing_file(self):
        self.assertIsNone(fs.board_module(os.path.join(self.dir, 'nope.py')))
        self.assertIn('缺少', fs._BOARD['error'])


def _data(n=3, hidden=1):
    rows = []
    for i in range(n):
        rows.append({'slug': 'p%d' % i, 'health': 'working', 'glyph': '*', 'colour': '#42d392',
                     'short': 'x', 'title': '项目 %d' % i, 'right': '%d/4' % i, 'progress': i / 4.0,
                     'badges': '待审 2' if i == 0 else '', 'age': '刚刚', 'sub': '认领：a · ws · 剩 5 分',
                     'sub_colour': '#42d392'})
    return {'header': '项目进展 · %d 个' % n, 'rows': rows, 'hidden': hidden,
            'guard': {'text': '守护运行中 · 2 项受保护', 'colour': '#8b9bb4'}, 'error': None}


@unittest.skipUnless(os.environ.get('OB_TK_TESTS') == '1', 'set OB_TK_TESTS=1 to open a real Tk window')
class TkBoardTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = fs.FloatingStatusWindow(self.root, lambda: (None, None, False))
        self.win.open()
        self.win.window.geometry('+-3000+-3000')

    def tearDown(self):
        self.win.close()
        self.root.destroy()

    def test_rows_grow_shrink_and_collapse(self):
        w = self.win
        w._paint_board(_data(3))
        self.root.update_idletasks()
        visible = [r for r in w._board_rows if r['frame'].winfo_manager() == 'grid']
        self.assertEqual(len(visible), 3)
        self.assertEqual(visible[0]['title'].cget('text'), '项目 0')
        self.assertIn('待审 2', visible[0]['right'].cget('text'))
        self.assertIn('+1 个项目', w.lbl_board_foot.cget('text'))
        self.assertIn('守护运行中', w.lbl_guard.cget('text'))
        w._paint_board(_data(1, hidden=0))
        visible = [r for r in w._board_rows if r['frame'].winfo_manager() == 'grid']
        self.assertEqual(len(visible), 1)
        self.assertEqual(w.lbl_board_foot.winfo_manager(), '')     # empty footer hidden
        w._board_open = False
        w._paint_board(_data(3))
        self.assertTrue(w.lbl_board_head.cget('text').startswith('\u25b8'))
        self.assertEqual([r for r in w._board_rows if r['frame'].winfo_manager() == 'grid'], [])

    def test_progress_bar_width(self):
        w = self.win
        w._paint_board(_data(3, hidden=0))
        x0, y0, x1, y1 = w._board_rows[2]['bar'].coords(w._board_rows[2]['fill'])
        self.assertEqual(int(x1), int(fs.BAR_WIDTH * 0.5))

    def test_refresh_survives_broken_board(self):
        saved = fs.board_module
        try:
            fs.board_module = lambda path=None: (_ for _ in ()).throw(RuntimeError('boom'))
            self.win._refresh_board()
            self.assertIn('boom', self.win.lbl_board_foot.cget('text'))
        finally:
            fs.board_module = saved


if __name__ == '__main__':
    unittest.main()
