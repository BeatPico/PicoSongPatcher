"""Portable Windows UI. User data stays next to the EXE, outside _internal."""
import sys,os,json,threading,queue,traceback
from pathlib import Path
SOURCE=Path(__file__).resolve().parent
if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(SOURCE.parent/'beat_saber_lab'/'python_packages'))
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,simpledialog
from library import Library,inspect_map,resolve_file
from engine import build,OBB
from neural_autogen import generate,available as ai_available,ALGORITHM as AI_ALGORITHM,engine_root,selected_model
from model_profiles import profiles
from model_training import train as train_model
from cache_view import inspect_cache,text_report,compare_library

HOME=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else SOURCE
ASSETS=Path(getattr(sys,'_MEIPASS',SOURCE))/'assets'
DEFAULT_BASE=r'C:\Users\home\Downloads\PICO_Beat_Saber_-MODDED-_1.40.8_OVRport_3.2.2\com.beatgames.beatsaber\main.1716.com.beatgames.beatsaber.obb'

class Window:
    def __init__(self,root):
        self.root=root;self.library=Library(HOME/'data');self.events=queue.Queue();self.busy=False;self.last_output=None;self.inventory=None
        saved_output=self.library.state.get('last_output')
        if saved_output and Path(saved_output).is_dir():self.last_output=Path(saved_output)
        source_path=self.library.state.get('source_obb') or (DEFAULT_BASE if Path(DEFAULT_BASE).is_file() else self.library.state.get('base_obb',''))
        preview_path=self.library.state.get('cache_obb') or self.library.state.get('base_obb') or source_path
        root.title('Pico Song Patcher 1.3 · Обучение AI');root.geometry('1180x950');root.minsize(1120,880)
        root.configure(bg='#f3f5f8')
        style=ttk.Style();style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10));style.configure('TFrame',background='#f3f5f8')
        style.configure('TLabel',background='#f3f5f8',foreground='#1d293b')
        style.configure('Title.TLabel',font=('Segoe UI',21,'bold'))
        style.configure('Hint.TLabel',foreground='#526276')
        style.configure('TButton',padding=(12,8));style.configure('Treeview',rowheight=32)
        style.configure('Accent.TButton',font=('Segoe UI',11,'bold'),background='#176b5b',foreground='white')
        frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Песни для твоего Pico',style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame,text='Beat Saber 1.40.8 · OVRport 3.2.2 · новые песни появятся в OST Vol. 1',style='Hint.TLabel').pack(anchor='w',pady=(3,18))
        ttk.Label(frame,text='1. Оригинальный OBB — основа для новой сборки').pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=(6,9))
        self.base=tk.StringVar(value=source_path)
        self.base_entry=ttk.Entry(row,textvariable=self.base);self.base_entry.pack(side='left',fill='x',expand=True)
        self.controls=[]
        self.button(row,'Выбрать OBB',self.choose_base).pack(side='left',padx=(8,0))
        cache_row=ttk.Frame(frame);cache_row.pack(fill='x',pady=(0,4))
        ttk.Label(cache_row,text='Посмотреть кэш:').pack(side='left',padx=(0,6))
        self.cache_path=tk.StringVar(value=preview_path)
        self.cache_entry=ttk.Entry(cache_row,textvariable=self.cache_path);self.cache_entry.pack(side='left',fill='x',expand=True)
        self.button(cache_row,'Открыть OBB…',self.choose_cache).pack(side='left',padx=(7,0))
        self.button(cache_row,'Песни внутри',self.show_cache).pack(side='left',padx=(7,0))
        self.cache_summary=tk.StringVar(value='Содержимое OBB ещё не прочитано.')
        ttk.Label(frame,textvariable=self.cache_summary,style='Hint.TLabel').pack(anchor='w',pady=(0,9))
        ttk.Label(frame,text='2. Библиотека песен').pack(anchor='w')
        toolbar=ttk.Frame(frame);toolbar.pack(fill='x',pady=7)
        self.button(toolbar,'Добавить папку…',self.add_folder).pack(side='left')
        self.button(toolbar,'Добавить ZIP…',self.add_zip).pack(side='left',padx=7)
        self.button(toolbar,'Из MP3 (AI)…',self.add_audio).pack(side='left',padx=(0,7))
        self.button(toolbar,'Пересоздать AI',self.regenerate_audio).pack(side='left',padx=(0,7))
        self.button(toolbar,'Вкл. / выкл.',self.toggle).pack(side='left')
        self.button(toolbar,'Убрать из списка',self.remove).pack(side='right')
        model_row=ttk.Frame(frame);model_row.pack(fill='x',pady=(0,5))
        ttk.Label(model_row,text='AI-модель:').pack(side='left',padx=(0,7))
        self.model_name=tk.StringVar()
        self.model_combo=ttk.Combobox(model_row,textvariable=self.model_name,state='readonly',width=36)
        self.model_combo.pack(side='left');self.model_combo.bind('<<ComboboxSelected>>',self.choose_model)
        self.controls.append(self.model_combo);self.refresh_models()
        self.button(model_row,'Дообучить на примерах',self.train_model).pack(side='left',padx=7)
        self.button(model_row,'Сравнение моделей',self.show_model_report).pack(side='left')
        self.compatible=tk.BooleanVar(value=False)
        check=ttk.Checkbutton(frame,text='Упрощать карты с модами (анимации и особые правила будут удалены)',variable=self.compatible)
        check.pack(anchor='w',pady=(0,8));self.controls.append(check)
        table=ttk.Frame(frame);table.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(table,columns=('on','cached','title','artist','diff','status'),show='headings',selectmode='extended',height=5)
        for col,label,width in [('on','В новую сборку',110),('cached','В этом OBB',100),('title','Песня',260),('artist','Исполнитель',165),('diff','Сложности',200),('status','Проверить',125)]:
            self.tree.heading(col,text=label);self.tree.column(col,width=width,minwidth=60,stretch=col!='on')
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.bind('<Button-1>',self.click_enabled)
        self.tree.bind('<Double-1>',lambda e:self.toggle() if self.tree.identify_column(e.x)!='#1' else None)
        self.tree.tag_configure('warning',foreground='#8c5200')
        self.count=tk.StringVar();ttk.Label(frame,textvariable=self.count,style='Hint.TLabel').pack(anchor='w',pady=(6,10))
        actions=ttk.Frame(frame);actions.pack(fill='x')
        self.button(actions,'3. Собрать кэш',self.start_build,'Accent.TButton').pack(side='left')
        self.open_button=ttk.Button(actions,text='Открыть готовые файлы',command=self.open_output,state='disabled')
        self.open_button.pack(side='left',padx=8)
        if self.last_output:self.open_button.configure(state='normal')
        self.button(actions,'Подробности',self.show_selected).pack(side='left')
        self.button(actions,'Состав новой сборки',self.show_next_build).pack(side='left',padx=(7,0))
        ttk.Label(actions,text='Каждая сборка включает все включённые песни.',style='Hint.TLabel').pack(side='right')
        self.progress=ttk.Progressbar(frame,mode='indeterminate');self.progress.pack(fill='x',pady=(12,8))
        self.logbox=tk.Text(frame,height=4,wrap='word',font=('Segoe UI',9),bg='#ffffff',fg='#26354b',relief='flat',padx=10,pady=8)
        self.logbox.pack(fill='x');self.logbox.configure(state='disabled')
        ttk.Label(frame,text='Перенос на очки выполняется вручную. Исходный кэш программа не меняет.',style='Hint.TLabel').pack(anchor='w',pady=(8,0))
        self.refresh();self.log('AI создаёт карты для всей записи: акценты, плотность, позиции и направления блоков. Музыка обрабатывается на этом ПК.')
        root.after(100,self.poll);root.protocol('WM_DELETE_WINDOW',self.close)
        if preview_path and Path(preview_path).is_file() and '--self-test' not in sys.argv:root.after(250,lambda:self.read_cache(preview_path))
    def button(self,parent,text,command,style='TButton'):
        b=ttk.Button(parent,text=text,command=command,style=style);self.controls.append(b);return b
    def refresh_models(self):
        self.models=profiles(engine_root())
        self.model_combo.configure(values=[p['label'] for p in self.models])
        current=self.library.state.get('ai_model','base')
        record=next((p for p in self.models if p['id']==current),None)
        self.model_name.set(record['label'] if record else 'Модель недоступна: '+current)
    def choose_model(self,event=None):
        record=next((p for p in self.models if p['label']==self.model_name.get()),None)
        if record:
            self.library.state['ai_model']=record['id'];self.library.save()
            self.log('Выбрана AI-модель: '+record['label'])
    def show_model_report(self):
        try:
            selected=selected_model(self.library)
            report=selected.get('report')
            if not report:
                candidates=[p for p in self.models if p.get('report')]
                if candidates:report=candidates[0]['report']
            if report and Path(report).is_dir():os.startfile(report)
            else:messagebox.showinfo('Сравнение','Сравнение появится после дообучения и проверки на отложенной песне.')
        except Exception as error:messagebox.showerror('Модель',str(error))
    def train_model(self):
        if self.busy:return
        project=SOURCE if not getattr(sys,'frozen',False) else HOME
        pointer=HOME/'training_project.txt'
        if getattr(sys,'frozen',False) and pointer.is_file():project=Path(pointer.read_text(encoding='utf-8').strip())
        if not (project/'training_examples/preparation_result.json').is_file():
            selected=filedialog.askdirectory(title='Папка проекта с подготовленными примерами training_examples')
            if not selected:return
            project=Path(selected)
            if not (project/'training_examples/preparation_result.json').is_file():
                messagebox.showerror('Обучающие данные','В выбранной папке нет training_examples/preparation_result.json.');return
        self.set_busy(True)
        def worker():
            try:
                result=train_model(engine_root(),project,log=lambda text:self.events.put(('log',text)))
                self.events.put(('trained',result))
            except Exception as error:self.events.put(('error',str(error)))
        threading.Thread(target=worker,daemon=True).start()
    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        cached={s['id'] for s in self.inventory['songs']} if self.inventory else None
        for i,s in enumerate(self.library.state['songs']):
            status=(s.get('ai_model_label','Нейросеть AI') if s.get('auto_backend') else 'Старый Auto') if s.get('autogenerated') else ('Есть замечания' if s.get('warnings') else '—')
            presence='?' if cached is None else ('Да' if s['level_id'] in cached else 'Нет')
            self.tree.insert('', 'end',iid=str(i),tags=('warning',) if s.get('warnings') else (),values=('Да' if s.get('enabled',True) else '—',presence,s['title'],s['artist'],', '.join(s['difficulties']),status))
        count=f"В новую сборку: {sum(s.get('enabled',True) for s in self.library.state['songs'])} · В библиотеке: {len(self.library.state['songs'])}"
        if self.inventory:
            diff=compare_library(self.inventory,self.library.state['songs'])
            count+=f" · Из выбранных ещё нет в этом OBB: {len(diff['selected_not_in_cache'])}"
        self.count.set(count+' · Щелчок по первой колонке включает/выключает песню.')
    def log(self,text):
        self.logbox.configure(state='normal');self.logbox.insert('end',str(text)+'\n');self.logbox.see('end');self.logbox.configure(state='disabled')
    def choose_base(self):
        path=filedialog.askopenfilename(title='Исходный OBB из скачанного комплекта игры',filetypes=[('Кэш Beat Saber','*.obb')])
        if path:self.base.set(path);self.library.state.update(base_obb=path,source_obb=path);self.library.save()
    def choose_cache(self):
        if self.busy:return
        typed=self.cache_path.get().strip()
        path=filedialog.askopenfilename(title='OBB: посмотреть фактический список песен',initialdir=str(Path(typed).parent) if typed else None,filetypes=[('Кэш Beat Saber','*.obb')])
        if path:self.read_cache(path)
    def read_cache(self,path):
        if self.busy:return
        self.cache_path.set(str(path));self.inventory=None;self.refresh();self.cache_summary.set('Читаю список песен из OBB…');self.set_busy(True)
        def worker():
            try:self.events.put(('cache_read',inspect_cache(path)))
            except Exception as error:self.events.put(('cache_error',str(error)))
        threading.Thread(target=worker,daemon=True).start()
    def accept_inventory(self,inventory):
        self.inventory=inventory;self.cache_path.set(inventory['path'])
        self.cache_summary.set(f"В этом OBB: {inventory['total']} песен OST Vol. 1 · Добавленных: {inventory['custom']} · AI: {inventory['ai']} · Старых Auto: {inventory['auto']}")
        self.library.state.update(cache_obb=inventory['path'],source_obb=self.base.get())
        self.library.save();self.refresh()
        (self.library.root/'viewed_cache.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.library.root/'ПЕСНИ_В_ПРОСМОТРЕННОМ_КЭШЕ.txt').write_text(text_report(inventory),encoding='utf-8')
    def show_cache(self):
        if not self.inventory:
            if self.cache_path.get().strip():self.read_cache(self.cache_path.get().strip())
            else:messagebox.showinfo('Выбери OBB','Нажми «Открыть OBB…», чтобы увидеть песни внутри кэша.')
            return
        dialog=tk.Toplevel(self.root);dialog.title('Песни внутри выбранного OBB');dialog.geometry('1000x620');dialog.transient(self.root)
        frame=ttk.Frame(dialog,padding=16);frame.pack(fill='both',expand=True)
        inv=self.inventory
        ttk.Label(frame,text=inv['path'],wraplength=950).pack(anchor='w',pady=(0,8))
        ttk.Label(frame,text=f"Всего {inv['total']}: исходных {inv['total']-inv['custom']}, добавленных {inv['custom']}, новых AI {inv['ai']}.").pack(anchor='w',pady=(0,8))
        tree=ttk.Treeview(frame,columns=('title','artist','kind','diff'),show='headings')
        for col,label,width in [('title','Песня',330),('artist','Исполнитель',240),('kind','Версия',120),('diff','Сложности',230)]:
            tree.heading(col,text=label);tree.column(col,width=width,minwidth=60)
        scroll=ttk.Scrollbar(frame,command=tree.yview);tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');tree.pack(fill='both',expand=True)
        for song in inv['songs']:tree.insert('', 'end',values=(' '.join(song['title'].split()),song['artist'],song['kind'],', '.join(song['difficulties'])))
        buttons=ttk.Frame(dialog,padding=(16,8));buttons.pack(fill='x')
        ttk.Button(buttons,text='В новую сборку — как в этом OBB',command=lambda:self.use_cache_selection(inv)).pack(side='left')
        ttk.Button(buttons,text='Сохранить список…',command=lambda:self.save_cache_list(inv)).pack(side='left',padx=8)
        ttk.Button(buttons,text='Закрыть',command=dialog.destroy).pack(side='right')
    def use_cache_selection(self,inventory):
        if self.busy:return
        ids={s['id'] for s in inventory['songs']}
        for song in self.library.state['songs']:song['enabled']=song['level_id'] in ids
        self.library.save();self.refresh();self.log('Выбор для новой сборки совпадает с песнями OBB, которые есть в библиотеке.')
        unknown=compare_library(inventory,self.library.state['songs'])['cached_not_in_library']
        if unknown:messagebox.showwarning('Не все песни есть в библиотеке',f'В OBB есть {len(unknown)} добавленных песен без исходных карт в библиотеке. Их список доступен в «Песни внутри».')
    def save_cache_list(self,inventory):
        path=filedialog.asksaveasfilename(title='Сохранить список песен из OBB',defaultextension='.txt',initialfile='Песни в кэше.txt',filetypes=[('Текстовый список','*.txt')])
        if path:Path(path).write_text(text_report(inventory),encoding='utf-8')
    def show_next_build(self):
        selected=[s for s in self.library.state['songs'] if s.get('enabled',True)]
        rows=[f'Будет добавлено: {len(selected)}. Исходные песни игры сохраняются.','']
        for i,song in enumerate(selected,1):
            kind='AI' if song.get('auto_backend') else ('Старый Auto' if song.get('autogenerated') else 'Готовая карта')
            rows.append(f"{i}. {song['title']} — {song['artist']} [{kind}]\n   "+', '.join(song['difficulties']))
        duplicates={}
        for song in selected:duplicates.setdefault((song['title'].casefold().strip(),song['artist'].casefold().strip()),[]).append(song)
        repeated=[v[0]['title'] for v in duplicates.values() if len(v)>1]
        if repeated:rows.extend(['','Выбрано несколько версий одной песни: '+', '.join(repeated)+'. Выключи лишнюю строку, если нужна одна версия.'])
        self.show_report('Состав следующей сборки','\n'.join(rows))
    def click_enabled(self,event):
        if self.busy or self.tree.identify_column(event.x)!='#1':return
        iid=self.tree.identify_row(event.y)
        if iid:
            song=self.library.state['songs'][int(iid)];song['enabled']=not song.get('enabled',True)
            self.library.save();self.refresh()
    def add_folder(self):
        path=filedialog.askdirectory(title='Папка карты с info.dat')
        if path:self.import_maps([path])
    def add_zip(self):
        paths=filedialog.askopenfilenames(title='ZIP с картами BeatSaver',filetypes=[('Карты BeatSaver','*.zip')])
        if paths:self.import_maps(paths)
    def add_audio(self):
        paths=filedialog.askopenfilenames(title='Полные песни для AI-карт',filetypes=[('Музыка','*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.wma *.opus'),('Все файлы','*.*')])
        if not paths:return
        value=simpledialog.askstring('Нейросеть: полные песни','Создам Easy, Normal и Hard для всей записи. Ограничения по минутам нет.\nAI работает на этом ПК; обработка может занять несколько минут.\n\nBPM: оставь «Авто» или введи известный темп (40–320).\nОдно число применяется ко всем выбранным файлам.',initialvalue='Авто',parent=self.root)
        if value is None:return
        try:
            bpm=None if value.strip().lower() in ('','авто','auto') else float(value.replace(',','.'))
            if bpm is not None and not 40<=bpm<=320:raise ValueError()
        except ValueError:messagebox.showerror('Неверный BPM','Введи «Авто» или число от 40 до 320.');return
        self.import_maps(paths,audio=True,bpm=bpm)
    def regenerate_audio(self):
        if self.busy:return
        selected=[self.library.state['songs'][int(i)] for i in self.tree.selection()]
        try:profile=selected_model(self.library)
        except Exception as error:messagebox.showerror('Модель',str(error));return
        selected=[s for s in selected if s.get('auto_algorithm')!=AI_ALGORITHM or s.get('ai_model','base')!=profile['id'] or (profile['id']!='base' and s.get('ai_model_revision')!=profile['revision'])]
        if not selected:
            messagebox.showinfo('Выбери песни','Выдели старые Auto-карты или другие песни, которые хочешь пересоздать.\nБудет использована полная музыка из библиотеки и создан новый AI-рисунок.\nКарты этой версии AI уже готовы.');return
        self.set_busy(True)
        def worker():
            notes=[];errors=[]
            for old in selected:
                try:
                    folder=self.library.root/old['folder'];parsed=inspect_map(folder,old.get('compatible',False))
                    source=resolve_file(folder,parsed['info']['_songFilename'])
                    song,_=generate(source,self.library,ASSETS,log=lambda t:self.events.put(('log',t)),replace_entry=old)
                    self.events.put(('log','Готова AI-карта: '+song['title']+'; прежняя версия выключена.'))
                    notes.append(song['title']+': создана новая AI-карта. Старые данные сохранены, прежняя строка выключена.')
                except Exception as error:errors.append(old['title']+': '+str(error))
            body=('ОШИБКИ\n\n'+'\n\n'.join(errors)+'\n\n' if errors else '')+'\n\n'.join(notes)
            (self.library.root/'last_import.txt').write_text(body,encoding='utf-8')
            self.events.put(('imported',body))
        threading.Thread(target=worker,daemon=True).start()
    def show_report(self,title,body):
        dialog=tk.Toplevel(self.root);dialog.title(title);dialog.geometry('850x560');dialog.transient(self.root)
        frame=ttk.Frame(dialog,padding=16);frame.pack(fill='both',expand=True)
        box=tk.Text(frame,wrap='word',font=('Segoe UI',10),padx=10,pady=10)
        scroll=ttk.Scrollbar(frame,command=box.yview);box.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');box.pack(fill='both',expand=True);box.insert('end',body);box.configure(state='disabled')
        ttk.Button(dialog,text='Закрыть',command=dialog.destroy).pack(pady=(0,12))
    def show_selected(self):
        selected=self.tree.selection()
        if not selected:return
        rows=[]
        for iid in selected:
            s=self.library.state['songs'][int(iid)]
            rows.append(s['title']+'\n'+(', '.join(s['difficulties']))+'\n'+'\n'.join(s.get('warnings') or ['Замечаний при импорте нет.']))
        self.show_report('Подробности карт','\n\n'.join(rows))
    def set_busy(self,busy):
        self.busy=busy
        for b in self.controls:b.configure(state='disabled' if busy else 'normal')
        self.model_combo.configure(state='disabled' if busy else 'readonly')
        self.base_entry.configure(state='disabled' if busy else 'normal')
        self.cache_entry.configure(state='disabled' if busy else 'normal')
        if busy:self.progress.start(12)
        else:self.progress.stop()
    def import_maps(self,paths,audio=False,bpm=None):
        self.set_busy(True)
        compatible=self.compatible.get()
        def task():
            errors=[];notes=[]
            for path in paths:
                try:
                    if audio:song,added=generate(path,self.library,ASSETS,bpm,lambda t:self.events.put(('log',t)))
                    else:song,added=self.library.import_path(path,compatible=compatible)
                    self.events.put(('log',('Добавлена: ' if added else 'Уже в библиотеке: ')+song['title']))
                    if song.get('warnings'):notes.append(song['title']+':\n'+'\n'.join(song['warnings']))
                except Exception as e:errors.append(Path(path).name+': '+str(e))
            body=('ОШИБКИ\n\n'+'\n\n'.join(errors)+'\n\n' if errors else '')+('ЗАМЕЧАНИЯ\n\n'+'\n\n'.join(notes) if notes else '')
            if body:(self.library.root/'last_import.txt').write_text(body,encoding='utf-8')
            self.events.put(('imported',body))
        threading.Thread(target=task,daemon=True).start()
    def toggle(self):
        if self.busy:return
        for iid in self.tree.selection():
            s=self.library.state['songs'][int(iid)];s['enabled']=not s.get('enabled',True)
        self.library.save();self.refresh()
    def remove(self):
        if self.busy:return
        selected={int(i) for i in self.tree.selection()}
        self.library.state['songs']=[s for i,s in enumerate(self.library.state['songs']) if i not in selected]
        self.library.save();self.refresh()
    def start_build(self):
        entries=[dict(s) for s in self.library.state['songs'] if s.get('enabled',True)]
        if not entries:messagebox.showinfo('Нет песен','Добавь хотя бы одну карту.');return
        self.library.state.update(base_obb=self.base.get(),source_obb=self.base.get());self.library.save();self.set_busy(True)
        self.open_button.configure(state='disabled')
        # Read Tk variables on the UI thread, then pass a plain string to the worker.
        base=self.base.get()
        def worker():
            try:
                out=build(base,entries,self.library.root,HOME/'output',ASSETS,lambda t:self.events.put(('log',t)))
                inventory=json.loads((out/'cache_inventory.json').read_text(encoding='utf-8'))
                self.events.put(('built',(str(out),inventory)))
            except Exception as e:
                (self.library.root/'last_error.txt').write_text(traceback.format_exc(),encoding='utf-8')
                self.events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='log':self.log(data)
                elif kind=='trained':
                    self.library.state['ai_model']=data['id'];self.library.save()
                    self.set_busy(False);self.refresh_models()
                    self.show_report('Обучение завершено','Выбрана дообученная модель. Пересоздай песню для проверки; исходную модель можно выбрать в том же списке.\n\nМетрики и полные карты для сравнения: '+data['report'])
                elif kind=='imported':
                    self.set_busy(False);self.refresh()
                    if data:self.show_report('Результат добавления карт',data)
                elif kind=='built':
                    self.set_busy(False);self.last_output=Path(data[0]);self.open_button.configure(state='normal')
                    self.library.state['last_output']=str(self.last_output);self.accept_inventory(data[1])
                    (HOME/'last_build.json').write_text(json.dumps({'output':str(self.last_output),'cache':data[1]['path']},ensure_ascii=False,indent=2),encoding='utf-8')
                    messagebox.showinfo('Готово','Кэш собран и проверен.\nНажми «Открыть готовые файлы».\nИнструкция по переносу находится внутри.')
                elif kind=='cache_read':self.set_busy(False);self.accept_inventory(data);self.log(f"Кэш прочитан: {data['custom']} добавленных песен, из них AI {data['ai']}.")
                elif kind=='cache_error':
                    self.set_busy(False);self.cache_summary.set('Содержимое OBB не прочитано.');self.log(data);messagebox.showerror('Не удалось открыть OBB',data)
                elif kind=='error':self.set_busy(False);self.log('Ошибка: '+data);messagebox.showerror('Не удалось собрать',data)
        except queue.Empty:pass
        self.root.after(100,self.poll)
    def open_output(self):
        if self.last_output and self.last_output.exists():os.startfile(self.last_output)
    def close(self):
        if self.busy:messagebox.showinfo('Идёт обработка','Дождись окончания обработки. Окно можно свернуть.');return
        self.root.destroy()

def main():
    if '--inspect-cache' in sys.argv:
        path=sys.argv[sys.argv.index('--inspect-cache')+1];inventory=inspect_cache(path)
        (HOME/'cache_inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
        (HOME/'ПЕСНИ_В_КЭШЕ.txt').write_text(text_report(inventory),encoding='utf-8');return
    if '--self-test-ai' in sys.argv:
        import tempfile
        source=sys.argv[sys.argv.index('--self-test-ai')+1]
        temp_root=HOME/'data/tmp';temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='frozen_ai_test_',dir=temp_root) as td:
            lib=Library(Path(td)/'data')
            entry,_=generate(source,lib,ASSETS,difficulties={'Normal':2.5},log=lambda text:None)
            analysis=json.loads((lib.root/entry['analysis_file']).read_text(encoding='utf-8'))
            (HOME/'ai_self_test.json').write_text(json.dumps({'status':'PASS','duration':entry['full_audio_seconds'],
                'notes':analysis['difficulties'],'backend':entry['auto_backend'],'cropped_audio':analysis['cropped_audio']},indent=2),encoding='utf-8')
        return
    if '--build' in sys.argv:
        lib=Library(HOME/'data');source=lib.state.get('source_obb') or (DEFAULT_BASE if Path(DEFAULT_BASE).is_file() else lib.state['base_obb'])
        out=build(source,[s for s in lib.state['songs'] if s.get('enabled',True)],lib.root,HOME/'output',ASSETS)
        lib.state.update(last_output=str(out),cache_obb=str(out/'Android/obb/com.beatgames.beatsaber'/OBB),source_obb=source);lib.save()
        (HOME/'last_build.json').write_text(json.dumps({'output':str(out)}),encoding='utf-8');return
    root=tk.Tk();app=Window(root)
    if '--self-test' in sys.argv:
        root.withdraw();root.update_idletasks()
        check={'tk':root.tk.call('info','patchlevel'),'assets':str(ASSETS),
               'template':(ASSETS/'gameplay_template.bundle').is_file(),
               'ffmpeg':(ASSETS/'tools'/'ffmpeg.exe').is_file(),
               'library_songs':len(app.library.state['songs']),'widgets':len(app.controls)}
        check['source_obb']=app.base.get();check['cache_obb']=app.cache_path.get()
        root.update_idletasks();check['bottom_widget_y']=app.logbox.winfo_rooty()+app.logbox.winfo_height()-root.winfo_rooty();check['window_height']=root.winfo_height()
        import numpy as np
        check['numpy']=np.__version__;check['version']='1.3';check['ai_engine']=ai_available();check['ai_models']=[p['id'] for p in app.models]
        (HOME/'self_test.json').write_text(json.dumps(check,indent=2),encoding='utf-8');root.destroy();return
    root.mainloop()

if __name__=='__main__':main()
