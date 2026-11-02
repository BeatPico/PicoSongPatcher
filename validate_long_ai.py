import sys,json,subprocess,os,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT.parent/'beat_saber_lab/python_packages'),str(ROOT)]
from engine import run_ffmpeg
work=ROOT/'validation_v12/long';work.mkdir(parents=True,exist_ok=True)
ffmpeg=ROOT/'assets/tools/ffmpeg.exe'
run_ffmpeg(ffmpeg,['-stream_loop','1','-i',r'C:\Users\home\Downloads\Alexandra_Stan_-_Mr_Saxobeat.mp3',
    '-ac','1','-ar','44100','-c:a','pcm_s16le',work/'input.wav','-y'])
with wave.open(str(work/'input.wav'),'rb') as w:duration=w.getnframes()/w.getframerate()
assert duration>360
job={'audio':str(work/'input.wav'),'ffmpeg':str(ffmpeg),'output':str(work/'charts'),
     'difficulties':{'Normal':2.5},'bpm':None,'walls':False,'seed':42}
(work/'job.json').write_text(json.dumps(job),encoding='utf-8')
env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
result=subprocess.run([str(ROOT/'ai_engine/runtime/python.exe'),str(ROOT/'ai_engine/worker.py'),str(work/'job.json')],env=env)
assert result.returncode==0
report=json.loads((work/'charts/analysis.json').read_text(encoding='utf-8'))
chart=json.loads((work/'charts/Normal.dat').read_text(encoding='utf-8'))
assert report['chunks']==3
times=[n['b'] for n in chart['colorNotes']]
assert min(times)<15 and max(times)>duration-15
assert all(a<=b for a,b in zip(times,times[1:]))
for seam in (180,360):
    assert any(seam-12<t<seam for t in times)
    assert any(seam<t<seam+12 for t in times)
(work/'result.json').write_text(json.dumps({'status':'PASS','duration':duration,'chunks':report['chunks'],
    'notes':len(times),'last_note':max(times),'seams_verified':[180,360]},indent=2),encoding='utf-8')
print('LONG AUDIO VALIDATION PASS',flush=True)
