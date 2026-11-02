"""Build the separate 1.4 executable; user library/engine are packaged separately."""
import sys
import shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LAB=ROOT.parent/'beat_saber_lab'
sys.path[:0]=[str(LAB/'build_packages'),str(LAB/'python_packages')]
import PyInstaller.__main__
PyInstaller.__main__.run([
    str(ROOT/'app_v14.py'),'--noconfirm','--windowed','--onedir',
    '--name','Pico Song Patcher 1.4','--distpath',str(ROOT/'build_v14/distribution'),
    '--contents-directory','_internal','--workpath',str(ROOT/'build_v14'),'--specpath',str(ROOT),
    '--paths',str(LAB/'python_packages'),'--paths',str(ROOT),
    '--collect-all','UnityPy','--collect-all','fmod_toolkit','--collect-all','PIL',
    '--collect-all','archspec','--collect-all','tpk_ar','--collect-all','numpy',
    '--add-data',str(ROOT/'assets')+';assets'])
built=ROOT/'build_v14/distribution/Pico Song Patcher 1.4'
target=ROOT/'dist_v4/Pico Song Patcher 1.4';target.mkdir(parents=True,exist_ok=True)
shutil.copytree(built/'_internal',target/'_internal',dirs_exist_ok=True)
shutil.copy2(built/'Pico Song Patcher 1.4.exe',target/'Pico Song Patcher 1.4.exe')
(target/'training_project.txt').write_text(str(ROOT),encoding='utf-8')
