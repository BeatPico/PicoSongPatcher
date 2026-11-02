"""Download a small, auditable human-map subset of the user's BeatSaver search.

Raw archives/packages remain unchanged. Filtered training views are separate.
This script does not train, replace model weights, or touch the headset/library.
"""
import copy,hashlib,json,math,shutil,stat,time,urllib.error,urllib.parse,urllib.request,zipfile
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent/'training_examples'
SOURCE_URL='https://beatsaver.com/?maxNps=7.6&minNps=1.2&order=Rating&from=2025-08-03'
QUERY={'order':'Rating','minNps':'1.2','maxNps':'7.6','from':'2025-08-03T00:00:00Z'}
TARGET=10
for name in ('api','archives','raw','packages'): (ROOT/name).mkdir(parents=True,exist_ok=True)

def save(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)
def log(value):print(str(value).encode('ascii','backslashreplace').decode(),flush=True)
def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':'PicoSongPatcher-Examples/1.0','Accept':'application/json,application/zip,*/*'})
    for attempt in range(4):
        try:return urllib.request.urlopen(request,timeout=90)
        except urllib.error.HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==3:raise
            time.sleep(min(10,float(error.headers.get('Retry-After',attempt+1))))
        except OSError:
            if attempt==3:raise
            time.sleep(attempt+1)
def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def locate(root,name):
    p=Path(str(name).replace('\\','/'));assert not p.is_absolute() and '..' not in p.parts,'Unsafe map path'
    current=Path(root).resolve()
    for part in p.parts:
        matches=[q for q in current.iterdir() if q.name.casefold()==part.casefold()]
        if len(matches)!=1:raise ValueError('Missing/ambiguous file '+str(name))
        current=matches[0].resolve()
        if not current.is_relative_to(Path(root).resolve()):raise ValueError('External map path')
    if not current.is_file():raise ValueError('Missing file '+str(name))
    return current
def check_timing(value,bpm):
    if isinstance(value,dict):
        for key,item in value.items():
            normalized=key.lstrip('_').lower()
            if normalized in ('songtimeoffset','shuffle') and item!=0:raise ValueError('Nonzero '+key)
            if normalized in ('bpmevents','bpmchanges') and item:
                for event in item:
                    tempo=event.get('m',event.get('_BPM',event.get('_bpm',event.get('bpm'))))
                    if not isinstance(tempo,(int,float)) or not math.isclose(tempo,bpm,abs_tol=.001):raise ValueError('Variable tempo')
            check_timing(item,bpm)
    elif isinstance(value,list):
        for item in value:check_timing(item,bpm)
def audit_chart(path,bpm):
    chart=read_json(path);version=str(chart.get('_version',chart.get('version','')))
    if not version.startswith(('2.','3.')):raise ValueError('Training reader does not support chart '+version)
    check_timing(chart,bpm)
    notes=[]
    for n in chart.get('_notes' if version.startswith('2.') else 'colorNotes',[]):
        if version.startswith('2.'):
            if n['_type']==3:continue
            t,c,x,y,d=n['_time'],n['_type'],n['_lineIndex'],n['_lineLayer'],n['_cutDirection'];angle=0
        else:t,c,x,y,d,angle=[n.get(k,0) for k in ('b','c','x','y','d','a')]
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (t,c,x,y,d,angle)):raise ValueError('Nonfinite note')
        if c not in (0,1) or x not in range(4) or y not in range(3) or d not in range(9) or t<0:raise ValueError('Extended note geometry')
        if angle!=0:raise ValueError('Nonzero note angle is unsupported by the trained direction classes')
        custom=n.get('_customData',n.get('customData',{}))
        if any(k.lstrip('_').lower() in ('position','coordinates','rotation','localrotation','cutdirection','scale','animation','track','fake') for k in custom):raise ValueError('Modded note behavior')
        notes.append((t*60/bpm,int(c),int(x),int(y),int(d)))
    if len(notes)<50:raise ValueError('Too few colored notes for a useful example')
    if any(e.get('_type',e.get('et',0))==100 for e in chart.get('_events',chart.get('basicBeatmapEvents',[]))):raise ValueError('Old tempo events')
    return {'version':version,'notes':len(notes),'first_note_seconds':min(n[0] for n in notes),
        'last_note_seconds':max(n[0] for n in notes),'directions':dict(Counter(n[4] for n in notes)),
        'bombs':sum(n.get('_type')==3 for n in chart.get('_notes',[])) if version.startswith('2.') else len(chart.get('bombNotes',[])),
        'walls':len(chart.get('_obstacles',chart.get('obstacles',[]))),
        'arcs':len(chart.get('_sliders',chart.get('sliders',[]))),
        'chains':len(chart.get('burstSliders',[]))}

def published(row):
    versions=[v for v in row['versions'] if v.get('state')=='Published']
    if not versions:raise ValueError('No published version')
    return versions[0]
def eligible_diffs(row):
    if row.get('automapper') or row.get('declaredAi') not in (None,'None'):return []
    if not 45<=row['metadata'].get('duration',0)<=420:return []
    return [d for d in published(row)['diffs'] if d['characteristic']=='Standard' and 1.2<=d.get('nps',0)<=7.6
            and not any(d.get(k) for k in ('ne','me','cinema','chroma'))]
def download(row):
    version=published(row);map_id=row['id'];archive=ROOT/'archives'/(map_id+'.zip')
    if not archive.is_file():
        url=version['downloadURL'];tmp=archive.with_suffix('.part')
        with fetch(url) as response,tmp.open('wb') as output:
            size=0
            while block:=response.read(512*1024):
                size+=len(block)
                if size>80*1024*1024:raise ValueError('Archive exceeds the small-set size budget')
                output.write(block)
        tmp.replace(archive)
    raw=ROOT/'raw'/map_id;raw.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist())>2000 or sum(i.file_size for i in z.infolist())>512*1024**2:raise ValueError('Oversized/unexpected archive')
        seen=set()
        for item in z.infolist():
            dest=(raw/item.filename.replace('\\','/')).resolve()
            if not dest.is_relative_to(raw.resolve()) or stat.S_ISLNK(item.external_attr>>16):raise ValueError('Unsafe archive member')
            if str(dest).casefold() in seen:raise ValueError('Duplicate archive filename')
            seen.add(str(dest).casefold())
        if z.testzip() is not None:raise ValueError('ZIP CRC failed')
        z.extractall(raw)
    infos=[p for p in raw.rglob('*') if p.name.casefold()=='info.dat']
    if len(infos)!=1:raise ValueError('Expected one Info.dat')
    package=infos[0].parent;info=read_json(infos[0])
    if not str(info.get('_version','')).startswith('2.'):raise ValueError('Info v4 requires an additional timing/schema adapter')
    bpm=info['_beatsPerMinute'];check_timing(info,bpm)
    if not isinstance(bpm,(int,float)) or not math.isfinite(bpm) or bpm<=0:raise ValueError('Invalid BPM')
    locate(package,info['_songFilename']);locate(package,info['_coverImageFilename'])
    # External timing is never silently discarded by this reference selection.
    for timing in package.rglob('*'):
        if timing.name.casefold() not in ('audiobpm.dat','audiodata.dat','bpminfo.dat'):continue
        data=read_json(timing);frequency=data.get('songFrequency',data.get('_songFrequency',0))
        regions=data.get('bpmData',data.get('_regions',[]))
        if not frequency or not regions:raise ValueError('Unsupported external audio timing metadata')
        for region in regions:
            si=region.get('si',region.get('_startSampleIndex'));ei=region.get('ei',region.get('_endSampleIndex'))
            sb=region.get('sb',region.get('_startBeat'));eb=region.get('eb',region.get('_endBeat'))
            if None in (si,ei,sb,eb) or ei<=si or not math.isclose((eb-sb)*60*frequency/(ei-si),bpm,rel_tol=.002):raise ValueError('Nonconstant external audio timing')
    wanted={d['difficulty']:d for d in eligible_diffs(row)};configs=[];audits=[];skipped=[]
    for group in info['_difficultyBeatmapSets']:
        if group['_beatmapCharacteristicName']!='Standard':continue
        for config in group['_difficultyBeatmaps']:
            name=config['_difficulty']
            if name not in wanted:continue
            try:
                mods=config.get('_customData',{}).get('_requirements',[])
                if mods:raise ValueError('Requires '+', '.join(mods))
                audit=audit_chart(locate(package,config['_beatmapFilename']),bpm)
                audit.update(difficulty=name,filename=config['_beatmapFilename'],api_nps=wanted[name]['nps'])
                configs.append(config);audits.append(audit)
            except (ValueError,KeyError,TypeError) as error:skipped.append({'difficulty':name,'reason':str(error)})
    if not configs:raise ValueError('No compatible ordinary Standard difficulty: '+json.dumps(skipped))
    view=ROOT/'packages'/map_id
    shutil.copytree(package,view,dirs_exist_ok=True)
    training_info=copy.deepcopy(info)
    training_info['_difficultyBeatmapSets']=[{'_beatmapCharacteristicName':'Standard','_difficultyBeatmaps':configs}]
    (view/infos[0].name).write_text(json.dumps(training_info,ensure_ascii=False,indent=2),encoding='utf-8')
    save(ROOT/'api'/(map_id+'.json'),row)
    return {'id':map_id,'url':'https://beatsaver.com/maps/'+map_id,'title':info['_songName'],
        'artist':info['_songAuthorName'],'mapper':info['_levelAuthorName'],'uploaded':row['uploaded'],
        'score':row['stats']['score'],'automapper':row['automapper'],'declared_ai':row.get('declaredAi'),
        'map_hash':version['hash'],'download_url':version['downloadURL'],
        'archive':str(archive.relative_to(ROOT)),'archive_bytes':archive.stat().st_size,
        'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'raw_package':str(package.relative_to(ROOT)),'training_package':str(view.relative_to(ROOT)),
        'info_filter':'Only validated Standard difficulties in requested NPS range; raw archive/info unchanged',
        'charts':audits,'skipped_charts':skipped}

def main():
    candidates=[]
    for page in range(5):
        cached=ROOT/'api'/f'search_rating_{page}.json'
        url='https://api.beatsaver.com/search/text/'+str(page)+'?'+urllib.parse.urlencode(QUERY)
        if cached.is_file():data=read_json(cached)
        else:
            with fetch(url) as response:data=json.load(response)
            save(cached,data);time.sleep(.4)
        candidates.extend(data['docs'])
        if len([d for d in candidates if eligible_diffs(d)])>=35:break
    # Prioritize graded easy-to-hard examples, then widen mapper/style diversity.
    def priority(row):
        ds=eligible_diffs(row)
        return (0 if any(d['nps']<=2.5 for d in ds) and len(ds)>=3 else 1,-row['stats']['score'])
    candidates=sorted((r for r in candidates if eligible_diffs(r)),key=priority)
    selected=[];skipped=[];mappers=Counter();ids=set()
    for row in candidates:
        if len(selected)>=TARGET:break
        if row['id'] in ids:continue
        mapper=row['metadata']['levelAuthorName'].casefold().strip()
        if mappers[mapper]>=2:continue
        log('Checking '+row['id']+' '+row['metadata']['songName'])
        try:
            record=download(row)
            selected.append(record);ids.add(row['id']);mappers[mapper]+=1
            log('Accepted '+row['id']+': '+', '.join(a['difficulty']+' '+str(a['api_nps'])+' NPS' for a in record['charts']))
        except Exception as error:
            skipped.append({'id':row['id'],'title':row['metadata']['songName'],'reason':type(error).__name__+': '+str(error)})
            log('Skipped '+row['id']+': '+str(error))
        save(ROOT/'manifest.json',{'source_search':SOURCE_URL,'api_query':QUERY,'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),
            'target_packages':TARGET,'packages':selected,'skipped':skipped,'trained':False,'active_model_changed':False})
        time.sleep(.4)
    if len(selected)!=TARGET:raise RuntimeError(f'Only {len(selected)} compatible packages; review skipped reasons')
    manifest=read_json(ROOT/'manifest.json')
    total=sum(p['archive_bytes'] for p in selected);charts=sum(len(p['charts']) for p in selected)
    rows=['ПРИМЕРЫ ДЛЯ ОБУЧЕНИЯ — BEATSAVER','',SOURCE_URL,'',
        f'Скачано: {len(selected)} песен, {charts} совместимых сложностей; ZIP: {total/1024**2:.1f} МБ.',
        'Архивы и исходные карты сохранены без изменений в archives и raw.',
        'Отдельные папки packages содержат отфильтрованный Info.dat для обучения.',
        'Отобраны авторские Standard-карты, NPS 1.2–7.6, без обязательных модов,',
        'переменного темпа и неподдерживаемой геометрии. Модель пока не обучалась.','']
    for i,p in enumerate(selected,1):
        rows.extend([f"{i}. {p['title']} — {p['artist']} / карта: {p['mapper']}",p['url'],
            '   '+', '.join(a['difficulty']+': '+str(a['api_nps'])+' NPS' for a in p['charts'])])
    (ROOT/'ПОДБОРКА.txt').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    log(f'DOWNLOAD COMPLETE: {len(selected)} songs / {charts} charts / {total/1024**2:.1f} MB')
if __name__=='__main__':main()
