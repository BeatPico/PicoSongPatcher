import base64,copy,gzip,hashlib,io,json,os,re,shutil,struct,subprocess,tempfile,wave,zipfile,zlib
from datetime import datetime
from pathlib import Path
import UnityPy
from UnityPy.streams import EndianBinaryReader
from PIL import Image
from maps import PatcherError,require,audio_metadata,steady_lights
from library import inspect_map,resolve_file,RANK

BASE_HASH='13fc1e957231820bed4c4189be36ccbd9083404594d3fce175b00443da09f0f5'
OBB='main.1716.com.beatgames.beatsaber.obb'
MENU_HASH='12bdc0fa893016f37e65b5c353040c02'

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(4*1024*1024),b''):h.update(data)
    return h.hexdigest()
def encoded(x):return json.dumps(x,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def ptr(x):return {'m_FileID':0,'m_PathID':x}
def text(obj,value):
    t=obj.parse_as_dict();t['m_Script']=gzip.compress(encoded(value),mtime=0).decode('utf-8','surrogateescape');obj.patch(t)
def decode_text(obj):
    raw=obj.parse_as_dict()['m_Script'].encode('utf-8','surrogateescape')
    return json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
def clone(obj,label):
    new=copy.copy(obj);new.path_id=int.from_bytes(hashlib.sha256(label.encode()).digest()[:8],'little')&0x7fffffffffffffff
    require(new.path_id not in obj.assets_file.objects,'Конфликт идентификаторов. Убери повторяющуюся карту.')
    new.data=obj.get_raw_data();obj.assets_file.objects[new.path_id]=new;obj.assets_file.mark_changed();return new
def replace_resource(bundle,name,data):
    reader=EndianBinaryReader(data);reader.flags=bundle.files[name].flags;bundle.files[name]=reader
def bank(wav):
    with wave.open(str(wav),'rb') as w:
        require(w.getnchannels()==2 and w.getframerate()==44100 and w.getsampwidth()==2,'Неверный формат преобразованного аудио.')
        frames=w.getnframes();pcm=w.readframes(frames)
    sample=struct.pack('<Q',(frames<<34)|(1<<5)|(8<<1))+b'\0'*28
    payload=pcm+b'\0'*((-len(pcm))%32)
    header=struct.pack('<4s6I8s16s8s',b'FSB5',1,1,len(sample),0,len(payload),2,b'\x01'+b'\0'*7,hashlib.md5(pcm).digest(),b'\0'*8)
    return header+sample+payload,frames,hashlib.sha256(pcm).hexdigest()
def run_ffmpeg(ffmpeg,args):
    result=subprocess.run([str(ffmpeg),'-hide_banner','-nostdin','-nostats',*map(str,args)],
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    require(result.returncode==0,'Ошибка преобразования музыки:\n'+result.stderr.decode('utf-8',errors='replace')[-1800:])
    return result.stderr.decode('utf-8',errors='replace')
def check_pointers(env):
    for sf in env.file.files.values():
        if not hasattr(sf,'objects'):continue
        def visit(v):
            if isinstance(v,dict):
                if 'm_FileID' in v and 'm_PathID' in v:
                    require(v['m_FileID']!=0 or v['m_PathID']==0 or v['m_PathID'] in sf.objects,'Повреждена ссылка между ресурсами.')
                    require(0<=v['m_FileID']<=len(sf.externals),'Неверная внешняя ссылка.')
                for x in v.values():visit(x)
            elif isinstance(v,(list,tuple)):
                for x in v:visit(x)
        for obj in sf.objects.values():visit(obj.parse_as_dict())

def gameplay(template,parsed,fsb,frames,level_id):
    env=UnityPy.load(str(template));objs=list(env.objects)
    so=next(o for o in objs if o.type.name=='MonoBehaviour');t=so.parse_as_dict()
    example=t['_difficultyBeatmapSets'][0]['_difficultyBeatmaps'][0]
    source_light=next(o for o in objs if o.path_id==example['_lightshowAsset']['m_PathID'])
    source_chart=next(o for o in objs if o.path_id==example['_beatmapAsset']['m_PathID'])
    chart_ids={};light_ids={};groups={}
    for name,config,chart,light in parsed['difficulties']:
        mode=config.get('_characteristic','Standard');label=mode+'-'+name
        group=groups.setdefault(mode,{'_beatmapCharacteristicSerializedName':mode,'_difficultyBeatmaps':[]})
        nc=clone(source_chart,level_id+'-'+label+'-chart');nl=clone(source_light,level_id+'-'+label+'-light')
        for obj,suffix in [(nc,'.beatmap.gz'),(nl,'.lightshow.gz')]:
            nt=obj.parse_as_dict();nt['m_Name']=label+suffix;obj.patch(nt)
        group['_difficultyBeatmaps'].append({'_difficulty':RANK[name],'_beatmapAsset':ptr(nc.path_id),'_lightshowAsset':ptr(nl.path_id)})
        chart_ids[nc.path_id]=chart;light_ids[nl.path_id]=light
    t['_difficultyBeatmapSets']=list(groups.values());t['m_Name']=level_id+'BeatmapLevelData';so.patch(t)
    keep={1,so.path_id,t['_audioClip']['m_PathID'],t['_audioDataAsset']['m_PathID'],t['m_Script']['m_PathID'],*chart_ids,*light_ids}
    metadata=audio_metadata(parsed['info']['_beatsPerMinute'],parsed['events'],frames,parsed.get('audio_data'))
    cab=next(k for k,v in env.file.files.items() if hasattr(v,'objects'))
    newcab='CAB-'+hashlib.md5((level_id+'-gameplay').encode()).hexdigest()
    for o in list(env.objects):
        if o.path_id not in keep:del o.assets_file.objects[o.path_id];continue
        x=o.parse_as_dict()
        if o.type.name=='AudioClip':
            x.update(m_Name=parsed['info']['_songName'],m_Length=frames/44100,m_Channels=2,m_Frequency=44100,
                     m_BitsPerSample=16,m_CompressionFormat=0,
                     m_Resource={'m_Source':f'archive:/{newcab}/{newcab}.resource','m_Offset':0,'m_Size':len(fsb)})
            o.patch(x)
        elif o.type.name=='TextAsset':
            if o.path_id in chart_ids:text(o,chart_ids[o.path_id])
            elif o.path_id in light_ids:text(o,light_ids[o.path_id])
            else:text(o,metadata)
        elif o.type.name=='AssetBundle':
            ids=[p for p in x['m_PreloadTable'] if p['m_PathID'] in keep]
            have={p['m_PathID'] for p in ids};ids += [ptr(p) for p in [*chart_ids,*light_ids] if p not in have]
            x.update(m_Name=level_id.lower(),m_AssetBundleName=level_id.lower(),m_PreloadTable=ids,
                m_Container=[[f'packages/com.beatgames.beatsaber.packs.ost1/so/{level_id.lower()}/{level_id.lower()}beatmapleveldata.asset',
                              {'preloadIndex':0,'preloadSize':len(ids),'asset':ptr(so.path_id)}]])
            o.patch(x)
    renamed={}
    for name,f in env.file.files.items():
        newname=name.replace(cab,newcab)
        if name.endswith('.resource'):
            r=EndianBinaryReader(fsb);r.flags=f.flags;f=r
        if hasattr(f,'name'):f.name=newname
        renamed[newname]=f
    env.file.files=renamed
    data=env.file.save(packer='original');check=UnityPy.load(data);check_pointers(check)
    for o in check.objects:
        if o.path_id in chart_ids:require(decode_text(o)==chart_ids[o.path_id],'Карта изменилась при сохранении.')
        if o.path_id in light_ids:require(decode_text(o)==light_ids[o.path_id],'Свет изменился при сохранении.')
    return data,metadata

class Menu:
    def __init__(self,raw):
        self.env=UnityPy.load(raw);self.objects=list(self.env.objects)
        self.original={o.path_id:o.get_raw_data() for o in self.objects};self.new_ids=[]
        self.collection=self.find('OstVol1BeatmapLevelCollection');self.levels=self.collection.parse_as_dict()
        self.loudness=self.find('OST1_PerceivedLoudnessPerLevel');self.loud=self.loudness.parse_as_dict()
        self.bundle=next(o for o in self.objects if o.type.name=='AssetBundle')
    def find(self,name):return next(o for o in self.objects if o.peek_name()==name)
    def add(self,parsed,level_id,fsb,frames,duration,lufs):
        info=parsed['info'];level=clone(self.find('BeatSaberBeatmapLevel'),level_id+'-menu')
        preview=clone(self.find('BeatSaberPreview'),level_id+'-preview')
        sprite=clone(next(o for o in self.objects if o.type.name=='Sprite'),level_id+'-sprite')
        texture=clone(next(o for o in self.objects if o.type.name=='Texture2D'),level_id+'-texture')
        self.new_ids += [o.path_id for o in [level,preview,sprite,texture]]
        t=preview.parse_as_dict();name=t['m_Resource']['m_Source'].rsplit('/',1)[-1]
        old=self.env.file.files[name].bytes;offset=(len(old)+31)//32*32
        replace_resource(self.env.file,name,old+b'\0'*(offset-len(old))+fsb)
        t.update(m_Name=level_id+'Preview',m_Length=frames/44100,m_CompressionFormat=0,m_Frequency=44100,m_Channels=2,m_BitsPerSample=16)
        t['m_Resource'].update(m_Offset=offset,m_Size=len(fsb));preview.patch(t)
        cover=Image.open(resolve_file(parsed['folder'],info['_coverImageFilename'])).convert('RGBA').resize((1024,1024),Image.Resampling.LANCZOS)
        tx=texture.parse_as_object();tx.m_Name=level_id+'Cover';tx.set_image(cover,target_format=4,mipmap_count=1);tx.save()
        t=sprite.parse_as_dict();t['m_Name']=level_id+'Cover';t['m_RD']['texture']=ptr(texture.path_id)
        guid=hashlib.md5((level_id+'-cover').encode()).digest()
        t['m_RenderDataKey']=({f'data[{i}]':struct.unpack_from('<I',guid,i*4)[0] for i in range(4)},t['m_RenderDataKey'][1]);sprite.patch(t)
        t=level.parse_as_dict();t.update(m_Name=level_id+'BeatmapLevel',_levelID=level_id)
        for k in ['_songName','_songSubName','_songAuthorName','_levelAuthorName','_beatsPerMinute','_songTimeOffset','_shuffle','_shufflePeriod']:
            t[k]=info.get(k,0 if k.startswith(('_songTime','_shuffle')) else '')
        t.update(_songDuration=duration,_integratedLufs=lufs,_previewStartTime=info.get('_previewStartTime',0),
                 _previewDuration=frames/44100,_previewAudioClip=ptr(preview.path_id),_coverImage=ptr(sprite.path_id),
                 _environmentName={'_environmentName':info.get('_environmentName','DefaultEnvironment')},
                 _environmentNames=[{'_environmentName':n} for n in info.get('_environmentNames',[info.get('_environmentName','DefaultEnvironment')])],_colorSchemes=[])
        mode_ids={'Standard':-7286399427822119286,'OneSaber':-5623662769225589684,'NoArrows':-8583864861369561029}
        templates={m:next(g for g in t['_previewDifficultyBeatmapSets'] if g['_beatmapCharacteristic']['m_PathID']==pid) for m,pid in mode_ids.items()}
        groups={}
        for name,config,chart,light in parsed['difficulties']:
            mode=config.get('_characteristic','Standard')
            if mode not in groups:
                groups[mode]=copy.deepcopy(templates[mode]);groups[mode]['_previewDifficultyBeatmaps']=[]
            group=groups[mode];example=templates[mode]['_previewDifficultyBeatmaps'][0]
            d=copy.deepcopy(example);d.update(_difficulty=RANK[name],_noteJumpMovementSpeed=config.get('_noteJumpMovementSpeed',0),
                _noteJumpStartBeatOffset=config.get('_noteJumpStartBeatOffset',0),_notesCount=len(chart['colorNotes']),
                _environmentNameIdx=config.get('_environmentNameIdx',0),_beatmapColorSchemeIdx=0,
                _bombsCount=len(chart['bombNotes']),_obstaclesCount=len(chart['obstacles']),
                _cuttableBeatmapObjectsCount=len(chart['colorNotes'])+sum(max(0,chart['chainsData'][c.get('ci',0)].get('c',0)-1) for c in chart['chains']))
            group['_previewDifficultyBeatmaps'].append(d)
        t['_previewDifficultyBeatmapSets']=list(groups.values());level.patch(t)
        self.levels['_beatmapLevels'].append(ptr(level.path_id))
        self.loud['_perceivedLoudnessPerLevel'].append({'_levelId':level_id,'_perceivedLoudness':lufs,'_checkSum':''})
    def save(self):
        self.collection.patch(self.levels);self.loudness.patch(self.loud)
        t=self.bundle.parse_as_dict();definition=next(m for p,m in t['m_Container'] if p.endswith('OstVol1_PackDefinition.asset'))
        index=definition['preloadIndex']+definition['preloadSize']
        t['m_PreloadTable'][index:index]=[ptr(x) for x in self.new_ids]
        for p,m in t['m_Container']:
            if m is definition:m['preloadSize']+=len(self.new_ids)
            elif m['preloadIndex']>=index:m['preloadIndex']+=len(self.new_ids)
        self.bundle.patch(t);data=self.env.file.save(packer='original')
        check=UnityPy.load(data);check_pointers(check)
        changed={self.collection.path_id,self.loudness.path_id,self.bundle.path_id}
        for o in check.objects:
            if o.path_id in self.original and o.path_id not in changed:
                require(o.get_raw_data()==self.original[o.path_id],'Изменилась исходная песня в меню.')
        require(len(check.objects)==len(self.original)+len(self.new_ids),'Ошибка числа объектов меню.')
        crc=0
        for f in check.file.files.values():crc=zlib.crc32(f.reader.bytes if hasattr(f,'reader') else f.bytes,crc)
        return data,crc

def parse_extra(b):
    p=0;out=[]
    while p<len(b):
        start=p;require(b[p]==7,'Неизвестный формат каталога.');p+=1
        n=b[p];p+=1+n;n=b[p];p+=1+n;lp=p
        n=struct.unpack_from('<I',b,p)[0];p+=4;t=json.loads(b[p:p+n].decode('utf-16-le'));p+=n
        out.append((start,lp,p,t))
    return out
def patch_catalog(catalog,menu,crc):
    old=base64.b64decode(catalog['m_ExtraDataString']);new=bytearray();offsets={};matches=0
    for start,lp,end,t in parse_extra(old):
        offsets[start]=len(new)
        if t.get('m_Hash')==MENU_HASH:
            t.update(m_Crc=crc,m_BundleSize=len(menu),m_Hash=hashlib.md5(menu).hexdigest())
            raw=json.dumps(t,separators=(',',':')).encode('utf-16-le');new+=old[start:lp]+struct.pack('<I',len(raw))+raw;matches+=1
        else:new+=old[start:end]
    require(matches==1,'Не найден исходный каталог OST Vol. 1.')
    entries=bytearray(base64.b64decode(catalog['m_EntryDataString']));count=struct.unpack_from('<i',entries,0)[0]
    require(len(entries)==4+28*count,'Неизвестный формат записей каталога.')
    for i in range(count):
        pos=4+i*28+16;index=struct.unpack_from('<i',entries,pos)[0]
        if index>=0:struct.pack_into('<i',entries,pos,offsets[index])
    after={x[0]:x[3] for x in parse_extra(new)}
    for start,_,_,value in parse_extra(old):
        if value.get('m_Hash')!=MENU_HASH:require(after[offsets[start]]==value,'Изменились посторонние записи каталога.')
    c=copy.deepcopy(catalog);c.update(m_ExtraDataString=base64.b64encode(new).decode(),m_EntryDataString=base64.b64encode(entries).decode())
    return encoded(c)

def build(base,entries,library_root,output_root,assets,log=print):
    base=Path(base).resolve();output_root=Path(output_root).resolve();assets=Path(assets)
    require(base.is_file(),'Выбери исходный OBB-кэш игры.')
    log('Проверка исходного кэша…')
    require(digest(base)==BASE_HASH,'Нужен исходный main.1716.com.beatgames.beatsaber.obb из комплекта 1.40.8 / OVRport 3.2.2.\nВыбран другой или уже изменённый кэш.')
    require(entries,'Добавь хотя бы одну песню и включи её в сборку.')
    require(len({e['level_id'] for e in entries})==len(entries),'Повторяющийся ID песни.')
    ffmpeg=assets/'tools'/'ffmpeg.exe';require(ffmpeg.is_file(),'Не найден tools/ffmpeg.exe рядом с программой.')
    output_root.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')
    stage=output_root/('.building_'+stamp);stage.mkdir()
    final=output_root/('PicoSongs_'+stamp)
    dest=stage/'Android'/'obb'/'com.beatgames.beatsaber';dest.mkdir(parents=True)
    reports=[]
    try:
        with tempfile.TemporaryDirectory(prefix='work_',dir=stage) as temp,zipfile.ZipFile(base) as src:
            work=Path(temp);menu_name=next(n for n in src.namelist() if '/ostvol1_pack_assets_all' in n)
            menu=Menu(src.read(menu_name));gameplay_files={}
            for i,entry in enumerate(entries,1):
                log(f'Песня {i}/{len(entries)}: {entry["title"]}')
                folder=Path(library_root)/entry['folder'];parsed=inspect_map(folder,entry.get('compatible',False))
                require(parsed['hash']==entry['hash'],'Файлы в библиотеке изменились. Добавь карту заново.')
                supported=json.loads((assets/'environments.json').read_text(encoding='utf-8'))
                envs=parsed['info'].get('_environmentNames') or [parsed['info'].get('_environmentName','DefaultEnvironment')]
                missing={i for i,n in enumerate(envs) if n not in supported}
                if missing:
                    parsed['warnings'].append('Арена '+', '.join(envs[i] for i in missing)+' отсутствует в игре: заменена на Default с простым светом.')
                    for i in missing:envs[i]='DefaultEnvironment'
                    parsed['difficulties']=[(n,c,m,steady_lights() if c.get('_environmentNameIdx',0) in missing else l) for n,c,m,l in parsed['difficulties']]
                parsed['info']['_environmentNames']=envs;parsed['info']['_environmentName']=envs[0]
                for warning in parsed['warnings']:log('  '+warning)
                wav=work/f'{i}.wav';info=parsed['info']
                run_ffmpeg(ffmpeg,['-i',resolve_file(folder,info['_songFilename']),'-ac','2','-ar','44100','-c:a','pcm_s16le',wav,'-y'])
                fsb,frames,pcm_hash=bank(wav)
                stderr=run_ffmpeg(ffmpeg,['-i',wav,'-af','ebur128','-f','null','NUL'])
                match=re.search(r'Integrated loudness:\s+I:\s+([-\d.]+) LUFS',stderr)
                lufs=float(match.group(1)) if match else -8.
                preview=work/f'{i}_preview.wav'
                with wave.open(str(wav),'rb') as r:
                    start=max(0,min(round(info.get('_previewStartTime',0)*44100),max(0,frames-44100)))
                    r.setpos(start);pcm=r.readframes(max(1,round(min(30,max(1,info.get('_previewDuration',10)))*44100)))
                with wave.open(str(preview),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(44100);w.writeframes(pcm)
                preview_fsb,preview_frames,_=bank(preview)
                level_id=entry['level_id'];data,metadata=gameplay(assets/'gameplay_template.bundle',parsed,fsb,frames,level_id)
                # Exercise the saved game's audio container through FMOD.
                check=UnityPy.load(data);clip=next(o.parse_as_object() for o in check.objects if o.type.name=='AudioClip')
                with wave.open(io.BytesIO(next(iter(clip.samples.values()))),'rb') as w:
                    require(w.getnframes()==frames and hashlib.sha256(w.readframes(frames)).hexdigest()==pcm_hash,'Не прошла проверка звука.')
                path=work/f'{level_id}.bundle';path.write_bytes(data)
                gameplay_files['assets/BeatmapLevelsData/'+level_id.lower()]=path
                menu.add(parsed,level_id,preview_fsb,preview_frames,frames/44100,lufs)
                reports.append({'id':level_id,'title':info['_songName'],'artist':info['_songAuthorName'],
                    'duration':frames/44100,'audio_verified':True,'difficulties':[n for n,*_ in parsed['difficulties']],
                    'notes':{cfg.get('_characteristic','Standard')+'/'+n:len(c['colorNotes']) for n,cfg,c,_ in parsed['difficulties']},'bpm_regions':len(metadata['bpmData']),
                    'warnings':parsed['warnings'],'autogenerated':entry.get('autogenerated',False)})
                del data,check,clip,fsb
            log('Сохранение списка песен и каталога…');menu_data,crc=menu.save()
            catalog=patch_catalog(json.loads(src.read('assets/aa/catalog.json')),menu_data,crc)
            replacements={menu_name:menu_data,'assets/aa/catalog.json':catalog}
            log('Сборка кэша — это может занять несколько минут…')
            with zipfile.ZipFile(dest/OBB,'w',allowZip64=True) as dst:
                for item in src.infolist():
                    target=copy.copy(item);target._compresslevel=1
                    if item.filename in replacements:dst.writestr(target,replacements[item.filename])
                    else:
                        with src.open(item) as r,dst.open(target,'w') as w:shutil.copyfileobj(r,w,4*1024*1024)
                for name,path in gameplay_files.items():dst.write(path,name,compress_type=zipfile.ZIP_STORED)
            log('Проверка готового кэша…')
            with zipfile.ZipFile(dest/OBB) as result:
                require(result.testzip() is None,'Повреждён итоговый ZIP-кэш.')
                require(result.namelist()==src.namelist()+list(gameplay_files),'Потеряны файлы исходного кэша.')
                for item in src.infolist():
                    if item.filename not in replacements:
                        after=result.getinfo(item.filename)
                        require((after.CRC,after.file_size)==(item.CRC,item.file_size),'Изменился исходный файл '+item.filename)
                    else:require(result.read(item.filename)==replacements[item.filename],'Неверно сохранён каталог.')
            # Restore the earlier manual Espresso replacement when copying this output.
            shutil.copy2(assets/'original_espresso',dest/'espresso')
        from cache_view import inspect_cache,text_report
        inventory=inspect_cache(dest/OBB)
        require({s['id'] for s in inventory['songs'] if s['custom']}=={e['level_id'] for e in entries},'Список песен в готовом OBB отличается от выбранного.')
        # The completed folder is renamed below; persist its final location.
        inventory['path']=str(final/'Android/obb/com.beatgames.beatsaber'/OBB)
        (stage/'cache_inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
        (stage/'ПЕСНИ_В_КЭШЕ.txt').write_text(text_report(inventory),encoding='utf-8')
        report={'app_version':'1.3.1','base_sha256':BASE_HASH,'output_sha256':digest(dest/OBB),
                'songs':reports,'ost1_song_count':10+len(reports),'source_files_written':False,'device_written':False,
                'local_checks_passed':True,'new_charts_require_headset_playtest':True}
        (stage/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        instructions='''Готовая сборка для Beat Saber 1.40.8 / OVRport 3.2.2 на Pico 4.

1. Закрой игру на очках.
2. Сохрани с очков текущие main.1716.com.beatgames.beatsaber.obb и espresso.
3. Перенеси два файла из Android/obb/com.beatgames.beatsaber этой папки
   в такую же папку во внутреннем хранилище Pico с заменой.
4. Дождись окончания копирования, запусти игру и открой OST Vol. 1.
5. Новые песни добавлены в этот набор. Сначала проверь их в режиме тренировки.

APK не меняется. На устройстве программа сама ничего не устанавливает.
Сборка включает весь выбранный список библиотеки; оригинальные песни сохранены.
Файл espresso возвращает исходную Espresso после раннего эксперимента с подменой.
Для возврата назад закрой игру и верни два файла, сохранённых на шаге 2.
Проверки звука, ссылок и архива выполнены на ПК. Новые карты проверь в очках.
'''
        (stage/'КАК_УСТАНОВИТЬ.txt').write_text(instructions,encoding='utf-8')
        stage.rename(final);log('Готово: '+str(final));return final
    except Exception:
        # Keep the incomplete output identifiable; never present it as ready.
        (stage/'СБОРКА_НЕ_ГОТОВА.txt').write_text('Сборка прервана. Не переносите эти файлы на очки.',encoding='utf-8')
        raise
