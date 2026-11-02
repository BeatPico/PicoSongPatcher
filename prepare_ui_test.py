import sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'beat_saber_lab/python_packages'))
from library import Library,inspect_map
app=ROOT/'dist_v2/Pico Song Patcher 1.1';test=ROOT/'validation_v11/frozen_ui'
test.mkdir(exist_ok=True)
shutil.copy2(app/'Pico Song Patcher 1.1.exe',test/'Pico Song Patcher 1.1.exe')
shutil.copytree(app/'_internal',test/'_internal',dirs_exist_ok=True)
lib=Library(app/'data')
rows=['13 новых архивов: результат импорта с упрощением карт, требующих модов.',
      'Исходные архивы и прежняя библиотека не изменены.',
      'Новые карты требуют проверки в очках.']
for entry in lib.state['songs']:
    if not entry.get('compatible'):continue
    entry['warnings']=inspect_map(lib.root/entry['folder'],True)['warnings']
    rows.extend(['\n'+entry['title'],', '.join(entry['difficulties']),*entry['warnings']])
lib.save()
(app/'НОВЫЕ_ПЕСНИ_ЗАМЕЧАНИЯ.txt').write_text('\n'.join(rows),encoding='utf-8')
(lib.root/'last_import.txt').write_text('\n'.join(rows),encoding='utf-8')
shutil.copy2(ROOT/'README.txt',app/'ПРОЧТИ_МЕНЯ.txt')
print('Test runtime ready. Release MB:',round(sum(p.stat().st_size for p in app.rglob('*') if p.is_file())/1024**2,1))
