"""Local onset/tempo analysis and conservative two-saber draft mapping."""
import hashlib,json,math,re,shutil,tempfile,wave
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from maps import PatcherError,require
from engine import run_ffmpeg

ALGORITHM='rhythm-draft-1.1.0'
SR=22050
HOP=220

def analyse(samples,sample_rate=SR,bpm_override=None):
    x=np.asarray(samples,dtype=np.float32)
    require(len(x)>=sample_rate*8,'Для автоматической карты нужно хотя бы 8 секунд музыки.')
    require(float(np.max(np.abs(x)))>0.001,'Аудиофайл содержит тишину.')
    size=1024;hop=round(sample_rate/100);count=1+(len(x)-size)//hop
    flux=np.zeros(count);energy=np.zeros(count);previous=np.zeros(size//2+1);window=np.hanning(size)
    for start in range(0,count,512):
        n=min(512,count-start)
        frames=np.lib.stride_tricks.sliding_window_view(x[start*hop:(start+n-1)*hop+size],size)[::hop]
        spec=np.log1p(np.abs(np.fft.rfft(frames*window,axis=1))*10)
        # Ignore near-DC rumble. Positive spectral change marks attacks.
        spec[:,:2]=0;prev=np.vstack([previous,spec[:-1]])
        flux[start:start+n]=np.maximum(spec-prev,0).sum(axis=1)
        energy[start:start+n]=np.sqrt(np.mean(frames*frames,axis=1));previous=spec[-1]
    dt=hop/sample_rate
    local=np.convolve(flux,np.ones(101)/101,mode='same')
    onset=np.maximum(flux-local*0.7,0)
    active=energy>max(float(np.max(energy))*0.025,0.001)
    onset[~active]=0
    require(float(onset.max())>0,'Не удалось выделить ритм в аудио.')
    onset/=onset.max()
    peaks=np.flatnonzero((onset[1:-1]>=onset[:-2])&(onset[1:-1]>onset[2:]))+1
    floor=max(0.025,float(np.quantile(onset[onset>0],0.45)))
    peaks=[int(i) for i in peaks if onset[i]>=floor]
    selected=[];blocked=np.zeros(count,dtype=bool);radius=max(1,round(0.085/dt))
    for i in sorted(peaks,key=lambda i:-onset[i]):
        if not blocked[i]:
            selected.append(i);blocked[max(0,i-radius):min(count,i+radius+1)]=True
    peaks=np.array(sorted(selected),dtype=int)
    require(len(peaks)>=6,'Слишком мало ритмических акцентов. Попробуй другую запись.')
    candidates=np.arange(75,180.01,0.25)
    # Fractional-lag autocorrelation prevents sample-bin tempo bias.
    lags=np.arange(max(1,round(60/200/dt)),round(60/35/dt)+2)
    ac=np.array([np.dot(onset[k:],onset[:-k])/max(1,len(onset)-k) for k in lags])
    def correlation(b):return np.interp(60/b/dt,lags,ac)
    scores=np.array([correlation(b)+0.35*correlation(b/2)+0.1*correlation(b*2) for b in candidates])
    scores*=np.exp(-0.8*(np.log2(candidates/120))**2)
    bpm=float(candidates[np.argmax(scores)]) if bpm_override is None else float(bpm_override)
    require(math.isfinite(bpm) and 40<=bpm<=250,'BPM должен быть числом от 40 до 250.')
    times=peaks*dt+size/2/sample_rate;strengths=onset[peaks];period=60/bpm
    phase_candidates=np.linspace(0,period,120,endpoint=False)
    phase_scores=[]
    for p in phase_candidates:
        distance=np.abs((times-p+period/2)%period-period/2)
        phase_scores.append(float(np.sum(strengths*np.exp(-0.5*(distance/(period*0.10))**2))))
    phase=float(phase_candidates[int(np.argmax(phase_scores))])
    confidence=float(max(phase_scores)/max(1e-9,float(strengths.sum())))
    return {'bpm':bpm,'phase':phase,'duration':len(x)/sample_rate,'times':times,'strengths':strengths,
            'energy':energy,'dt':dt,'confidence':round(confidence,3)}

def draft_charts(a):
    bpm=a['bpm'];period=60/bpm;end=a['duration']-1.0
    times=a['times'];strengths=a['strengths'];charts={}
    for name,step,njs in [('Easy',2,10),('Normal',1,13),('Hard',0.5,16)]:
        notes=[];last=[-100.,-100.];strokes=[0,0];hand=0;used=set()
        for grid in np.arange(0,max(0,(end-a['phase'])/period),step):
            target=a['phase']+grid*period
            if target<1.6:continue
            close=np.flatnonzero(np.abs(times-target)<=min(0.12,period*step*0.35))
            if not len(close):continue
            k=int(close[np.argmax(strengths[close])]);t=float(times[k])
            if k in used or t>end or t<1.6:continue
            if t-last[hand]<0.27:continue
            used.add(k);hands=[hand]
            # Sparse, symmetric accents add variety without crossover patterns.
            if name=='Hard' and round(grid*2)%16==0 and strengths[k]>=0.28 and t-last[1-hand]>=0.34:hands=[0,1]
            for h in hands:
                stroke=strokes[h];down=stroke%2==0;phrase=int(grid//8)
                diagonal=phrase%3!=0
                direction=(7 if h==0 else 6) if down and diagonal else ((4 if h==0 else 5) if diagonal else 0)
                if down and not diagonal:direction=1
                notes.append({'b':round(t*bpm/60,6),'x':(0 if h==0 else 3) if down else (1 if h==0 else 2),
                              'y':1 if down else 0,'c':h,'d':direction,'a':0})
                strokes[h]+=1;last[h]=t
            hand=1-hand
        require(len(notes)>=4,f'Не удалось создать достаточно блоков для {name}. Укажи BPM вручную или выбери ритмичную запись.')
        lights=[{'b':float(b),'et':i%5,'i':1+(i%2)*4,'f':0.7} for i,b in enumerate(np.arange(0,end*bpm/60,4))]
        charts[name]={'version':'3.3.0','bpmEvents':[],'colorNotes':sorted(notes,key=lambda n:(n['b'],n['c'])),
            'bombNotes':[],'obstacles':[],'sliders':[],'burstSliders':[],
            'basicBeatmapEvents':lights,'colorBoostBeatmapEvents':[],'useNormalEventsAsCompatibleEvents':True}
    return charts

def generate(source,library,assets,bpm_override=None,log=print):
    source=Path(source);require(source.is_file(),'Аудиофайл не найден.')
    require(source.suffix.lower() in ('.mp3','.wav','.flac','.m4a','.ogg','.egg'),'Выбери MP3, WAV, FLAC, M4A или OGG.')
    require(source.stat().st_size<500*1024*1024,'Аудиофайл больше 500 МБ.')
    ffmpeg=Path(assets)/'tools'/'ffmpeg.exe';key=hashlib.sha256()
    with source.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):key.update(block)
    key.update((ALGORITHM+str(bpm_override)).encode());key=key.hexdigest()[:24]
    temp_root=library.root/'tmp';temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='audio_',dir=temp_root) as td:
        folder=Path(td);wav=folder/'analysis.wav';log('Анализ ритма: '+source.name)
        stderr=run_ffmpeg(ffmpeg,['-i',source,'-t','1801','-vn','-ac','1','-ar',SR,'-c:a','pcm_s16le',wav,'-y'])
        with wave.open(str(wav),'rb') as w:
            require(w.getnframes()/SR<=1800,'Для автокарты выбери запись не длиннее 30 минут.')
            samples=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)/32768
        analysis=analyse(samples,SR,bpm_override);charts=draft_charts(analysis)
        log(f'Темп: {analysis["bpm"]:g} BPM · блоки: '+', '.join(n+' '+str(len(c['colorNotes'])) for n,c in charts.items()))
        # Encode a common standalone game-compatible audio file, preserving the source.
        run_ffmpeg(ffmpeg,['-i',source,'-vn','-ac','2','-ar','44100','-c:a','libvorbis','-q:a','5',folder/'song.ogg','-y'])
        title=source.stem;artist=''
        for tag in ('title','artist'):
            match=re.search(r'^\s*'+tag+r'\s*:\s*(.+)$',stderr,re.I|re.M)
            if match:
                if tag=='title':title=match.group(1).strip()
                else:artist=match.group(1).strip()
        if not artist and ' - ' in title:artist,title=title.split(' - ',1)
        image=Image.new('RGB',(512,512),'#172b44');draw=ImageDraw.Draw(image)
        for i in range(7):
            x=45+i*63;h=80+int(key[i*2:i*2+2],16)%170
            draw.rounded_rectangle((x,320-h,x+35,320),8,fill=('#ff5277' if i%2==0 else '#51dce3'))
        font_path=Path('C:/Windows/Fonts/segoeui.ttf');font=ImageFont.truetype(str(font_path),26) if font_path.exists() else ImageFont.load_default()
        draw.text((35,365),'AUTO MAP',font=font,fill='white');draw.text((35,415),title[:29],font=font,fill='#b9cddd');image.save(folder/'cover.jpg',quality=92)
        info={'_version':'2.1.0','_songName':title,'_songSubName':'(Auto map)','_songAuthorName':artist,
              '_levelAuthorName':'Pico Song Patcher Auto','_beatsPerMinute':analysis['bpm'],'_songTimeOffset':0,
              '_previewStartTime':min(30,analysis['duration']/3),'_previewDuration':12,
              '_songFilename':'song.ogg','_coverImageFilename':'cover.jpg','_environmentName':'DefaultEnvironment',
              '_difficultyBeatmapSets':[{'_beatmapCharacteristicName':'Standard','_difficultyBeatmaps':[
                  {'_difficulty':name,'_difficultyRank':rank,'_beatmapFilename':name+'.dat',
                   '_noteJumpMovementSpeed':speed,'_noteJumpStartBeatOffset':0}
                  for name,rank,speed in [('Easy',1,10),('Normal',3,13),('Hard',5,16)]]}]}
        (folder/'Info.dat').write_text(json.dumps(info,ensure_ascii=False),encoding='utf-8')
        for name,chart in charts.items():(folder/(name+'.dat')).write_text(json.dumps(chart,separators=(',',':')),encoding='utf-8')
        # Repeated import of identical audio/settings has the same level ID.
        level_id='PicoAuto'+key[:20]
        existing=next((e for e in library.state['songs'] if e['level_id']==level_id),None)
        if existing:
            existing['enabled']=True;library.save();return existing,False
        entry,added=library.import_path(folder,level_id=level_id)
        entry.update(autogenerated=True,auto_bpm=analysis['bpm'],auto_confidence=analysis['confidence'],
            warnings=['Автоматический черновик: ритм и удобство движений проверь в игре. При неверном темпе создай карту с BPM вручную.'],
            source_audio=source.name,auto_algorithm=ALGORITHM)
        library.save();return entry,added
