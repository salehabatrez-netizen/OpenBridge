import json
import os
import tempfile
import unittest

from registry import Registry, load, slugify, validate_slug


class SlugTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify('Skykeep'), 'skykeep')

    def test_spaces_and_dots_become_hyphens(self):
        self.assertEqual(slugify('My Web Scraper'), 'my-web-scraper')
        self.assertEqual(slugify('a.b.c'), 'a-b-c')

    def test_path_separators_cannot_escape(self):
        # A slug becomes a directory name; traversal must be impossible.
        self.assertEqual(slugify('../../etc'), 'etc')
        self.assertNotIn('/', slugify('a/b'))
        self.assertNotIn('\\', slugify('a\\b'))

    def test_unicode_only_raises(self):
        with self.assertRaises(ValueError):
            slugify('项目')

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            slugify('   ')

    def test_length_capped(self):
        self.assertLessEqual(len(slugify('x' * 200)), 48)

    def test_validate_rejects_traversal(self):
        for bad in ('../x', 'a/b', 'A-Upper', '-lead', '', 'x' * 49):
            with self.assertRaises(ValueError):
                validate_slug(bad)

    def test_validate_rejects_reserved(self):
        with self.assertRaises(ValueError):
            validate_slug('projects')


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='reg-test-')
        self.reg = Registry(self.root)

    def test_missing_registry_loads_empty(self):
        self.assertEqual(load(self.root).listing(), [])

    def test_corrupt_registry_does_not_crash(self):
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, 'registry.json'), 'w') as fh:
            fh.write('{broken')
        self.assertEqual(load(self.root).listing(), [])

    def test_register_and_roundtrip(self):
        self.reg.register('skykeep', name='Skykeep', goal='通关')
        self.reg.save()
        again = load(self.root)
        self.assertTrue(again.exists('skykeep'))
        self.assertEqual(again.listing()[0]['name'], 'Skykeep')

    def test_first_project_becomes_active(self):
        self.reg.register('alpha')
        self.assertEqual(self.reg.active, 'alpha')

    def test_second_project_does_not_steal_active(self):
        self.reg.register('alpha')
        self.reg.register('beta')
        self.assertEqual(self.reg.active, 'alpha')

    def test_duplicate_register_rejected(self):
        self.reg.register('alpha')
        with self.assertRaises(ValueError):
            self.reg.register('alpha')

    def test_set_active_unknown_raises(self):
        with self.assertRaises(KeyError):
            self.reg.set_active('ghost')

    def test_resolve_prefers_explicit_slug(self):
        self.reg.register('alpha')
        self.reg.register('beta')
        self.reg.set_active('alpha')
        self.assertEqual(self.reg.resolve('beta'), 'beta')

    def test_resolve_falls_back_to_active(self):
        self.reg.register('alpha')
        self.assertEqual(self.reg.resolve(), 'alpha')

    def test_resolve_without_anything_raises_with_guidance(self):
        # The error text is the only thing a confused assistant will read.
        with self.assertRaises(RuntimeError) as ctx:
            self.reg.resolve()
        self.assertIn('projects add', str(ctx.exception))

    def test_resolve_unknown_slug_raises(self):
        with self.assertRaises(KeyError):
            self.reg.resolve('ghost')

    def test_paths_are_scoped_per_project(self):
        self.reg.register('alpha')
        self.reg.register('beta')
        self.assertNotEqual(self.reg.state_path('alpha'),
                            self.reg.state_path('beta'))
        self.assertIn(os.path.join('projects', 'alpha'),
                      self.reg.state_path('alpha'))

    def test_state_path_rejects_traversal_slug(self):
        with self.assertRaises(ValueError):
            self.reg.state_path('../escape')

    def test_remove_reassigns_active(self):
        self.reg.register('alpha')
        self.reg.register('beta')
        self.reg.set_active('alpha')
        self.reg.remove('alpha')
        self.assertEqual(self.reg.active, 'beta')

    def test_remove_keeps_files_on_disk(self):
        # Losing work to a typo is unrecoverable; only the entry is dropped.
        self.reg.register('alpha')
        directory = self.reg.project_dir('alpha')
        self.reg.remove('alpha')
        self.assertTrue(os.path.isdir(directory))

    def test_dangling_active_is_cleared_on_load(self):
        self.reg.register('alpha')
        self.reg.save()
        with open(self.reg.path, encoding='utf-8') as fh:
            payload = json.load(fh)
        payload['active'] = 'deleted-project'
        with open(self.reg.path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh)
        self.assertIsNone(load(self.root).active)

    def test_listing_marks_active(self):
        self.reg.register('alpha')
        self.reg.register('beta')
        self.reg.set_active('beta')
        flags = {r['slug']: r['active'] for r in self.reg.listing()}
        self.assertEqual(flags, {'alpha': False, 'beta': True})

    def test_atomic_save_leaves_no_temp(self):
        self.reg.register('alpha')
        self.reg.save()
        self.assertEqual([n for n in os.listdir(self.root)
                          if n.startswith('.registry-')], [])


if __name__ == '__main__':
    unittest.main()
