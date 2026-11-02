"""Mapped Tk smoke checks using a disposable library, never the user's data."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT.parent / 'beat_saber_lab/python_packages'), str(ROOT)]
import app_v14


class InterfaceV14Test(unittest.TestCase):
    def setUp(self):
        work = ROOT / 'test_work'
        work.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='ui_v14_', dir=work)
        self.home = Path(self.temporary.name)
        self.home_patch = patch.object(app_v14, 'HOME', self.home)
        self.base_patch = patch.object(app_v14, 'DEFAULT_BASE', '')
        self.models_patch = patch.object(app_v14, 'profiles', return_value=[{'id': 'base', 'label': 'Исходная модель'}])
        for item in (self.home_patch, self.base_patch, self.models_patch):
            item.start()
        self.root = tk.Tk()
        self.window = app_v14.Window(self.root, read_on_start=False)
        self.window.library.state['songs'] = [
            {'level_id': f'PicoTest{index:03d}', 'title': 'Одинаковое название' if index < 2 else f'Песня {index:02d}',
             'artist': 'Исполнитель', 'difficulties': ['Easy', 'Normal', 'Hard'],
             'enabled': index % 2 == 0, 'folder': f'maps/local_{index:03d}', 'warnings': []}
            for index in range(35)]
        for entry in self.window.library.state['songs']:
            folder = self.window.library.root / entry['folder']
            folder.mkdir(parents=True)
            (folder / 'local.txt').write_text(entry['level_id'], encoding='utf-8')
        self.window.library.save()
        self.window.refresh()
        self.root.update()

    def tearDown(self):
        self.window.busy = False
        self.window.close()
        for item in (self.models_patch, self.base_patch, self.home_patch):
            item.stop()
        self.temporary.cleanup()

    def inventory(self):
        return {'path': str(self.home / 'chosen.obb'), 'packs': [
            {'id': 'ostvol1', 'name': 'OST Vol. 1'}, {'id': 'ostvol2', 'name': 'OST Vol. 2'},
            {'id': 'debug', 'name': 'Debug'}],
            'total': 3, 'custom': 2, 'ai': 0, 'auto': 0, 'warnings': [],
            'songs': [
                {'id': 'PicoTest000', 'title': 'Одинаковое название', 'artist': 'Исполнитель',
                 'pack_id': 'ostvol1', 'pack_name': 'OST Vol. 1', 'custom': True,
                 'kind': 'Готовая карта', 'difficulties': ['Hard']},
                {'id': 'NativeCustom', 'title': 'Из кэша', 'artist': '', 'pack_id': 'ostvol2',
                 'pack_name': 'OST Vol. 2', 'custom': True, 'kind': 'Готовая карта', 'difficulties': ['Hard']},
                {'id': 'StockSong', 'title': 'Штатная', 'artist': '', 'pack_id': 'ostvol1',
                 'pack_name': 'OST Vol. 1', 'custom': False, 'kind': 'Оригинал', 'difficulties': ['Easy']}]}

    def click(self, iid, column='title', state=0):
        self.window.tree.see(iid)
        self.root.update()
        x, y, width, height = self.window.tree.bbox(iid, column)
        self.window.tree.event_generate('<ButtonPress-1>', x=x + width // 2, y=y + height // 2, state=state)
        self.window.tree.event_generate('<ButtonRelease-1>', x=x + width // 2, y=y + height // 2, state=state)
        self.root.update()

    def test_layout_at_minimum_size(self):
        self.root.geometry('1140x820')
        self.root.update()
        bottom = self.window.footer.winfo_rooty() + self.window.footer.winfo_height() - self.root.winfo_rooty()
        self.assertLessEqual(bottom, self.root.winfo_height())
        self.assertGreater(self.window.tree.winfo_height(), 130)
        for widget in self.window.controls:
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
        self.assertIs(self.window.base, self.window.cache_path)
        self.assertIs(self.window.base_entry, self.window.cache_entry)

    def test_drag_selects_contiguous_rows_without_toggling(self):
        tree = self.window.tree
        before = [entry['enabled'] for entry in self.window.library.state['songs']]
        x, y, width, height = tree.bbox('PicoTest001', 'title')
        end_y = tree.bbox('PicoTest005', 'title')[1] + height // 2
        tree.event_generate('<ButtonPress-1>', x=x + 30, y=y + height // 2)
        tree.event_generate('<B1-Motion>', x=x + 30, y=end_y, state=0x100)
        tree.event_generate('<ButtonRelease-1>', x=x + 30, y=end_y)
        self.root.update()
        self.assertEqual(tree.selection(), tuple(f'PicoTest{index:03d}' for index in range(1, 6)))
        self.assertEqual(before, [entry['enabled'] for entry in self.window.library.state['songs']])
        self.window.refresh()
        self.assertEqual(tree.selection(), tuple(f'PicoTest{index:03d}' for index in range(1, 6)))
        self.click('PicoTest003', 'on')
        after = [entry['enabled'] for entry in self.window.library.state['songs']]
        self.assertEqual(after, before[:3] + [not before[3]] + before[4:])

    def test_drag_autoscroll_and_ctrl_shift_selection(self):
        tree = self.window.tree
        x, y, width, height = tree.bbox('PicoTest000', 'title')
        tree.event_generate('<ButtonPress-1>', x=x + 25, y=y + height // 2)
        tree.event_generate('<B1-Motion>', x=x + 25, y=tree.winfo_height() + 20, state=0x100)
        deadline = time.monotonic() + 0.35
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        tree.event_generate('<ButtonRelease-1>', x=x + 25, y=tree.winfo_height() + 20)
        self.root.update()
        self.assertGreater(tree.yview()[0], 0)
        indices = [int(iid.removeprefix('PicoTest')) for iid in tree.selection()]
        self.assertEqual(indices, list(range(max(indices) + 1)))
        self.click('PicoTest001')
        self.click('PicoTest003', state=0x0004)
        self.assertEqual(set(tree.selection()), {'PicoTest001', 'PicoTest003'})
        self.click('PicoTest005', state=0x0001)
        self.assertTrue({'PicoTest003', 'PicoTest004', 'PicoTest005'} <= set(tree.selection()))

    def test_exact_presence_and_explicit_pack_assignment(self):
        window = self.window
        window.accept_inventory(self.inventory())
        self.assertEqual(window.tree.item('PicoTest000', 'values')[1], 'Да')
        self.assertEqual(window.tree.item('PicoTest001', 'values')[1], 'Нет')
        self.assertEqual(window.current_pack(), 'ostvol1')
        self.assertNotIn('debug', window.pack_labels.values())
        self.assertIn('debug', {pack['id'] for pack in window.inventory['packs']})
        first = window.library.state['songs'][0]
        window.pack_name.set('OST Vol. 2 [ostvol2]')
        window.choose_pack()
        self.assertNotIn('target_pack', first)
        self.assertEqual(first['cached_pack'], 'ostvol1')
        window.tree.selection_set('PicoTest000')
        window.assign_pack()
        self.assertEqual(first['target_pack'], 'ostvol2')
        self.assertEqual(first['cached_pack'], 'ostvol1')
        self.assertNotIn('target_pack', window.library.state['songs'][1])
        self.assertEqual(window.tree.get_children()[0], 'PicoTest000')
        window.cache_path.set(str(self.home / 'another.obb'))
        self.assertIsNone(window.inventory)
        self.assertEqual(window.tree.item('PicoTest000', 'values')[1], '?')

    def test_guarded_local_deletion_uses_reviewed_names_and_paths(self):
        window = self.window
        original = self.home / 'original_music.mp3'
        original.write_bytes(b'original')
        window.accept_inventory(self.inventory())
        first = window.library.state['songs'][0]
        first_folder = window.library.root / first['folder']
        second = window.library.state['songs'][1]
        second_folder = window.library.root / second['folder']
        window.tree.selection_set(second['level_id'])
        with patch.object(window, 'confirm_delete', return_value=True) as confirm:
            window.remove()
        reviewed, folders = confirm.call_args.args
        self.assertEqual(reviewed[0]['title'], second['title'])
        self.assertEqual(folders, [second_folder.resolve()])
        self.assertFalse(second_folder.exists())
        self.assertTrue(first_folder.is_dir())
        self.assertTrue(original.is_file())
        with patch.object(window, 'confirm_delete', return_value=False) as confirm:
            window.remove_outside_cache()
        self.assertTrue(confirm.call_args.kwargs['outside'])
        self.assertNotIn('PicoTest000', {entry['level_id'] for entry in confirm.call_args.args[0]})
        bad = window.library.state['songs'][1]
        bad['folder'] = str(self.home)
        window.tree.selection_set(bad['level_id'])
        with patch.object(app_v14.messagebox, 'showerror') as show_error, patch.object(window, 'confirm_delete') as confirm:
            window.remove()
        show_error.assert_called_once()
        confirm.assert_not_called()
        self.assertTrue(original.is_file())

    def test_read_cache_imports_and_defaults_only_cached_entries(self):
        window = self.window
        inventory = self.inventory()
        cache = Path(inventory['path'])
        cache.write_bytes(b'test cache input')
        window.library.state['songs'][0]['enabled'] = False
        window.library.state['songs'][1]['enabled'] = False

        def read_cache(path, assets, log):
            self.assertEqual(Path(path), cache)
            window.library.state['songs'][0]['enabled'] = True
            window.library.state['songs'].append({'level_id': 'NativeCustom', 'title': 'Из кэша', 'artist': '',
                                                  'storage': 'cache-reference', 'cached_pack': 'ostvol2',
                                                  'enabled': True, 'difficulties': ['Hard'], 'warnings': []})
            return inventory

        with patch.object(window.library, 'read_cache', side_effect=read_cache) as read:
            window.read_cache(str(cache))
            deadline = time.monotonic() + 3
            while window.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertFalse(window.busy)
        read.assert_called_once()
        self.assertTrue(window.library.state['songs'][0]['enabled'])
        self.assertFalse(window.library.state['songs'][1]['enabled'])
        self.assertIn('NativeCustom', window.tree.get_children())
        self.assertNotIn('StockSong', window.tree.get_children())
        self.assertEqual(window.current_pack(), 'ostvol1')

    def test_build_accepts_zero_enabled_custom_songs(self):
        window = self.window
        inventory = self.inventory()
        cache = Path(inventory['path'])
        cache.write_bytes(b'test cache input')
        window.accept_inventory(inventory)
        for entry in window.library.state['songs']:
            entry['enabled'] = False
        output = self.home / 'stock_only_output'
        output.mkdir()
        result = dict(inventory, songs=[song for song in inventory['songs'] if not song['custom']],
                      custom=0, ai=0, total=1)
        (output / 'cache_inventory.json').write_text(json.dumps(result), encoding='utf-8')
        with patch.object(app_v14, 'build', return_value=output) as build, \
             patch.object(app_v14.messagebox, 'showinfo'):
            window.start_build()
            deadline = time.monotonic() + 3
            while window.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
        self.assertFalse(window.busy)
        build.assert_called_once()
        self.assertEqual(build.call_args.args[1], [])
        self.assertEqual(window.last_output, output)
        self.assertFalse(any(entry['enabled'] for entry in window.library.state['songs']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
