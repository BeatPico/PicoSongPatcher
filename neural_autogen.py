"""Full-song learned mapping using a separate, portable CPU runtime."""
import hashlib,json,os,re,shutil,subprocess,sys,tempfile,traceback,wave
from datetime import datetime
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from maps import PatcherError,require
from engine import run_ffmpeg
from model_profiles import resolve_profile

ALGORITHM='infernosaber-v2-pico-1.2.0'
MODEL_REVISION='e0f4c8b53f4c52fc30c5b66fd7384c0ce85884ff'
DEFAULT_DIFFICULTIES={'Easy':1.5,'Normal':2.5,'Hard':4.0}

def engine_root():
    return (Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent)/'ai_engine'

def available():
    root=engine_root()
    return all((root/name).is_file() for name in ('runtime/python.exe','worker.py','models/v2_expert/bundle.json','models_lock.json'))

def selected_model(library):
    return resolve_profile(engine_root(),library.state.get('ai_model','base'))

def cover(path,title,key):
    picture=Image.new('RGB',(512,512),'#182737');draw=ImageDraw.Draw(picture)
    for i in range(12):
        x=36+i*38;h=50+int(key[(i*2)%len(key):(i*2)%len(key)+2],16)%200
        draw.rounded_rectangle((x,325-h,x+23,325),6,fill='#ff537e' if i%2==0 else '#45cfdb')
    font_path=Path('C:/Windows/Fonts/segoeui.ttf')
    font=ImageFont.truetype(str(font_path),25) if font_path.exists() else ImageFont.load_default()
    draw.text((35,367),'AI / FULL SONG',font=font,fill='white')
    draw.text((35,420),title[:30],font=font,fill='#c3d6dd');picture.save(path,quality=93)

def generate(source,library,assets,bpm_override=None,log=print,difficulties=None,replace_entry=None):
    source=Path(source).resolve();assets=Path(assets)
    require(source.is_file(),'Аудиофайл не найден.')
    require(source.suffix.lower() in ('.mp3','.wav','.flac','.m4a','.ogg','.egg','.aac','.wma','.opus'),
        'Выбери MP3, WAV, FLAC, M4A, OGG, AAC, WMA или OPUS.')
    require(available(),'Не найден полный AI-движок. Запусти версию 1.2 из её папки вместе с каталогом ai_engine.')
    difficulties=dict(difficulties or DEFAULT_DIFFICULTIES)
    require(difficulties and all(n in ['Easy','Normal','Hard','Expert','ExpertPlus'] and 0.5<=v<=10 for n,v in difficulties.items()),'Неверные сложности AI-карты.')
    if bpm_override is not None:require(40<=float(bpm_override)<=320,'BPM должен быть от 40 до 320.')
    fingerprint=hashlib.sha256()
    with source.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):fingerprint.update(block)
    source_hash=fingerprint.hexdigest()
    seed_fingerprint=fingerprint.copy()
    seed_fingerprint.update(json.dumps([ALGORITHM,MODEL_REVISION,bpm_override,difficulties],sort_keys=True).encode())
    generation_seed=int(seed_fingerprint.hexdigest()[:8],16)%2147483647
    profile=selected_model(library)
    revision=MODEL_REVISION if profile['id']=='base' else profile['revision']
    fingerprint.update(json.dumps([ALGORITHM,revision,bpm_override,difficulties],sort_keys=True).encode())
    key=fingerprint.hexdigest();level_id='PicoAutoAI'+key[:20]
    existing=next((e for e in library.state['songs'] if e['level_id']==level_id),None)
    if existing:
        existing['enabled']=True
        if replace_entry and replace_entry is not existing:replace_entry['enabled']=False
        library.save();return existing,False
    root=engine_root();ffmpeg=(assets/'tools/ffmpeg.exe').resolve()
    tmp=library.root/'tmp';tmp.mkdir(exist_ok=True)
    logs=library.root/'logs';logs.mkdir(exist_ok=True)
    log_path=logs/('ai_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.log')
    with tempfile.TemporaryDirectory(prefix='ai_full_',dir=tmp) as td:
        folder=Path(td).resolve();wav=folder/'analysis.wav';charts=folder/'charts'
        log('Подготовка полной записи: '+source.name)
        stderr=run_ffmpeg(ffmpeg,['-i',source,'-vn','-ac','1','-ar','44100','-c:a','pcm_s16le',wav,'-y'])
        with wave.open(str(wav),'rb') as audio:duration=audio.getnframes()/audio.getframerate()
        require(duration>=8,'Для AI-карты нужно хотя бы 8 секунд музыки.')
        log(f'Вся песня: {int(duration//60)}:{int(duration%60):02d}. Анализ на этом ПК…')
        job={'audio':str(wav),'ffmpeg':str(ffmpeg),'output':str(charts),'difficulties':difficulties,
             'bpm':bpm_override,'walls':False,'seed':generation_seed,
             'model_profile':profile['id'],'model_revision':revision}
        job_file=folder/'job.json';job_file.write_text(json.dumps(job),encoding='utf-8')
        env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8',HF_HUB_OFFLINE='1',CUDA_VISIBLE_DEVICES='-1')
        process=subprocess.Popen([str(root/'runtime/python.exe'),str(root/'worker.py'),str(job_file)],
            cwd=root/'infernosaber',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            encoding='utf-8',errors='replace',env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        tail=[]
        with log_path.open('w',encoding='utf-8') as output:
            for line in process.stdout:
                output.write(line);output.flush();tail=(tail+[line.strip()])[-16:]
                if line.startswith('@pico '):
                    try:log(json.loads(line[6:])['log'])
                    except (ValueError,KeyError):pass
        process.stdout.close();code=process.wait()
        require(code==0,'AI не завершил генерацию. Подробности: '+str(log_path)+'\n'+'\n'.join(tail[-5:]))
        report=json.loads((charts/'analysis.json').read_text(encoding='utf-8'))
        require(abs(report['audio_seconds']-duration)<0.03,'AI обработал не всю запись.')
        title=replace_entry['title'] if replace_entry else source.stem
        artist=replace_entry.get('artist','') if replace_entry else ''
        if not replace_entry:
            for tag in ('title','artist'):
                match=re.search(r'^\s*'+tag+r'\s*:\s*(.+)$',stderr,re.M|re.I)
                if match:
                    if tag=='title':title=match.group(1).strip()
                    else:artist=match.group(1).strip()
            if not artist and ' - ' in title:artist,title=title.split(' - ',1)
        # Keep the complete original music. No -t, trim, teaser or fade is used.
        run_ffmpeg(ffmpeg,['-i',source,'-vn','-ac','2','-ar','44100','-c:a','libvorbis','-q:a','6',folder/'song.ogg','-y'])
        cover(folder/'cover.jpg',title,key)
        ranks={'Easy':1,'Normal':3,'Hard':5,'Expert':7,'ExpertPlus':9}
        info={'_version':'2.1.0','_songName':title,'_songSubName':'(AI map)' if profile['id']=='base' else '(AI trained)',
            '_songAuthorName':artist,'_levelAuthorName':'InfernoSaber AI / Pico Song Patcher',
            '_beatsPerMinute':60.,'_songTimeOffset':0.,'_shuffle':0.,'_shufflePeriod':0.,
            '_previewStartTime':min(30,duration/3),'_previewDuration':12,
            '_songFilename':'song.ogg','_coverImageFilename':'cover.jpg','_environmentName':'DefaultEnvironment',
            '_difficultyBeatmapSets':[{'_beatmapCharacteristicName':'Standard','_difficultyBeatmaps':[
                {'_difficulty':name,'_difficultyRank':ranks[name],'_beatmapFilename':name+'.dat',
                 '_noteJumpMovementSpeed':min(20,10+difficulties[name]*1.5),'_noteJumpStartBeatOffset':0}
                for name in difficulties]}]}
        (folder/'Info.dat').write_text(json.dumps(info,ensure_ascii=False),encoding='utf-8')
        for name in difficulties:shutil.copy2(charts/(name+'.dat'),folder/(name+'.dat'))
        entry,added=library.import_path(folder,level_id=level_id)
        target=library.root/entry['folder']
        shutil.copy2(charts/'analysis.json',target/'AI_analysis.json')
        estimated=[r['estimated_bpm'] for r in report['parts']]
        entry.update(autogenerated=True,auto_algorithm=ALGORITHM,auto_backend='InfernoSaber v2',
            ai_model=profile['id'],ai_model_label=profile['label'],ai_model_revision=revision,
            auto_bpm=sum(estimated)/len(estimated),source_audio=(replace_entry or {}).get('source_audio',source.name),
            source_audio_sha256=source_hash,full_audio_seconds=duration,ai_difficulties=difficulties,
            analysis_file=str(Path(entry['folder'])/'AI_analysis.json'),
            warnings=['Карта создана обученной нейросетью. Удобство движений проверь в режиме тренировки.'])
        if replace_entry:
            replace_entry['enabled']=False;entry['replaces_level_id']=replace_entry['level_id']
        library.save();return entry,added
