"""BeatSaver v2/v3 Standard charts -> built-in v4.1 charts."""
import math,copy

class PatcherError(Exception):
    pass

def require(condition, message):
    if not condition:
        raise PatcherError(message)

def number(value, label):
    require(isinstance(value,(float,int)) and math.isfinite(value),f'Некорректное число: {label}')
    return value

def steady_lights():
    return {'version':'4.0.0','basicEvents':[{'b':0,'i':i} for i in range(5)],
            'basicEventsData':[{'t':i,'i':1,'f':0.5} for i in range(5)],
            'useNormalEventsAsCompatibleEvents':True}

def convert_legacy(source):
    version=str(source.get('version',source.get('_version','')))
    require(version.startswith(('2.','3.')),f'Карта {version}: поддерживаются форматы v2 и v3.')
    if version.startswith('2.'):
        require(not source.get('_BPMChanges'),'Изменения темпа в старом формате v2 пока не поддерживаются.')
        events=source.get('_events',[])
        require(not any(e['_type']==100 for e in events),'BPM-события v2 пока не поддерживаются.')
        s={'version':'3.3.0','bpmEvents':[],'colorNotes':[],'bombNotes':[],
           'obstacles':[],'sliders':[],'burstSliders':[], 'basicBeatmapEvents':[],
           'colorBoostBeatmapEvents':[]}
        for n in source.get('_notes',[]):
            require(not n.get('_customData'),'Объекты с настройками модов пока не поддерживаются.')
            base={'b':n['_time'],'x':n['_lineIndex'],'y':n['_lineLayer']}
            if n['_type']==3:s['bombNotes'].append(base)
            else:
                require(n['_type'] in (0,1),'Неизвестный тип блока.')
                s['colorNotes'].append(dict(base,c=n['_type'],d=n['_cutDirection'],a=0))
        for w in source.get('_obstacles',[]):
            require(not w.get('_customData'),'Стены с настройками модов пока не поддерживаются.')
            require(w['_type'] in (0,1),'Нестандартные стены требуют модов.')
            s['obstacles'].append({'b':w['_time'],'x':w['_lineIndex'],'y':0 if w['_type']==0 else 2,
                'd':w['_duration'],'w':w['_width'],'h':5 if w['_type']==0 else 3})
        s['basicBeatmapEvents']=[{'b':e['_time'],'et':e['_type'],'i':e['_value'],
                                  'f':e.get('_floatValue',1)} for e in events]
        return convert(s)
    require(not source.get('rotationEvents'),'Карта с поворотами арены пока не поддерживается.')
    for key in ['lightColorEventBoxGroups','lightRotationEventBoxGroups','lightTranslationEventBoxGroups','vfxEventBoxGroups']:
        require(not source.get(key),'Карта использует продвинутый свет v3. Выбери карту с обычным освещением.')
    custom=source.get('customData',{})
    require(not any(custom.get(k) for k in ['customEvents','pointDefinitions','materials','environment']),
            'Карта содержит события модов Noodle/Chroma и не подходит для этого патчера.')
    out={'version':'4.1.0',**{k:[] for k in ['colorNotes','colorNotesData','bombNotes','bombNotesData',
        'obstacles','obstaclesData','arcs','arcsData','chains','chainsData']}}
    def add(key,data):
        index=len(out[key]);out[key].append(data);return index
    def basic(n):
        require(not n.get('customData'),'Объекты с настройками модов пока не поддерживаются.')
        for k in ('b','x','y'):number(n[k],k)
        require(n['x'] in range(4) and n['y'] in range(3),'Карта использует нестандартную сетку блоков.')
    for n in source.get('colorNotes',[]):
        basic(n)
        require(n['c'] in (0,1) and n['d'] in range(9),'Нестандартный цвет или направление блока.')
        i=add('colorNotesData',{k:n.get(k,0) for k in ['x','y','c','d','a']})
        out['colorNotes'].append({'b':n['b'],'r':0,'i':i})
    for n in source.get('bombNotes',[]):
        basic(n);i=add('bombNotesData',{k:n[k] for k in ['x','y']})
        out['bombNotes'].append({'b':n['b'],'r':0,'i':i})
    for w in source.get('obstacles',[]):
        require(not w.get('customData'),'Стены с настройками модов пока не поддерживаются.')
        for k in ['b','x','y','d','w','h']:number(w[k],k)
        require(w['d']>=0 and w['w']>0 and w['h']>0,'Некорректный размер стены.')
        i=add('obstaclesData',{k:w[k] for k in ['d','x','y','w','h']})
        out['obstacles'].append({'b':w['b'],'r':0,'i':i})
    for a in source.get('sliders',[]):
        basic(a)
        hi=add('colorNotesData',{'x':a['x'],'y':a['y'],'c':a['c'],'d':a['d'],'a':0})
        ti=add('colorNotesData',{'x':a['tx'],'y':a['ty'],'c':a['c'],'d':a['tc'],'a':0})
        ai=add('arcsData',{'m':a.get('mu',1),'tm':a.get('tmu',1),'a':a.get('m',0)})
        out['arcs'].append({'hb':a['b'],'tb':a['tb'],'hr':0,'tr':0,'hi':hi,'ti':ti,'ai':ai})
    for a in source.get('burstSliders',[]):
        basic(a)
        hi=add('colorNotesData',{'x':a['x'],'y':a['y'],'c':a['c'],'d':a['d'],'a':0})
        ci=add('chainsData',{'tx':a['tx'],'ty':a['ty'],'c':a['sc'],'s':a['s']})
        out['chains'].append({'hb':a['b'],'tb':a['tb'],'hr':0,'tr':0,'i':hi,'ci':ci})
    light={'version':'4.0.0','basicEvents':[],'basicEventsData':[],
           'colorBoostEvents':[],'colorBoostEventsData':[],
           'useNormalEventsAsCompatibleEvents':source.get('useNormalEventsAsCompatibleEvents',True)}
    for event in source.get('basicBeatmapEvents',[]):
        i=len(light['basicEventsData'])
        light['basicEventsData'].append({'t':event['et'],'i':event['i'],'f':event.get('f',1)})
        light['basicEvents'].append({'b':event['b'],'i':i})
    for event in source.get('colorBoostBeatmapEvents',[]):
        i=len(light['colorBoostEventsData'])
        light['colorBoostEventsData'].append({'b':int(event['o'])})
        light['colorBoostEvents'].append({'b':event['b'],'i':i})
    if not light['basicEvents']:light=steady_lights()
    return out,light,source.get('bpmEvents',[])

def convert(source,compatible=False,warnings=None):
    warnings=warnings if warnings is not None else []
    source=copy.deepcopy(source)
    version=str(source.get('version',source.get('_version','')))
    if compatible:
        modified=False;removed=0
        for field in ['colorNotes','bombNotes','obstacles','sliders','burstSliders','_notes','_obstacles']:
            kept=[]
            for obj in source.get(field,[]):
                custom=obj.pop('customData',obj.pop('_customData',{}))
                if custom:
                    modified=True
                    if custom.get('fake',custom.get('_fake',False)) or custom.get('interactable',custom.get('_interactable',True)) is False:
                        removed+=1;continue
                    if field in ['obstacles','_obstacles'] and any(k in custom for k in ['animation','_animation','track','_track','coordinates','_position']):
                        removed+=1;continue
                    if custom.get('disableBadCutDirection') or custom.get('_cutDirection')==8:
                        obj['d' if field=='colorNotes' else '_cutDirection']=8
                kept.append(obj)
            if field in source:source[field]=kept
        for key in ['customData','_customData']:
            data=source.get(key,{})
            if any(data.get(k) for k in ['customEvents','_customEvents','environment','_environment','pointDefinitions','_pointDefinitions']):modified=True
            source.pop(key,None)
        if modified:warnings.append('Упрощено: анимации, особые координаты и правила модов удалены; исходные движения могут отличаться.')
        if removed:warnings.append(f'Убрано декоративных или недоступных без модов объектов: {removed}.')
        for key in ['lightColorEventBoxGroups','lightRotationEventBoxGroups','lightTranslationEventBoxGroups','vfxEventBoxGroups']:
            if source.get(key):
                source[key]=[]
                if 'Продвинутый свет v3 заменён обычным освещением.' not in warnings:warnings.append('Продвинутый свет v3 заменён обычным освещением.')
        # A static ordinary-grid approximation must not retain overlapping duplicates.
        if modified and source.get('colorNotes'):
            used=set();kept=[];duplicates=0
            for n in source['colorNotes']:
                key=(round(n['b'],5),n['c'])
                if key in used:duplicates+=1;continue
                used.add(key);kept.append(n)
            if duplicates:
                source['colorNotes']=kept;warnings.append(f'Убрано наложений блоков после упрощения: {duplicates}.')
    if version.startswith('4.'):
        require(version in ('4.0.0','4.1.0'),f'Формат карты {version} пока не поддерживается.')
        for key in ['colorNotes','colorNotesData','bombNotes','bombNotesData','obstacles','obstaclesData',
                    'arcs','arcsData','chains','chainsData','njsEvents','njsEventData']:
            source.setdefault(key,[])
        for key,metadata,index in [('colorNotes','colorNotesData','i'),('bombNotes','bombNotesData','i'),
                                    ('obstacles','obstaclesData','i'),('arcs','arcsData','ai'),('chains','chainsData','ci')]:
            for obj in source[key]:
                require(0<=obj.get(index,0)<len(source[metadata]),f'Неверный индекс {key} в карте v4.')
        for obj in source['arcs']:
            require(all(0<=obj.get(k,0)<len(source['colorNotesData']) for k in ('hi','ti')),'Неверная ссылка дуги v4.')
        for obj in source['chains']:
            require(0<=obj.get('i',0)<len(source['colorNotesData']),'Неверная ссылка цепочки v4.')
        return source,steady_lights(),[]
    return convert_legacy(source)

def audio_metadata(bpm,events,frames,source=None):
    if source is not None:
        require(str(source.get('version','')).startswith('4.'),'Неподдерживаемый AudioData.dat.')
        freq=number(source.get('songFrequency',0),'songFrequency');require(freq>0,'Нет частоты в AudioData.dat.')
        regions=[]
        for r in source.get('bpmData',[]):
            si,ei,sb,eb=[number(r.get(k,0),k) for k in ('si','ei','sb','eb')]
            require(ei>si and eb>sb,'Неверный участок темпа в AudioData.dat.')
            regions.append({'si':round(si/freq*44100),'ei':round(ei/freq*44100),'sb':sb,'eb':eb})
        require(regions and regions[0]['si']==0,'Нет начального участка темпа в AudioData.dat.')
        for a,b in zip(regions,regions[1:]):
            require(abs(a['ei']-b['si'])<=1 and abs(a['eb']-b['sb'])<0.001,'Разрыв темпа в AudioData.dat.')
        last=regions[-1];require(last['si']<frames,'Темп находится за концом музыки.')
        last['eb']=last['sb']+(frames-last['si'])*(last['eb']-last['sb'])/(last['ei']-last['si']);last['ei']=frames
        return {'version':'4.0.0','songChecksum':'','songSampleCount':frames,'songFrequency':44100,'bpmData':regions,'lufsData':[]}
    require(bpm>0,'Темп должен быть больше нуля.')
    beat=seconds=0.;regions=[]
    for e in sorted(events,key=lambda x:x['b']):
        require(e['b']>=beat and e['m']>0,'Некорректное изменение темпа.')
        if e['b']>beat:
            end=seconds+(e['b']-beat)*60/bpm
            regions.append({'si':round(seconds*44100),'ei':round(end*44100),'sb':beat,'eb':e['b']})
            seconds=end;beat=e['b']
        bpm=e['m']
    duration=frames/44100
    require(seconds<duration,'Изменение темпа находится за концом музыки.')
    regions.append({'si':round(seconds*44100),'ei':frames,'sb':beat,'eb':beat+(duration-seconds)*bpm/60})
    return {'version':'4.0.0','songChecksum':'','songSampleCount':frames,'songFrequency':44100,
            'bpmData':regions,'lufsData':[]}
