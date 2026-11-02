import json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
APP=ROOT/'dist_v2'/'Pico Song Patcher 1.1'
TARGET=ROOT/'PicoSongPatcher-1.1-portable.zip'
excluded={'output','tmp','__pycache__'}
with zipfile.ZipFile(TARGET,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in APP.rglob('*'):
        if not p.is_file():continue
        relative=p.relative_to(APP)
        if any(x in excluded for x in relative.parts):continue
        if relative.name in ['self_test.json','last_build.json']:continue
        z.write(p,str(Path('Pico Song Patcher 1.1')/relative))
with zipfile.ZipFile(TARGET) as z:
    assert z.testzip() is None
    assert 'Pico Song Patcher 1.1/Pico Song Patcher 1.1.exe' in z.namelist()
    assert 'Pico Song Patcher 1.1/data/library.json' in z.namelist()
print(TARGET)
print('ZIP bytes:',TARGET.stat().st_size)
