import sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LAB=ROOT.parent/'beat_saber_lab'
sys.path[:0]=[str(LAB/'build_packages'),str(LAB/'python_packages')]
import PyInstaller.__main__
PyInstaller.__main__.run([
    str(ROOT/'app.py'),'--noconfirm','--windowed','--onedir',
    '--name','Pico Song Patcher 1.3.1','--distpath',str(ROOT/'build_v4_1/distribution'),
    '--contents-directory','_internal_1_3_1',
    '--workpath',str(ROOT/'build_v4_1'),'--specpath',str(ROOT),
    '--paths',str(LAB/'python_packages'),'--paths',str(ROOT),
    '--collect-all','UnityPy','--collect-all','fmod_toolkit','--collect-all','PIL',
    '--collect-all','archspec','--collect-all','tpk_ar',
    '--collect-all','numpy',
    '--add-data',str(ROOT/'assets')+';assets',
])
# Only update runtime files; PyInstaller's --noconfirm must never delete user data.
built=ROOT/'build_v4_1/distribution/Pico Song Patcher 1.3.1'
target=ROOT/'dist_v3/Pico Song Patcher 1.2';target.mkdir(parents=True,exist_ok=True)
shutil.copytree(built/'_internal_1_3_1',target/'_internal_1_3_1',dirs_exist_ok=True)
shutil.copy2(built/'Pico Song Patcher 1.3.1.exe',target/'Pico Song Patcher 1.3.1.exe')
(target/'training_project.txt').write_text(str(ROOT),encoding='utf-8')
