import hashlib,json,shutil,zipfile,tempfile,os
from pathlib import Path
from maps import PatcherError,require,convert,steady_lights

RANK={'Easy':0,'Normal':1,'Hard':2,'Expert':3,'ExpertPlus':4}

def load_json(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (ValueError,UnicodeError) as e:raise PatcherError(f'Не удалось прочитать {Path(path).name}: {e}') from e

def resolve_file(root,name):
    path=Path(str(name).replace('\\','/'))
    require(not path.is_absolute() and not any(p in ('..','') for p in path.parts),'Недопустимый путь в карте.')
    current=Path(root).resolve()
    for part in path.parts:
        matches=[p for p in current.iterdir() if p.name.casefold()==part.casefold()]
        require(len(matches)==1,f'Не найден файл карты: {name}')
        current=matches[0].resolve()
        require(current.is_relative_to(Path(root).resolve()),'Файл карты находится за пределами папки.')
    require(current.is_file(),f'Не найден файл: {name}')
    return current

def inspect_map(folder,compatible=False):
    folder=Path(folder)
    info_path=resolve_file(folder,'info.dat')
    raw_info=load_json(info_path);warnings=[];audio_data=None
    version=str(raw_info.get('_version',raw_info.get('version','')))
    if version.startswith('4.'):
        audio=raw_info['audio'];song=raw_info['song'];refs=raw_info.get('difficultyBeatmaps',[])
        authors=list(dict.fromkeys(a for d in refs for a in d.get('beatmapAuthors',{}).get('mappers',[])))
        info={'_version':'2.1.0','_songName':song['title'],'_songSubName':song.get('subTitle',''),
              '_songAuthorName':song.get('author',''),'_levelAuthorName':', '.join(authors),
              '_beatsPerMinute':audio['bpm'],'_songTimeOffset':0,'_shuffle':0,'_shufflePeriod':0,
              '_previewStartTime':audio.get('previewStartTime',0),'_previewDuration':audio.get('previewDuration',10),
              '_songFilename':audio['songFilename'],'_coverImageFilename':raw_info['coverImageFilename'],
              '_environmentName':(raw_info.get('environmentNames') or ['DefaultEnvironment'])[0],
              '_environmentNames':raw_info.get('environmentNames') or ['DefaultEnvironment']}
        configs=[]
        for d in refs:
            configs.append({'_difficulty':d['difficulty'],'_characteristic':d['characteristic'],
                '_beatmapFilename':d['beatmapDataFilename'],'_lightshowFilename':d.get('lightshowDataFilename'),
                '_noteJumpMovementSpeed':d.get('noteJumpMovementSpeed',0),
                '_noteJumpStartBeatOffset':d.get('noteJumpStartBeatOffset',0),
                '_environmentNameIdx':d.get('environmentNameIdx',0),
                '_customData':{'_requirements':d.get('customData',{}).get('requirements',[])}})
        audio_path=resolve_file(folder,audio['audioDataFilename']);audio_data=load_json(audio_path)
    else:
        require(version.startswith('2.'),'Поддерживаются Info.dat 2.x и 4.x.')
        info=dict(raw_info);configs=[]
        for group in raw_info.get('_difficultyBeatmapSets',[]):
            configs += [dict(d,_characteristic=group['_beatmapCharacteristicName']) for d in group['_difficultyBeatmaps']]
    require(info.get('_songTimeOffset',0)==0,'Карты с ненулевым смещением аудио пока не поддерживаются.')
    files=[info_path,resolve_file(folder,info['_songFilename']),resolve_file(folder,info['_coverImageFilename'])]
    if audio_data is not None:files.append(audio_path)
    difficulties=[];timeline=None
    for config in configs:
        name=config['_difficulty']
        label=config.get('_characteristic','Standard')+'/'+name
        try:
            path=resolve_file(folder,config['_beatmapFilename'])
            files.append(path);light_path=None
            if config.get('_lightshowFilename'):
                light_path=resolve_file(folder,config['_lightshowFilename']);files.append(light_path)
            require(config.get('_characteristic','Standard') in ['Standard','OneSaber','NoArrows'],'Режим '+config.get('_characteristic','Standard')+' пока не поддерживается.')
            require(name in RANK,f'Неизвестная сложность: {name}')
            mods=config.get('_customData',{}).get('_requirements',[])
            require(not mods or compatible,'Требует модов: '+', '.join(mods))
            notes=[]
            if mods:notes.append('Версия без эффектов '+', '.join(mods)+': движения могут отличаться от авторских.')
            chart,lights,events=convert(load_json(path),compatible,notes)
            if light_path:lights=load_json(light_path)
            if timeline is None:timeline=events
            require(timeline==events,'Темп этой сложности отличается от остальных.')
            require(len(chart['colorNotes'])>0,'Нет обычных блоков после преобразования.')
            difficulties.append((name,config,chart,lights))
            warnings += [label+': '+n for n in notes]
        except PatcherError as e:warnings.append(label+' пропущена: '+str(e))
    require(difficulties,'Нет совместимых сложностей.\n'+'\n'.join(warnings))
    require(len({(x[1].get('_characteristic','Standard'),x[0]) for x in difficulties})==len(difficulties),'Повторяющаяся сложность в карте.')
    supported=load_json(Path(__file__).parent/'assets'/'environments.json')
    envs=list(info.get('_environmentNames') or [info.get('_environmentName','DefaultEnvironment')])
    missing={i for i,n in enumerate(envs) if n not in supported}
    if missing:
        warnings.append('Арена '+', '.join(envs[i] for i in sorted(missing))+' отсутствует в игре: заменена на Default с простым светом.')
        difficulties=[(n,c,m,steady_lights() if c.get('_environmentNameIdx',0) in missing else l) for n,c,m,l in difficulties]
        for i in missing:envs[i]='DefaultEnvironment'
    info['_environmentNames']=envs;info['_environmentName']=envs[0]
    for n,c,m,l in difficulties:require(0<=c.get('_environmentNameIdx',0)<len(envs),'Неверный индекс арены.')
    digest=hashlib.sha256()
    for p in sorted(set(files),key=lambda p:p.name.casefold()):
        digest.update(p.name.casefold().encode());digest.update(p.read_bytes())
    return {'info':info,'difficulties':sorted(difficulties,key=lambda d:(d[1].get('_characteristic','Standard'),RANK[d[0]])),
            'events':timeline,'files':files,'hash':digest.hexdigest(),'folder':folder,
            'audio_data':audio_data,'warnings':warnings,'compatible':compatible}

class Library:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'library.json'
        self.state=load_json(self.path) if self.path.exists() else {'base_obb':'','songs':[]}
    def save(self):
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(self.path)
    def import_path(self,source,level_id=None,compatible=False):
        source=Path(source)
        if source.is_file():
            require(source.suffix.lower()=='.zip','Выбери ZIP с картой BeatSaver или папку с info.dat.')
            temp_root=self.root/'tmp';temp_root.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='pico_map_',dir=temp_root) as temp:
                with zipfile.ZipFile(source) as z:
                    require(len(z.infolist())<=4000,'В ZIP слишком много файлов.')
                    require(sum(i.file_size for i in z.infolist())<1024**3,'Распакованная карта больше 1 ГБ.')
                    for item in z.infolist():
                        dest=(Path(temp)/item.filename).resolve()
                        require(dest.is_relative_to(Path(temp).resolve()),'Небезопасный путь внутри ZIP.')
                    z.extractall(temp)
                infos=[p for p in Path(temp).rglob('*') if p.name.casefold()=='info.dat']
                require(len(infos)==1,'В ZIP должен находиться один info.dat. Добавляй карты по отдельности.')
                return self.import_path(infos[0].parent,level_id,compatible)
        require(source.is_dir(),'Папка карты не найдена.')
        parsed=inspect_map(source,compatible)
        for entry in self.state['songs']:
            if entry['hash']==parsed['hash']:
                entry.update(enabled=True,compatible=compatible,warnings=parsed['warnings'],
                    difficulties=[(x[1].get('_characteristic','Standard')+'/' if x[1].get('_characteristic','Standard')!='Standard' else '')+x[0] for x in parsed['difficulties']]);self.save();return entry,False
        key=parsed['hash'][:24]
        dest=self.root/'maps'/key
        dest.mkdir(parents=True,exist_ok=True)
        for p in parsed['files']:
            relative=p.relative_to(source.resolve())
            target=dest/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,target)
        info=parsed['info']
        entry={'hash':parsed['hash'],'folder':str(Path('maps')/key),
               'level_id':level_id or 'PicoCustom'+parsed['hash'][:20],
               'title':info['_songName'],'artist':info['_songAuthorName'],
               'difficulties':[(x[1].get('_characteristic','Standard')+'/' if x[1].get('_characteristic','Standard')!='Standard' else '')+x[0] for x in parsed['difficulties']],'enabled':True,
               'compatible':compatible,'warnings':parsed['warnings']}
        require(not any(e['level_id']==entry['level_id'] for e in self.state['songs']),'Повторяющийся ID песни.')
        self.state['songs'].append(entry);self.save()
        return entry,True
