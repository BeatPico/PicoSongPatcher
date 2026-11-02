"""Reproducible local preparation; not needed by the portable EXE."""
import sys,shutil,json,gzip,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LAB=ROOT.parent/'beat_saber_lab'
sys.path.insert(0,str(LAB/'python_packages'))
import UnityPy
from engine import replace_resource,bank,text,OBB
from library import Library

assets=ROOT/'assets';(assets/'tools').mkdir(parents=True,exist_ok=True)
source=Path(r'C:\Program Files\Virtual Desktop Streamer')
for name in ['ffmpeg.exe','avcodec-61.dll','avdevice-61.dll','avfilter-10.dll','avformat-61.dll',
             'avutil-59.dll','swresample-5.dll','swscale-8.dll']:
    shutil.copy2(source/name,assets/'tools'/name)
shutil.copy2(LAB/'input'/'espresso',assets/'original_espresso')
silence=assets/'silence.wav'
with wave.open(str(silence),'wb') as w:
    w.setnchannels(2);w.setsampwidth(2);w.setframerate(44100);w.writeframes(b'\0'*44100*4)
fsb,frames,_=bank(silence)
env=UnityPy.load(str(LAB/'input'/'espresso'))
for obj in env.objects:
    t=obj.parse_as_dict()
    if obj.type.name=='AudioClip':
        t.update(m_Length=1.,m_CompressionFormat=0,m_Name='Template')
        t['m_Resource']['m_Size']=len(fsb);obj.patch(t)
    elif obj.type.name=='TextAsset':
        name=t['m_Name']
        if '.audio' in name:value={'version':'4.0.0','songSampleCount':44100,'songFrequency':44100,'bpmData':[]}
        elif '.lightshow' in name:value={'version':'4.0.0'}
        else:value={'version':'4.1.0','colorNotes':[],'bombNotes':[],'obstacles':[]}
        text(obj,value)
for name in list(env.file.files):
    if name.endswith('.resource'):replace_resource(env.file,name,fsb)
(assets/'gameplay_template.bundle').write_bytes(env.file.save(packer='original'))
lib=Library(ROOT/'data')
lib.import_path(Path(r'C:\Users\home\Downloads\2b59 (Billie Jean - petrofsilvers)'),level_id='BillieJean2b59')
lib.state['base_obb']=r'C:\Users\home\Downloads\PICO_Beat_Saber_-MODDED-_1.40.8_OVRport_3.2.2\com.beatgames.beatsaber\main.1716.com.beatgames.beatsaber.obb'
lib.save()
print('Prepared template:',(assets/'gameplay_template.bundle').stat().st_size,'bytes')
