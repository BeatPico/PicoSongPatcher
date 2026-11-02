"""Focused 1.4 filesystem/cache tests; every filesystem target is disposable."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT.parent / 'beat_saber_lab/python_packages'), str(ROOT)]

import cache_builder
import library_v14
from maps import PatcherError


class BackendV14Test(unittest.TestCase):
    def setUp(self):
        work = ROOT / 'test_work'
        work.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='cache_backend_v14_', dir=work)
        self.work = Path(self.temporary.name)
        self.library = library_v14.LibraryV14(self.work / 'data')
        self.source = self.work / 'source.obb'
        self.source.write_bytes(b'original source cache must remain unchanged')
        self.library.state.update(source_obb=str(self.source), cache_obb=str(self.source))

    def tearDown(self):
        self.temporary.cleanup()

    def entry(self, identifier, folder=None, enabled=True):
        entry = {'level_id': identifier, 'title': 'Same title', 'artist': 'Same artist',
                 'enabled': enabled, 'difficulties': ['Hard'], 'warnings': []}
        if folder is not None:
            entry['folder'] = str(folder)
        self.library.state['songs'].append(entry)
        return entry

    def local_folder(self, relative):
        folder = self.library.root / relative
        folder.mkdir(parents=True)
        (folder / 'payload.bin').write_bytes(b'local song')
        return folder

    def ready_stage(self):
        stage = self.work / '.building_one'
        stage.mkdir()
        (stage / 'ГОТОВО.txt').write_text('ready', encoding='utf-8')
        (stage / 'source.bin').write_bytes(b'verified output')
        return stage, self.work / 'PicoSongs_one'

    def test_finalize_retries_temporary_permission_error_then_renames(self):
        stage, final = self.ready_stage()
        rename = Path.rename
        attempts = []

        def temporarily_locked(origin, destination):
            attempts.append((origin, destination))
            if len(attempts) < 3:
                raise PermissionError('temporary Windows lock')
            return rename(origin, destination)

        with patch.object(Path, 'rename', autospec=True, side_effect=temporarily_locked), \
             patch.object(cache_builder.time, 'sleep') as sleep:
            actual = cache_builder.finalize_folder(stage, final)
        self.assertEqual(actual, final.resolve())
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(stage.exists())
        self.assertEqual((actual / 'source.bin').read_bytes(), b'verified output')
        self.assertTrue((actual / 'ГОТОВО.txt').is_file())

    def test_finalize_permanent_permission_error_keeps_ready_source_folder(self):
        stage, final = self.ready_stage()
        messages = []
        with patch.object(Path, 'rename', autospec=True, side_effect=PermissionError('persistent Windows lock')) as rename, \
             patch.object(cache_builder.time, 'sleep') as sleep:
            actual = cache_builder.finalize_folder(stage, final, messages.append)
        self.assertEqual(actual, stage.resolve())
        self.assertEqual(rename.call_count, 8)
        self.assertEqual(sleep.call_count, 7)
        self.assertFalse(final.exists())
        self.assertEqual((actual / 'source.bin').read_bytes(), b'verified output')
        self.assertEqual((actual / 'ГОТОВО.txt').read_text(encoding='utf-8'), 'ready')
        self.assertIn(str(stage.resolve()), '\n'.join(messages))

    def test_finalize_rejects_non_sibling_or_identical_paths(self):
        stage, final = self.ready_stage()
        for invalid in (stage, self.work / 'different_parent' / 'final'):
            with self.subTest(path=invalid), patch.object(Path, 'rename', autospec=True) as rename:
                with self.assertRaises(PatcherError):
                    cache_builder.finalize_folder(stage, invalid)
                rename.assert_not_called()
        self.assertTrue(stage.is_dir())

    def test_delete_rejects_traversal_root_container_and_outside_paths(self):
        outside = self.work / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel.txt'
        sentinel.write_text('keep', encoding='utf-8')
        invalid = ['../outside', 'maps/../../outside', '.', 'maps', 'cache_maps', str(outside.resolve())]
        for relative in invalid:
            with self.subTest(folder=relative):
                self.library.state['songs'] = []
                self.entry('bad', relative)
                before = copy.deepcopy(self.library.state)
                with self.assertRaises(PatcherError):
                    self.library.deletion_plan(['bad'])
                with self.assertRaises(PatcherError):
                    self.library.delete_local(['bad'])
                self.assertEqual(self.library.state, before)
                self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
                self.assertTrue(self.source.is_file())

    def test_delete_reference_without_folder_preserves_library_root(self):
        sentinel = self.library.root / 'keep.txt'
        sentinel.write_text('keep root contents', encoding='utf-8')
        self.entry('no_folder')
        self.entry('empty_folder', '')
        selected, folders = self.library.deletion_plan(['no_folder', 'empty_folder'])
        self.assertEqual(len(selected), 2)
        self.assertEqual(folders, [])
        self.assertEqual(self.library.delete_local(['no_folder', 'empty_folder']), 2)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep root contents')
        self.assertTrue(self.source.is_file())

    def test_delete_checks_every_target_before_removing_any_folder(self):
        good = self.local_folder('maps/good')
        self.entry('good', 'maps/good')
        self.entry('bad', '../outside')
        with self.assertRaises(PatcherError):
            self.library.delete_local(['good', 'bad'])
        self.assertEqual((good / 'payload.bin').read_bytes(), b'local song')
        self.assertEqual(len(self.library.state['songs']), 2)
        self.assertTrue(self.source.is_file())

    def test_delete_retains_folder_shared_with_unselected_entry(self):
        shared = self.local_folder('maps/shared')
        self.entry('remove', 'maps/shared')
        retained = self.entry('retain', 'maps/shared')
        selected, folders = self.library.deletion_plan(['remove'])
        self.assertEqual([entry['level_id'] for entry in selected], ['remove'])
        self.assertEqual(folders, [])
        self.assertEqual(self.library.delete_local(['remove']), 1)
        self.assertEqual(self.library.state['songs'], [retained])
        self.assertTrue(shared.is_dir())
        self.assertEqual((shared / 'payload.bin').read_bytes(), b'local song')
        self.assertEqual(self.source.read_bytes(), b'original source cache must remain unchanged')

    def test_delete_shared_folder_once_when_all_owners_are_selected(self):
        shared = self.local_folder('cache_maps/shared')
        self.entry('remove_a', 'cache_maps/shared')
        self.entry('remove_b', 'cache_maps/shared')
        self.entry('reference_only')
        selected, folders = self.library.deletion_plan(['remove_a', 'remove_b', 'reference_only'])
        self.assertEqual(len(selected), 3)
        self.assertEqual(folders, [shared.resolve()])
        self.assertEqual(self.library.delete_local(['remove_a', 'remove_b', 'reference_only']), 3)
        self.assertFalse(shared.exists())
        self.assertEqual(self.library.state['songs'], [])
        self.assertEqual(json.loads(self.library.path.read_text(encoding='utf-8'))['songs'], [])
        self.assertTrue(self.source.is_file())

    def test_delete_refuses_every_configured_source_cache_inside_target_folder(self):
        folder = self.local_folder('maps/protected_source')
        protected = folder / 'nested_source.obb'
        protected.write_bytes(b'protected input cache')
        for key in ('source_obb', 'cache_obb', 'base_obb', 'cache_source_obb'):
            with self.subTest(source_field=key):
                self.library.state['songs'] = []
                self.library.state.update(source_obb=str(self.source), cache_obb=str(self.source), base_obb=str(self.source))
                self.entry('remove', 'maps/protected_source')
                holder = self.entry('retained_reference')
                if key == 'cache_source_obb':
                    holder[key] = str(protected)
                else:
                    self.library.state[key] = str(protected)
                before = copy.deepcopy(self.library.state)
                with self.assertRaises(PatcherError):
                    self.library.deletion_plan(['remove'])
                with self.assertRaises(PatcherError):
                    self.library.delete_local(['remove'])
                self.assertEqual(self.library.state, before)
                self.assertEqual(protected.read_bytes(), b'protected input cache')
                self.assertEqual((folder / 'payload.bin').read_bytes(), b'local song')
                self.assertTrue(self.source.is_file())

    def test_delete_preserves_retained_ancestor_or_descendant_folders(self):
        for selected_path, retained_path in [('maps/parent', 'maps/parent/child'),
                                              ('cache_maps/parent/child', 'cache_maps/parent')]:
            with self.subTest(selected=selected_path, retained=retained_path):
                self.library.state['songs'] = []
                selected_folder = self.library.root / selected_path
                retained_folder = self.library.root / retained_path
                selected_folder.mkdir(parents=True, exist_ok=True)
                retained_folder.mkdir(parents=True, exist_ok=True)
                (selected_folder / 'selected.bin').write_bytes(b'selected local data')
                (retained_folder / 'retained.bin').write_bytes(b'retained local data')
                self.entry('remove', selected_path)
                retained = self.entry('retain', retained_path)
                selected, folders = self.library.deletion_plan(['remove'])
                self.assertEqual([entry['level_id'] for entry in selected], ['remove'])
                self.assertEqual(folders, [])
                self.assertEqual(self.library.delete_local(['remove']), 1)
                self.assertEqual(self.library.state['songs'], [retained])
                self.assertEqual((selected_folder / 'selected.bin').read_bytes(), b'selected local data')
                self.assertEqual((retained_folder / 'retained.bin').read_bytes(), b'retained local data')
                self.assertTrue(self.source.is_file())

    def inventory(self):
        def song(identifier, pack, custom=True, kind='Готовая карта'):
            return {'id': identifier, 'title': 'Same title', 'artist': 'Same artist', 'difficulties': ['Hard'],
                    'pack_id': pack, 'pack_name': pack, 'custom': custom, 'kind': kind,
                    'gameplay_present': True, 'unity_header': True}
        return {'path': str(self.source.resolve()), 'packs': [{'id': 'ostvol1'}, {'id': 'ostvol2'}],
                'songs': [song('existing', 'ostvol1'), song('new_cached', 'ostvol2', kind='AI'),
                          song('new_cached', 'ostvol1', kind='AI'), song('stock', 'ostvol1', custom=False)],
                'total': 4, 'custom': 2, 'ai': 1, 'warnings': []}

    def fake_archive(self, inventory):
        source = self.source.resolve()

        class Archive:
            def __init__(self, path, assets):
                self.path = Path(path).resolve()
                assert self.path == source

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def inventory(self, log):
                return copy.deepcopy(inventory)

        return Archive

    def test_read_cache_defaults_exact_cached_ids_and_preserves_other_flags(self):
        existing = self.entry('existing', 'maps/existing', enabled=False)
        existing['target_pack'] = 'ostvol2'
        same_title = self.entry('same_title_different_id', 'maps/other', enabled=False)
        outside_enabled = self.entry('outside_enabled', 'maps/outside', enabled=True)
        inventory = self.inventory()

        def write_cached(archive, song, root):
            folder = root / 'cache_maps' / song['id']
            folder.mkdir(parents=True)
            (folder / 'level.bundle').write_bytes(b'UnityFS test data')
            return folder, 'test-hash'

        with patch.object(library_v14, 'CacheArchive', self.fake_archive(inventory)), \
             patch.object(library_v14, 'write_cached_material', side_effect=write_cached) as write:
            actual = self.library.read_cache(self.source, ROOT / 'assets', log=lambda text: None)
        self.assertEqual(actual, inventory)
        self.assertTrue(existing['enabled'])
        self.assertTrue(existing['in_viewed_cache'])
        self.assertEqual(existing['target_pack'], 'ostvol2')
        self.assertEqual(existing['cached_pack'], 'ostvol1')
        self.assertFalse(same_title['enabled'])
        self.assertFalse(same_title['in_viewed_cache'])
        self.assertTrue(outside_enabled['enabled'])
        self.assertFalse(outside_enabled['in_viewed_cache'])
        entries = {entry['level_id']: entry for entry in self.library.state['songs']}
        self.assertNotIn('stock', entries)
        self.assertEqual(len(entries), 4)
        cached = entries['new_cached']
        self.assertTrue(cached['enabled'])
        self.assertTrue(cached['in_viewed_cache'])
        self.assertEqual(cached['storage'], 'cached')
        self.assertEqual(cached['cached_pack'], 'ostvol2')
        self.assertEqual(cached['hash'], 'test-hash')
        self.assertTrue((self.library.root / cached['folder'] / 'level.bundle').is_file())
        write.assert_called_once()
        self.assertEqual(self.library.state['source_obb'], str(self.source.resolve()))
        self.assertEqual(self.library.state['cache_obb'], str(self.source.resolve()))
        self.assertEqual(self.source.read_bytes(), b'original source cache must remain unchanged')
        outside = self.library.outside_cache(inventory)
        self.assertEqual(outside, ['same_title_different_id', 'outside_enabled'])

    def test_read_cache_keeps_enabled_reference_when_local_extraction_fails(self):
        inventory = self.inventory()
        with patch.object(library_v14, 'CacheArchive', self.fake_archive(inventory)), \
             patch.object(library_v14, 'write_cached_material', side_effect=ValueError('unsupported native preview')):
            self.library.read_cache(self.source, ROOT / 'assets', log=lambda text: None)
        cached = next(entry for entry in self.library.state['songs'] if entry['level_id'] == 'new_cached')
        self.assertTrue(cached['enabled'])
        self.assertEqual(cached['storage'], 'cache-reference')
        self.assertNotIn('folder', cached)
        self.assertEqual(cached['cache_source_obb'], str(self.source.resolve()))
        self.assertIn('unsupported native preview', '\n'.join(cached['warnings']))
        self.assertTrue(self.source.is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
