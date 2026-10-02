import json
import os
import tempfile
import unittest

from floating_status import active_project


class ActiveProjectTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='fs-proj-')

    def _write(self, payload):
        with open(os.path.join(self.root, 'registry.json'), 'w', encoding='utf-8') as fh:
            json.dump(payload, fh)

    def test_reads_active_project_name(self):
        self._write({'active': 'skykeep',
                     'projects': {'skykeep': {'name': 'Skykeep 项目'}}})
        self.assertEqual(active_project(self.root)['name'], 'Skykeep 项目')

    def test_falls_back_to_slug_when_unnamed(self):
        self._write({'active': 'alpha', 'projects': {'alpha': {}}})
        self.assertEqual(active_project(self.root)['name'], 'alpha')

    def test_none_when_no_active(self):
        self._write({'active': None, 'projects': {}})
        self.assertIsNone(active_project(self.root))

    def test_none_when_registry_missing(self):
        # The indicator must survive a workspace with no handoff set up yet.
        self.assertIsNone(active_project(os.path.join(self.root, 'nope')))

    def test_none_when_registry_corrupt(self):
        with open(os.path.join(self.root, 'registry.json'), 'w') as fh:
            fh.write('{broken')
        self.assertIsNone(active_project(self.root))

    def test_survives_unexpected_shape(self):
        self._write({'active': 'x', 'projects': 'not-a-dict'})
        self.assertIsNone(active_project(self.root))


if __name__ == '__main__':
    unittest.main()
