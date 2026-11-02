import sys,json,subprocess,os
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT.parent/'beat_saber_lab/python_packages'),str(ROOT)]
from engine import run_ffmpeg
work=ROOT/'validation_v12/smoke';work.mkdir(parents=True,exist_ok=True)
ffmpeg=ROOT/'assets/tools/ffmpeg.exe'
run_ffmpeg(ffmpeg,['-i',r'C:\Users\home\Downloads\Alexandra_Stan_-_Mr_Saxobeat.mp3',
    '-t','45','-ac','1','-ar','44100','-c:a','pcm_s16le',work/'input.wav','-y'])
job={'audio':str(work/'input.wav'),'ffmpeg':str(ffmpeg),'output':str(work/'charts'),
     'difficulties':{'Hard':4.},'bpm':None,'walls':False}
(work/'job.json').write_text(json.dumps(job),encoding='utf-8')
env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
result=subprocess.run([str(ROOT/'ai_engine/runtime/python.exe'),str(ROOT/'ai_engine/worker.py'),str(work/'job.json')],env=env)
sys.exit(result.returncode)
