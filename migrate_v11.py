"""Create a separate 1.1 library from the user's 1.0 library, read-only source."""
import sys,json,shutil,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'beat_saber_lab/python_packages'))
from library import Library,inspect_map

old=ROOT/'dist/Pico Song Patcher/data'
app=ROOT/'dist_v2/Pico Song Patcher 1.1';new=app/'data'
assert app.is_dir() and not new.exists(),'Target must be a new distribution without a library'
before=hashlib.sha256((old/'library.json').read_bytes()).hexdigest()
shutil.copytree(old,new,ignore=shutil.ignore_patterns('tmp'))
lib=Library(new);originals=json.loads((old/'library.json').read_text(encoding='utf-8'))['songs']
for entry in originals:
    assert inspect_map(new/entry['folder'])['hash']==entry['hash']
codes={'5544b','5547d','55488','5549e','55528','5552a','55587','555af','555b6','555da','555f0','55624','53a7a'}
report=['Pico Song Patcher 1.1\n','Скопирована прежняя библиотека: '+str(len(originals))+' песен.\n',
        '13 новых архивов добавлены. Карты с модами импортированы с упрощением.\n',
        'Это приближение без Noodle/Chroma: движения могут отличаться. Начни с Easy.\n']
for path in sorted(p for p in Path(r'C:\Users\home\Downloads').glob('*.zip') if p.name.split(' ')[0] in codes):
    entry,added=lib.import_path(path,compatible=True)
    assert added
    report.extend(['\n'+path.name,', '.join(entry['difficulties']),*entry['warnings']])
assert len(lib.state['songs'])==len(originals)+13
assert lib.state['songs'][:len(originals)]==originals
assert hashlib.sha256((old/'library.json').read_bytes()).hexdigest()==before
(new/'migration.json').write_text(json.dumps({'source':str(old),'source_library_sha256':before,
    'original_songs':len(originals),'recovered_songs':13,'total':len(lib.state['songs']),
    'source_unchanged':True},indent=2),encoding='utf-8')
text='\n'.join(report)
(new/'last_import.txt').write_text(text,encoding='utf-8')
(app/'НОВЫЕ_ПЕСНИ_ЗАМЕЧАНИЯ.txt').write_text(text,encoding='utf-8')
shutil.copy2(ROOT/'README.txt',app/'ПРОЧТИ_МЕНЯ.txt')
print('Separate library ready:',len(lib.state['songs']),'songs; original library unchanged',flush=True)
