"""Separate 1.4 desktop entry point; the 1.3.1 application stays untouched."""
import json
import os
import queue
import sys
import tempfile
import threading
import traceback
import zipfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(SOURCE.parent / 'beat_saber_lab' / 'python_packages'))

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from cache_archive import cached_audio, inspect_cache, text_report
from arenas import DEFAULT_ARENA, VARIED_ARENA
from cache_builder import build
from library import inspect_map, resolve_file
from library_v14 import LibraryV14
from model_profiles import profiles, selected_id
from model_training import train as train_model
from neural_autogen import (available as ai_available, engine_root, generate,
                            needs_regeneration, object_options, selected_model)

HOME = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else SOURCE
ASSETS = Path(getattr(sys, '_MEIPASS', SOURCE)) / 'assets'
DEFAULT_BASE = ''
VERSION = '1.5.2'


class Window:
    def __init__(self, root, read_on_start=True):
        self.root = root
        self.library = LibraryV14(HOME / 'data')
        self.events = queue.Queue()
        self.busy = False
        self.inventory = None
        self.last_output = None
        self._poll_after = None
        self._drag_anchor = None
        self._drag_after = None
        self._drag_y = 0
        self._build_after_read = False
        saved_output = self.library.state.get('last_output', '')
        if saved_output and Path(saved_output).is_dir():
            self.last_output = Path(saved_output)
        source = (self.library.state.get('cache_obb') or self.library.state.get('source_obb')
                  or self.library.state.get('base_obb') or DEFAULT_BASE)

        root.title('Pico Song Patcher '+VERSION+' · Библиотека и паки')
        root.geometry('1140x920')
        root.minsize(1140, 820)
        root.configure(bg='#f3f5f8')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#f3f5f8')
        style.configure('TLabel', background='#f3f5f8', foreground='#1d293b')
        style.configure('Title.TLabel', font=('Segoe UI', 20, 'bold'))
        style.configure('Hint.TLabel', foreground='#526276')
        style.configure('TButton', padding=(10, 6))
        style.configure('Treeview', rowheight=29)
        style.configure('Accent.TButton', font=('Segoe UI', 10, 'bold'),
                        background='#176b5b', foreground='white')
        self.controls = []
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(9, weight=1, minsize=130)
        ttk.Label(frame, text='Песни для твоего Pico', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(frame, text='Beat Saber 1.40.8 · OVRport 3.2.2 · готовые песни и новые AI-карты',
                  style='Hint.TLabel').grid(row=1, column=0, sticky='w', pady=(0, 8))
        ttk.Label(frame, text='Кэш для просмотра и новой сборки').grid(row=2, column=0, sticky='w')
        cache_row = ttk.Frame(frame)
        cache_row.grid(row=3, column=0, sticky='ew', pady=4)
        self.cache_path = tk.StringVar(root, value=source)
        # Both names refer to the same input, so there can be no hidden second base.
        self.base = self.cache_path
        self.cache_entry = ttk.Entry(cache_row, textvariable=self.cache_path)
        self.base_entry = self.cache_entry
        self.cache_entry.pack(side='left', fill='x', expand=True)
        self.cache_entry.bind('<Return>', lambda event: self.read_cache(self.cache_path.get().strip()))
        self.button(cache_row, 'Выбрать OBB…', self.choose_cache).pack(side='left', padx=(7, 0))
        self.button(cache_row, 'Прочитать', lambda: self.read_cache(self.cache_path.get().strip())).pack(side='left', padx=(7, 0))
        self.button(cache_row, 'Песни внутри', self.show_cache).pack(side='left', padx=(7, 0))
        self.cache_summary = tk.StringVar(root, value='Выбери оригинальный или уже изменённый OBB. Будут прочитаны все доступные паки.')
        ttk.Label(frame, textvariable=self.cache_summary, style='Hint.TLabel').grid(row=4, column=0, sticky='w', pady=(0, 8))

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=5, column=0, sticky='ew', pady=(0, 6))
        for label, command in [('Добавить папку…', self.add_folder), ('Добавить ZIP…', self.add_zip),
                               ('Из MP3 (AI)…', self.add_audio), ('Пересоздать AI', self.regenerate_audio),
                               ('Вкл. / выкл.', self.toggle)]:
            self.button(toolbar, label, command).pack(side='left', padx=(0, 6))
        self.button(toolbar, 'Удалить выбранные…', self.remove).pack(side='right')

        model_row = ttk.Frame(frame)
        model_row.grid(row=6, column=0, sticky='ew', pady=(0, 6))
        ttk.Label(model_row, text='AI-модель:').pack(side='left', padx=(0, 6))
        self.model_name = tk.StringVar(root)
        self.model_combo = ttk.Combobox(model_row, textvariable=self.model_name, state='readonly', width=31)
        self.model_combo.pack(side='left')
        self.model_combo.bind('<<ComboboxSelected>>', self.choose_model)
        self.controls.append(self.model_combo)
        self.refresh_models()
        self.button(model_row, 'Обучить модель на эталонах', self.train_model).pack(side='left', padx=6)
        self.button(model_row, 'Сравнение моделей', self.show_model_report).pack(side='left')
        self.ai_walls = tk.BooleanVar(root, value=bool(self.library.state.get('ai_walls', False)))
        self.ai_bombs = tk.BooleanVar(root, value=bool(self.library.state.get('ai_bombs', False)))
        for label, variable in [('Стены', self.ai_walls), ('Бомбы', self.ai_bombs)]:
            control = ttk.Checkbutton(model_row, text=label, variable=variable, command=self.save_object_options)
            control.pack(side='left', padx=(8, 0))
            self.controls.append(control)

        pack_row = ttk.Frame(frame)
        pack_row.grid(row=7, column=0, sticky='ew', pady=(0, 6))
        ttk.Label(pack_row, text='Пак для новых песен:').pack(side='left', padx=(0, 6))
        self.pack_name = tk.StringVar(root)
        self.pack_combo = ttk.Combobox(pack_row, textvariable=self.pack_name, state='readonly', width=27)
        self.pack_combo.pack(side='left')
        self.pack_combo.bind('<<ComboboxSelected>>', self.choose_pack)
        self.controls.append(self.pack_combo)
        self.packs = [{'id': 'ostvol1', 'name': 'OST Vol. 1'}]
        self.refresh_packs()
        self.button(pack_row, 'Назначить выбранным', self.assign_pack).pack(side='left', padx=6)
        self.button(pack_row, 'Удалить локальные вне OBB…', self.remove_outside_cache).pack(side='right')

        arena_row = ttk.Frame(frame)
        arena_row.grid(row=8, column=0, sticky='ew', pady=(0, 6))
        self.compatible = tk.BooleanVar(root, value=False)
        compatible = ttk.Checkbutton(arena_row, text='Упрощать карты с модами (без анимаций и особых правил)',
                                     variable=self.compatible)
        compatible.pack(side='left')
        self.controls.append(compatible)
        self.arena_name = tk.StringVar(root)
        self.arena_combo = ttk.Combobox(arena_row, textvariable=self.arena_name, state='readonly', width=26)
        self.arena_combo.pack(side='right')
        ttk.Label(arena_row, text='Арена AI:').pack(side='right', padx=6)
        self.arena_combo.bind('<<ComboboxSelected>>', self.choose_ai_arena)
        self.controls.append(self.arena_combo)
        self.refresh_arenas()

        table = ttk.Frame(frame)
        table.grid(row=9, column=0, sticky='nsew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('on', 'cached', 'title', 'artist', 'pack', 'origin', 'diff'),
                                 show='headings', selectmode='extended', height=5)
        for column, label, width in [('on', 'В сборку', 76), ('cached', 'В этом OBB', 87),
                                     ('title', 'Песня', 217), ('artist', 'Исполнитель', 144),
                                     ('pack', 'Пак', 154), ('origin', 'Версия / объекты AI', 193),
                                     ('diff', 'Сложности', 164)]:
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=55, stretch=column not in ('on', 'cached'))
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.bind('<Button-1>', self.tree_press)
        self.tree.bind('<B1-Motion>', self.tree_motion)
        self.tree.bind('<ButtonRelease-1>', self.tree_release)
        self.tree.bind('<Double-1>', self.tree_double)
        self.tree.bind('<space>', lambda event: self.toggle() or 'break')
        self.tree.tag_configure('warning', foreground='#8c5200')
        self.count = tk.StringVar(root)
        ttk.Label(frame, textvariable=self.count, style='Hint.TLabel').grid(row=10, column=0, sticky='w', pady=(5, 7))

        actions = ttk.Frame(frame)
        actions.grid(row=11, column=0, sticky='ew')
        self.build_button = self.button(actions, 'Собрать кэш', self.start_build, 'Accent.TButton')
        self.build_button.pack(side='left')
        self.open_button = self.button(actions, 'Открыть готовые файлы', self.open_output)
        self.open_button.pack(side='left', padx=7)
        self.button(actions, 'Подробности', self.show_selected).pack(side='left')
        self.button(actions, 'Состав новой сборки', self.show_next_build).pack(side='left', padx=7)
        self.guide_button = self.button(actions, 'Инструкция', self.open_guide)
        self.guide_button.pack(side='right')
        self.open_button.configure(state='normal' if self.last_output else 'disabled')
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.grid(row=12, column=0, sticky='ew', pady=(9, 7))
        self.logbox = tk.Text(frame, height=3, wrap='word', font=('Segoe UI', 9), bg='white', fg='#26354b',
                              relief='flat', padx=9, pady=6)
        self.logbox.grid(row=13, column=0, sticky='ew')
        self.logbox.configure(state='disabled')
        self.footer = ttk.Label(frame, text='В библиотеке — добавленные песни. Штатные песни видны в «Песни внутри». Перенос на очки — вручную.',
                                style='Hint.TLabel')
        self.footer.grid(row=14, column=0, sticky='w', pady=(6, 0))
        self.refresh()
        self.log('Песни из открытого кэша включаются в сборку. Их исходные паки сохраняются; перенести песни можно кнопкой «Назначить выбранным».')
        self.cache_path.trace_add('write', self.cache_path_changed)
        self._poll_after = root.after(100, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)
        if read_on_start and source and Path(source).is_file():
            root.after(250, lambda: self.read_cache(source))

    def button(self, parent, text, command, style='TButton'):
        button = ttk.Button(parent, text=text, command=command, style=style)
        self.controls.append(button)
        return button

    def selected_entries(self):
        ids = set(self.tree.selection())
        return [song for song in self.library.state['songs'] if song['level_id'] in ids]

    def refresh_models(self):
        self.models = profiles(engine_root())
        self.model_combo.configure(values=[profile['label'] for profile in self.models])
        current = selected_id(engine_root(), self.library.state.get('ai_model'))
        record = next((profile for profile in self.models if profile['id'] == current), None)
        self.model_name.set(record['label'] if record else 'Модель недоступна: ' + current)

    def choose_model(self, event=None):
        if self.busy:
            return
        record = next((profile for profile in self.models if profile['label'] == self.model_name.get()), None)
        if record:
            self.library.state['ai_model'] = record['id']
            self.library.save()
            self.log('Выбрана AI-модель: ' + record['label'])

    def refresh_arenas(self):
        arenas = (self.inventory or {}).get('arenas', [DEFAULT_ARENA])
        self.arena_labels = {'Разные для песен': VARIED_ARENA, 'Стандартная': DEFAULT_ARENA}
        self.arena_labels.update({name.removesuffix('Environment'): name for name in arenas if name != DEFAULT_ARENA})
        self.arena_combo.configure(values=list(self.arena_labels))
        wanted = self.library.state.get('ai_arena_mode', VARIED_ARENA)
        self.arena_name.set(next((label for label, mode in self.arena_labels.items() if mode == wanted), 'Разные для песен'))

    def choose_ai_arena(self, event=None):
        self.library.state['ai_arena_mode'] = self.arena_labels[self.arena_name.get()]
        self.library.save()

    def save_object_options(self):
        if self.busy:
            return
        self.library.state.update(ai_walls=bool(self.ai_walls.get()), ai_bombs=bool(self.ai_bombs.get()))
        self.library.save()
        self.log('Стены и бомбы применятся к новым и пересозданным AI-картам.')

    def pack_label(self, pack_id):
        return next((pack['name'] for pack in self.packs if pack['id'] == pack_id), pack_id)

    def refresh_packs(self):
        self.pack_labels = {f"{pack['name']} [{pack['id']}]": pack['id'] for pack in self.packs
                            if pack['id'].casefold() != 'debug' and pack['name'].casefold() != 'debug'}
        self.pack_combo.configure(values=list(self.pack_labels))
        if not self.pack_labels:
            self.pack_name.set('Нет доступных паков')
            return
        wanted = self.library.state.get('target_pack', 'ostvol1')
        if wanted not in self.pack_labels.values():
            wanted = 'ostvol1' if 'ostvol1' in self.pack_labels.values() else next(iter(self.pack_labels.values()))
        self.pack_name.set(next(label for label, pack_id in self.pack_labels.items() if pack_id == wanted))

    def current_pack(self):
        return self.pack_labels.get(self.pack_name.get(), 'ostvol1')

    def choose_pack(self, event=None):
        if self.busy:
            return
        self.library.state['target_pack'] = self.current_pack()
        self.library.save()
        self.refresh()

    def assign_pack(self):
        if self.busy:
            return
        selected = self.selected_entries()
        if not selected:
            messagebox.showinfo('Выбери песни', 'Выдели песни в библиотеке, затем назначь им пак.', parent=self.root)
            return
        target = self.current_pack()
        for song in selected:
            song['target_pack'] = target
        self.library.save()
        self.refresh()
        self.log(f'Пак «{self.pack_label(target)}» назначен выбранным песням: {len(selected)}.')

    def entry_pack(self, entry):
        if entry.get('target_pack'):
            return self.pack_label(entry['target_pack']) + ' →'
        if self.inventory:
            packs = list(dict.fromkeys(song['pack_name'] for song in self.inventory['songs'] if song['id'] == entry['level_id']))
            if packs:
                return ', '.join(packs)
        return self.pack_label(entry.get('cached_pack') or self.current_pack())

    @staticmethod
    def entry_origin(entry):
        cached = entry.get('storage') in ('cached', 'cache-reference')
        if entry.get('auto_backend') or entry.get('ai_model'):
            objects = entry.get('ai_objects', {})
            options = [name for key, name in [('walls', 'стены'), ('bombs', 'бомбы')] if objects.get(key)]
            label = ('Кэш · ' if cached else '') + entry.get('ai_model_label', 'AI')
            return label + (' · ' + ', '.join(options) if options else ' · без стен/бомб')
        if entry.get('autogenerated'):
            return 'Кэш · ' + entry.get('cache_kind', 'AI/Auto') if cached else 'Старый Auto'
        return 'Из кэша' if cached else 'Готовая карта'

    def refresh(self):
        selected = self.tree.selection()
        focus = self.tree.focus()
        first = self.tree.yview()[0]
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        cached = {song['id'] for song in self.inventory['songs']} if self.inventory else None
        for song in self.library.state['songs']:
            presence = '?' if cached is None else ('Да' if song['level_id'] in cached else 'Нет')
            self.tree.insert('', 'end', iid=song['level_id'], tags=('warning',) if song.get('warnings') else (),
                             values=('☑' if song.get('enabled', True) else '☐', presence, song['title'],
                                     song.get('artist', ''), self.entry_pack(song), self.entry_origin(song),
                                     ', '.join(song.get('difficulties', []))))
        remaining = set(self.tree.get_children())
        self.tree.selection_set([iid for iid in selected if iid in remaining])
        if focus in remaining:
            self.tree.focus(focus)
        self.tree.yview_moveto(first)
        songs = self.library.state['songs']
        enabled = [song for song in songs if song.get('enabled', True)]
        text = f'В сборку: {len(enabled)} / {len(songs)}'
        if cached is not None:
            text += f" · Нет в этом OBB: {sum(song['level_id'] not in cached for song in enabled)}"
        self.count.set(text + ' · Первая колонка — включить; протяни мышью по строкам — выделить несколько.')

    def tree_press(self, event):
        self._stop_drag()
        if self.busy:
            return 'break'
        iid = self.tree.identify_row(event.y)
        if not iid:
            return None
        if self.tree.identify_column(event.x) == '#1':
            self.toggle_ids([iid])
            return 'break'
        # Let Tk handle the platform's ordinary Ctrl/Shift selection behavior.
        if event.state & (0x0001 | 0x0004):
            return None
        self._drag_anchor = iid
        self._drag_y = event.y
        self.tree.focus(iid)
        self.tree.selection_set(iid)
        return 'break'

    def _select_drag_range(self, y):
        children = self.tree.get_children()
        if self._drag_anchor not in children:
            self._stop_drag()
            return
        y = max(30, min(y, self.tree.winfo_height() - 4))
        hit = self.tree.identify_row(y)
        if not hit:
            visible = [iid for iid in children if self.tree.bbox(iid)]
            if not visible:
                return
            hit = visible[0] if y < self.tree.winfo_height() / 2 else visible[-1]
        start, end = sorted((children.index(self._drag_anchor), children.index(hit)))
        self.tree.selection_set(children[start:end + 1])
        self.tree.focus(hit)

    def tree_motion(self, event):
        if self.busy:
            return 'break'
        if self._drag_anchor:
            self._drag_y = event.y
            self._select_drag_range(event.y)
            if self._drag_after is None:
                self._drag_after = self.root.after(60, self._drag_scroll)
            return 'break'
        return None

    def _drag_scroll(self):
        self._drag_after = None
        if not self._drag_anchor:
            return
        direction = -1 if self._drag_y < 32 else (1 if self._drag_y > self.tree.winfo_height() - 18 else 0)
        if direction:
            self.tree.yview_scroll(direction, 'units')
            self._select_drag_range(self._drag_y)
            self._drag_after = self.root.after(60, self._drag_scroll)

    def _stop_drag(self):
        self._drag_anchor = None
        if self._drag_after is not None:
            self.root.after_cancel(self._drag_after)
            self._drag_after = None

    def tree_release(self, event):
        active = self._drag_anchor is not None
        self._stop_drag()
        return 'break' if active else None

    def tree_double(self, event):
        if self.tree.identify_column(event.x) != '#1' and not self.busy:
            self._stop_drag()
            self.show_selected()
        return 'break'

    def toggle_ids(self, ids):
        if self.busy:
            return
        identifiers = set(ids)
        for entry in self.library.state['songs']:
            if entry['level_id'] in identifiers:
                entry['enabled'] = not entry.get('enabled', True)
        self.library.save()
        self.refresh()

    def toggle(self):
        self.toggle_ids(self.tree.selection())

    def log(self, text):
        self.logbox.configure(state='normal')
        self.logbox.insert('end', str(text) + '\n')
        self.logbox.see('end')
        self.logbox.configure(state='disabled')

    def set_busy(self, busy):
        self.busy = busy
        self._stop_drag()
        for control in self.controls:
            control.configure(state='disabled' if busy else 'normal')
        for combo in (self.model_combo, self.pack_combo, self.arena_combo):
            combo.configure(state='disabled' if busy else 'readonly')
        self.cache_entry.configure(state='disabled' if busy else 'normal')
        self.open_button.configure(state='normal' if not busy and self.last_output else 'disabled')
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def run_worker(self, task, result, context):
        self.set_busy(True)

        def worker():
            try:
                self.events.put((result, task()))
            except Exception as error:
                (self.library.root / 'last_error.txt').write_text(traceback.format_exc(), encoding='utf-8')
                self.events.put(('error', (context, str(error))))

        threading.Thread(target=worker, daemon=True).start()

    def choose_cache(self):
        if self.busy:
            return
        current = self.cache_path.get().strip()
        path = filedialog.askopenfilename(parent=self.root, title='Оригинальный или изменённый кэш Beat Saber',
                                          initialdir=str(Path(current).parent) if current else None,
                                          filetypes=[('Кэш Beat Saber', '*.obb'), ('Все файлы', '*.*')])
        if path:
            self.read_cache(path)

    def cache_path_changed(self, *args):
        if self.inventory and not self.same_cache_path(self.cache_path.get(), self.inventory['path']):
            self.inventory = None
            self.refresh_arenas()
            self.cache_summary.set('Путь изменён. Нажми «Прочитать», чтобы загрузить песни выбранного OBB.')
            self.refresh()

    def read_cache(self, path, build_after=False):
        if self.busy:
            return
        if not path or not Path(path).is_file():
            messagebox.showerror('Кэш не найден', 'Выбери существующий файл OBB.', parent=self.root)
            return
        path = str(Path(path).resolve())
        self.cache_path.set(path)
        self.inventory = None
        self.refresh_arenas()
        self._build_after_read = build_after
        self.cache_summary.set('Читаю все паки и добавленные песни из OBB…')
        self.refresh()
        self.run_worker(lambda: self.library.read_cache(path, ASSETS, lambda text: self.events.put(('log', text))),
                        'cache_read', 'Не удалось прочитать OBB')

    def accept_inventory(self, inventory):
        self.inventory = inventory
        self.cache_path.set(inventory['path'])
        self.packs = inventory['packs']
        self.refresh_packs()
        self.refresh_arenas()
        ids = {song['id'] for song in inventory['songs'] if song['custom']}
        by_id = {song['id']: song for song in inventory['songs'] if song['custom']}
        for entry in self.library.state['songs']:
            entry['in_viewed_cache'] = entry['level_id'] in ids
            if entry['level_id'] in by_id:
                entry['cached_pack'] = by_id[entry['level_id']]['pack_id']
                entry['cache_kind'] = by_id[entry['level_id']]['kind']
        self.library.state.update(source_obb=inventory['path'], cache_obb=inventory['path'], base_obb=inventory['path'])
        self.library.save()
        warnings = len(inventory.get('warnings', []))
        self.cache_summary.set(f"Паков: {len(inventory['packs'])} · Всего записей: {inventory['total']} · Добавленных песен: {inventory['custom']} · AI: {inventory['ai']}"
                               + (f' · Не прочитано паков: {warnings}' if warnings else ''))
        (self.library.root / 'viewed_cache.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
        (self.library.root / 'ПЕСНИ_В_ПРОСМОТРЕННОМ_КЭШЕ.txt').write_text(text_report(inventory), encoding='utf-8')
        self.refresh()

    def show_cache(self):
        if not self.inventory:
            self.read_cache(self.cache_path.get().strip())
            return
        inventory = self.inventory
        dialog = tk.Toplevel(self.root)
        dialog.title('Все паки выбранного OBB')
        dialog.geometry('1080x650')
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=inventory['path'], wraplength=1030).pack(anchor='w', pady=(0, 6))
        filters = ttk.Frame(frame)
        filters.pack(fill='x', pady=(0, 8))
        kind = tk.StringVar(dialog, value='Только добавленные')
        kind_combo = ttk.Combobox(filters, textvariable=kind, state='readonly', width=24,
                                  values=['Только добавленные', 'Все песни'])
        kind_combo.pack(side='left')
        pack = tk.StringVar(dialog, value='Все паки')
        pack_labels = {'Все паки': None} | {f"{item['name']} [{item['id']}]": item['id'] for item in inventory['packs']}
        pack_combo = ttk.Combobox(filters, textvariable=pack, state='readonly', width=36, values=list(pack_labels))
        pack_combo.pack(side='left', padx=8)
        count = tk.StringVar(dialog)
        ttk.Label(filters, textvariable=count).pack(side='right')
        table = ttk.Frame(frame)
        table.pack(fill='both', expand=True)
        tree = ttk.Treeview(table, columns=('title', 'artist', 'pack', 'kind', 'diff'), show='headings')
        for column, label, width in [('title', 'Песня', 280), ('artist', 'Исполнитель', 190), ('pack', 'Пак', 180),
                                     ('kind', 'Версия', 120), ('diff', 'Сложности', 240)]:
            tree.heading(column, text=label)
            tree.column(column, width=width, minwidth=60)
        scroll = ttk.Scrollbar(table, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)

        def populate(event=None):
            children = tree.get_children()
            if children:
                tree.delete(*children)
            songs = [song for song in inventory['songs'] if (kind.get() == 'Все песни' or song['custom'])
                     and (pack_labels[pack.get()] is None or song['pack_id'] == pack_labels[pack.get()])]
            for song in songs:
                tree.insert('', 'end', values=(' '.join(song['title'].split()), song['artist'], song['pack_name'],
                                              song['kind'], ', '.join(song['difficulties'])))
            count.set(f'Показано: {len(songs)}')

        kind_combo.bind('<<ComboboxSelected>>', populate)
        pack_combo.bind('<<ComboboxSelected>>', populate)
        populate()
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=(10, 0))
        ttk.Button(buttons, text='Сохранить весь список…', command=lambda: self.save_cache_list(inventory)).pack(side='left')
        if inventory.get('warnings'):
            ttk.Button(buttons, text='Замечания чтения', command=lambda: self.show_report('Не прочитанные паки', '\n'.join(inventory['warnings']))).pack(side='left', padx=8)
        ttk.Button(buttons, text='Закрыть', command=dialog.destroy).pack(side='right')

    def save_cache_list(self, inventory):
        path = filedialog.asksaveasfilename(parent=self.root, title='Список песен во всех паках', defaultextension='.txt',
                                           initialfile='Песни в кэше.txt', filetypes=[('Текст', '*.txt')])
        if path:
            Path(path).write_text(text_report(inventory), encoding='utf-8')

    def confirm_delete(self, selected, folders, outside=False):
        dialog = tk.Toplevel(self.root)
        dialog.title('Удалить локальные песни')
        dialog.geometry('880x590')
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill='both', expand=True)
        title = 'Удалить локальные песни, которых нет в выбранном OBB?' if outside else 'Удалить выбранные песни и их локальные файлы?'
        ttk.Label(frame, text=title, font=('Segoe UI', 12, 'bold'), wraplength=830).pack(anchor='w')
        if outside:
            ttk.Label(frame, text='Сравнение по точному ID с OBB:\n' + self.inventory['path'], wraplength=830).pack(anchor='w', pady=(8, 0))
        lines = [f'Песен: {len(selected)}', '']
        lines += [f"• {song['title']} — {song.get('artist', '')}\n  ID: {song['level_id']}" for song in selected]
        lines += ['', 'Будут удалены локальные папки:']
        lines += [str(folder) for folder in folders] or ['Нет отдельных папок: будут удалены только записи библиотеки.']
        lines += ['', 'Исходные ZIP, музыка, папки импорта и выбранный OBB сохраняются.']
        box = tk.Text(frame, wrap='word', font=('Segoe UI', 10), padx=8, pady=8)
        scroll = ttk.Scrollbar(frame, command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y', pady=10)
        box.pack(fill='both', expand=True, pady=10)
        box.insert('end', '\n'.join(lines))
        box.configure(state='disabled')
        result = [False]

        def accept():
            result[0] = True
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=(16, 0, 16, 14))
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Удалить локальные файлы', command=accept).pack(side='left')
        cancel = ttk.Button(buttons, text='Отмена', command=dialog.destroy)
        cancel.pack(side='right')
        cancel.focus_set()
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        self.root.wait_window(dialog)
        return result[0]

    def delete_ids(self, identifiers, outside=False):
        if self.busy:
            return
        try:
            selected, folders = self.library.deletion_plan(identifiers)
            if not selected:
                messagebox.showinfo('Нет песен для удаления', 'Подходящих локальных песен нет.', parent=self.root)
                return
            if self.confirm_delete(selected, folders, outside=outside):
                count = self.library.delete_local([song['level_id'] for song in selected])
                self.refresh()
                self.log(f'Удалено локальных песен: {count}.')
        except Exception as error:
            messagebox.showerror('Удаление не выполнено', str(error), parent=self.root)

    def remove(self):
        self.delete_ids(self.tree.selection())

    def remove_outside_cache(self):
        if self.busy:
            return
        if not self.inventory or not self.same_cache_path(self.cache_path.get(), self.inventory['path']):
            messagebox.showinfo('Сначала прочитай OBB', 'Прочитай выбранный OBB перед сравнением и удалением.', parent=self.root)
            return
        self.delete_ids(self.library.outside_cache(self.inventory), outside=True)

    @staticmethod
    def same_cache_path(first, second):
        try:
            return Path(first).resolve() == Path(second).resolve()
        except (OSError, ValueError):
            return False

    def add_folder(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self.root, title='Папка карты с Info.dat')
        if path:
            self.import_maps([path])

    def add_zip(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title='ZIP с картами BeatSaver', filetypes=[('Карты BeatSaver', '*.zip')])
        if paths:
            self.import_maps(paths)

    def add_audio(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title='Полные песни для AI-карт',
                                            filetypes=[('Музыка', '*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.wma *.opus'), ('Все файлы', '*.*')])
        if not paths:
            return
        value = simpledialog.askstring('Нейросеть: полные песни',
                                       'Создам Easy, Normal и Hard для всей записи на этом ПК.\nОбработка может занять несколько минут.\n\nBPM: «Авто» или известный темп (40–320).\nОдно число применяется ко всем выбранным файлам.',
                                       initialvalue='Авто', parent=self.root)
        if value is None:
            return
        try:
            bpm = None if value.strip().lower() in ('', 'авто', 'auto') else float(value.replace(',', '.'))
            if bpm is not None and not 40 <= bpm <= 320:
                raise ValueError()
        except ValueError:
            messagebox.showerror('Неверный BPM', 'Введи «Авто» или число от 40 до 320.', parent=self.root)
            return
        self.import_maps(paths, audio=True, bpm=bpm)

    def import_maps(self, paths, audio=False, bpm=None):
        if self.busy:
            return
        compatible, pack = self.compatible.get(), self.current_pack()
        if audio:
            self.choose_ai_arena()

        def task():
            errors, notes = [], []
            for path in paths:
                try:
                    if audio:
                        song, added = generate(path, self.library, ASSETS, bpm, lambda text: self.events.put(('log', text)))
                    else:
                        song, added = self.library.import_path(path, compatible=compatible)
                    if added:
                        song['target_pack'] = pack
                        self.library.save()
                    self.events.put(('log', ('Добавлена: ' if added else 'Уже в библиотеке: ') + song['title']))
                    if song.get('warnings'):
                        notes.append(song['title'] + ':\n' + '\n'.join(song['warnings']))
                except Exception as error:
                    errors.append(Path(path).name + ': ' + str(error))
            body = ('ОШИБКИ\n\n' + '\n\n'.join(errors) + '\n\n' if errors else '') + ('ЗАМЕЧАНИЯ\n\n' + '\n\n'.join(notes) if notes else '')
            if body:
                (self.library.root / 'last_import.txt').write_text(body, encoding='utf-8')
            return body

        self.run_worker(task, 'imported', 'Не удалось добавить песни')

    def regeneration_source(self, entry, work, fallback_cache):
        if entry.get('storage') == 'cached':
            return cached_audio(self.library.root / entry['folder'], work / 'song.wav')
        if entry.get('storage') == 'cache-reference':
            source = Path(entry.get('cache_source_obb') or fallback_cache)
            if not source.is_file():
                source = Path(fallback_cache)
            with zipfile.ZipFile(source) as archive:
                raw = archive.read('assets/BeatmapLevelsData/' + entry['level_id'].lower())
            if not raw.startswith(b'UnityFS'):
                raise ValueError('Игровые данные песни не являются UnityFS.')
            (work / 'level.bundle').write_bytes(raw)
            return cached_audio(work, work / 'song.wav')
        folder = self.library.root / entry['folder']
        parsed = inspect_map(folder, entry.get('compatible', False))
        return resolve_file(folder, parsed['info']['_songFilename'])

    def regenerate_audio(self):
        if self.busy:
            return
        try:
            profile = selected_model(self.library)
            objects = object_options(self.library)
        except Exception as error:
            messagebox.showerror('Модель', str(error), parent=self.root)
            return
        self.choose_ai_arena()
        arena_mode = self.library.state['ai_arena_mode']
        arenas = (self.inventory or {}).get('arenas', [DEFAULT_ARENA])
        selected = [entry for entry in self.selected_entries()
                    if needs_regeneration(entry, profile, objects, arena_mode=arena_mode)
                    or entry.get('ai_arena', DEFAULT_ARENA) not in arenas]
        if not selected:
            messagebox.showinfo('Выбери песни', 'Выдели песни для пересоздания. Карты с выбранной моделью и текущими настройками уже готовы.', parent=self.root)
            return
        fallback, pack = self.cache_path.get().strip(), self.current_pack()

        def task():
            errors, notes = [], []
            temporary = self.library.root / 'tmp'
            temporary.mkdir(exist_ok=True)
            for old in selected:
                try:
                    with tempfile.TemporaryDirectory(prefix='regenerate_', dir=temporary) as directory:
                        source = self.regeneration_source(old, Path(directory), fallback)
                        song, _ = generate(source, self.library, ASSETS, log=lambda text: self.events.put(('log', text)), replace_entry=old)
                    song['target_pack'] = old.get('target_pack') or old.get('cached_pack') or pack
                    self.library.save()
                    self.events.put(('log', 'Готова AI-карта: ' + song['title'] + '; прежняя версия выключена.'))
                    notes.append(song['title'] + ': создана новая AI-карта. Старые данные сохранены, прежняя строка выключена.')
                except Exception as error:
                    errors.append(old['title'] + ': ' + str(error))
            body = ('ОШИБКИ\n\n' + '\n\n'.join(errors) + '\n\n' if errors else '') + '\n\n'.join(notes)
            (self.library.root / 'last_import.txt').write_text(body, encoding='utf-8')
            return body

        self.run_worker(task, 'imported', 'Не удалось пересоздать песни')

    def train_model(self):
        if self.busy:
            return
        project = SOURCE if not getattr(sys, 'frozen', False) else HOME
        pointer = HOME / 'training_project.txt'
        if getattr(sys, 'frozen', False) and pointer.is_file():
            project = Path(pointer.read_text(encoding='utf-8').strip())

        def prepared_file(folder):
            return next((folder / relative for relative in ('training_examples_70/preparation_result.json',
                                                           'training_examples_40/preparation_result.json',
                                                           'training_examples/preparation_result.json')
                         if (folder / relative).is_file()), None)

        prepared = prepared_file(project)
        if prepared is None:
            chosen = filedialog.askdirectory(parent=self.root, title='Проект с подготовленными эталонными картами')
            if not chosen:
                return
            project = Path(chosen)
            prepared = prepared_file(project)
            if prepared is None:
                messagebox.showerror('Обучающие данные', 'Нет training_examples_40/preparation_result.json или training_examples/preparation_result.json.', parent=self.root)
                return
        source_profile = selected_id(engine_root(), self.library.state.get('ai_model'))
        self.log('Обучение выбранной модели на подготовленных эталонах. Выделенные песни библиотеки в обучение не входят.')
        self.run_worker(lambda: train_model(engine_root(), project, log=lambda text: self.events.put(('log', text)),
                                            prepared_file=prepared, source_profile=source_profile),
                        'trained', 'Обучение не завершено')

    def show_model_report(self):
        try:
            profile = selected_model(self.library)
            report = profile.get('report')
            if report and Path(report).is_dir():
                os.startfile(report)
            else:
                messagebox.showinfo('Сравнение моделей', 'Отчёт выбранной модели появится после её обучения и проверки на отложенной песне.', parent=self.root)
        except Exception as error:
            messagebox.showerror('Модель', str(error), parent=self.root)

    def show_report(self, title, body):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry('880x560')
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill='both', expand=True)
        box = tk.Text(frame, wrap='word', font=('Segoe UI', 10), padx=9, pady=9)
        scroll = ttk.Scrollbar(frame, command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        box.pack(fill='both', expand=True)
        box.insert('end', body)
        box.configure(state='disabled')
        ttk.Button(dialog, text='Закрыть', command=dialog.destroy).pack(pady=(0, 12))

    def show_selected(self):
        rows = []
        for song in self.selected_entries():
            lines = [song['title'] + ' — ' + song.get('artist', ''), 'ID: ' + song['level_id'],
                     'Пак в новой сборке: ' + self.entry_pack(song), 'Версия: ' + self.entry_origin(song),
                     'Сложности: ' + ', '.join(song.get('difficulties', []))]
            if song.get('folder'):
                lines.append('Локальная папка: ' + str(self.library.root / song['folder']))
            lines += song.get('warnings') or ['Замечаний при импорте нет.']
            rows.append('\n'.join(lines))
        if rows:
            self.show_report('Подробности карт', '\n\n'.join(rows))

    def show_next_build(self):
        selected = [song for song in self.library.state['songs'] if song.get('enabled', True)]
        rows = [f'Добавленных песен в новой сборке: {len(selected)}. Штатные песни игры сохраняются.', '']
        groups = {}
        for index, song in enumerate(selected, 1):
            rows.append(f"{index}. {song['title']} — {song.get('artist', '')}\n   {self.entry_pack(song)} · {self.entry_origin(song)}\n   ID: {song['level_id']}")
            groups.setdefault((song['title'].strip().casefold(), song.get('artist', '').strip().casefold()), []).append(song)
        duplicates = [values for values in groups.values() if len(values) > 1]
        if duplicates:
            rows += ['', 'Выбрано несколько версий одной песни:']
            rows += [f"• {values[0]['title']}: {len(values)} версии с разными ID" for values in duplicates]
        self.show_report('Состав новой сборки', '\n'.join(rows))

    def start_build(self):
        if self.busy:
            return
        base = self.cache_path.get().strip()
        if not base or not Path(base).is_file():
            messagebox.showerror('Кэш не найден', 'Выбери существующий OBB для новой сборки.', parent=self.root)
            return
        if not self.inventory or not self.same_cache_path(base, self.inventory['path']):
            self.read_cache(base, build_after=True)
            return
        entries = [dict(song) for song in self.library.state['songs'] if song.get('enabled', True)]
        target = self.current_pack()
        self.library.state.update(base_obb=base, source_obb=base, cache_obb=base, target_pack=target)
        self.library.save()

        def task():
            out = build(base, entries, self.library.root, HOME / 'output', ASSETS,
                        lambda text: self.events.put(('log', text)), target_pack=target)
            inventory = json.loads((out / 'cache_inventory.json').read_text(encoding='utf-8'))
            return str(out), inventory

        self.run_worker(task, 'built', 'Не удалось собрать кэш')

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'log':
                    self.log(data)
                elif kind == 'cache_read':
                    self.set_busy(False)
                    self.accept_inventory(data)
                    self.log(f"Кэш прочитан: {len(data['packs'])} паков; добавленных песен: {data['custom']}.")
                    if self._build_after_read:
                        self._build_after_read = False
                        self.start_build()
                elif kind == 'imported':
                    self.set_busy(False)
                    self.refresh()
                    if data:
                        self.show_report('Результат добавления карт', data)
                elif kind == 'trained':
                    self.library.state['ai_model'] = data['id']
                    self.library.save()
                    self.set_busy(False)
                    self.refresh_models()
                    self.show_report('Обучение завершено', 'Выбрана новая дообученная модель. Исходная модель доступна в том же списке.\nПересоздай песню для проверки.\n\nОтчёт: ' + str(data.get('report', '')))
                elif kind == 'built':
                    self.last_output = Path(data[0])
                    self.library.state['last_output'] = str(self.last_output)
                    self.accept_inventory(data[1])
                    self.set_busy(False)
                    (HOME / 'last_build.json').write_text(json.dumps({'output': str(self.last_output), 'cache': data[1]['path'], 'version': VERSION}, ensure_ascii=False, indent=2), encoding='utf-8')
                    messagebox.showinfo('Готово', 'Кэш собран и проверен.\nНажми «Открыть готовые файлы».\nИнструкция по переносу находится внутри.', parent=self.root)
                elif kind == 'error':
                    self._build_after_read = False
                    self.set_busy(False)
                    if self.inventory is None:
                        self.cache_summary.set('Содержимое OBB не прочитано. Выбери кэш и повтори чтение.')
                    self.refresh()
                    self.log(data[0] + ': ' + data[1])
                    messagebox.showerror(data[0], data[1], parent=self.root)
        except queue.Empty:
            pass
        self._poll_after = self.root.after(100, self.poll)

    def open_output(self):
        if self.last_output and self.last_output.is_dir():
            os.startfile(self.last_output)

    def open_guide(self):
        path = next((candidate for candidate in (HOME / 'ИНСТРУКЦИЯ.html', SOURCE / 'docs/USER_GUIDE_RU.html')
                     if candidate.is_file()), None)
        if path:
            os.startfile(path)
        else:
            messagebox.showinfo('Инструкция', 'Полная инструкция: https://github.com/BeatPico/PicoSongPatcher/blob/main/docs/USER_GUIDE_RU.md', parent=self.root)

    def close(self):
        if self.busy:
            messagebox.showinfo('Идёт обработка', 'Дождись окончания обработки. Окно можно свернуть.', parent=self.root)
            return
        self._stop_drag()
        if self._poll_after is not None:
            self.root.after_cancel(self._poll_after)
        self.root.destroy()


def main():
    if '--inspect-cache' in sys.argv:
        path = sys.argv[sys.argv.index('--inspect-cache') + 1]
        inventory = inspect_cache(path, ASSETS)
        (HOME / 'cache_inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
        (HOME / 'ПЕСНИ_В_КЭШЕ.txt').write_text(text_report(inventory), encoding='utf-8')
        return
    if '--build' in sys.argv:
        library = LibraryV14(HOME / 'data')
        source = library.state.get('cache_obb') or library.state.get('source_obb') or library.state.get('base_obb') or DEFAULT_BASE
        enabled = {song['level_id']: song.get('enabled', True) for song in library.state['songs']}
        library.read_cache(source, ASSETS)
        # A command-line build consumes the saved checkboxes. Only newly discovered
        # cached songs receive the default enabled flag from read_cache.
        for song in library.state['songs']:
            if song['level_id'] in enabled:
                song['enabled'] = enabled[song['level_id']]
        out = build(source, [song for song in library.state['songs'] if song.get('enabled', True)],
                    library.root, HOME / 'output', ASSETS, target_pack=library.state.get('target_pack', 'ostvol1'))
        inventory = json.loads((out / 'cache_inventory.json').read_text(encoding='utf-8'))
        library.state.update(last_output=str(out), source_obb=inventory['path'], cache_obb=inventory['path'])
        library.save()
        (HOME / 'last_build.json').write_text(json.dumps({'output': str(out), 'cache': inventory['path'], 'version': VERSION}), encoding='utf-8')
        return
    root = tk.Tk()
    window = Window(root, read_on_start='--self-test' not in sys.argv)
    if '--self-test' in sys.argv:
        root.update()
        check = {'version': VERSION, 'tk': root.tk.call('info', 'patchlevel'), 'assets': str(ASSETS),
                 'template': (ASSETS / 'gameplay_template.bundle').is_file(),
                 'ffmpeg': (ASSETS / 'tools/ffmpeg.exe').is_file(), 'library_songs': len(window.library.state['songs']),
                 'widgets': len(window.controls), 'source_obb': window.cache_path.get(),
                 'ai_engine': ai_available(), 'ai_models': [profile['id'] for profile in window.models],
                 'ai_objects': object_options(window.library), 'window_width': root.winfo_width(),
                 'window_height': root.winfo_height(), 'requested_width': root.winfo_reqwidth(),
                 'requested_height': root.winfo_reqheight(),
                 'bottom_widget_y': window.footer.winfo_rooty() + window.footer.winfo_height() - root.winfo_rooty(),
                 'build_button_visible': bool(window.build_button.winfo_ismapped())}
        check['guide_button_visible'] = bool(window.guide_button.winfo_ismapped())
        check['offline_guide'] = (HOME / 'ИНСТРУКЦИЯ.html').is_file()
        check['ai_arena_mode'] = window.arena_labels[window.arena_name.get()]
        (HOME / 'self_test.json').write_text(json.dumps(check, ensure_ascii=False, indent=2), encoding='utf-8')
        window.close()
        return
    root.mainloop()


if __name__ == '__main__':
    main()
