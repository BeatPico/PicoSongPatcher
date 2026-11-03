"""Read pack menus and preserve native custom songs from compatible OBB files."""
import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile

import UnityPy
from maps import require, PatcherError
from arenas import environments_in_archive

DIFFICULTIES={0:'Easy',1:'Normal',2:'Hard',3:'Expert',4:'ExpertPlus'}
MODES={-7286399427822119286:'Standard',-5623662769225589684:'OneSaber',
       -8583864861369561029:'NoArrows',4533580413116749821:'360Degree',-5995858427784384822:'90Degree'}


def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def stock_ids(assets=None):
    path=(Path(assets) if assets else Path(__file__).resolve().parent/'assets')/'stock_packs.json'
    require(path.is_file(),'Не найден справочник штатных паков stock_packs.json.')
    return {level for pack in read_json(path)['packs'] for level in pack['stock_levels']}

def hash_file(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def json_bytes(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')


class CacheArchive:
    def __init__(self,path,assets=None):
        self.path=Path(path).resolve();require(self.path.is_file(),'Кэш OBB не найден.')
        self.before=self.path.stat();self.stock=stock_ids(assets);self.archive=zipfile.ZipFile(self.path)
        self.assets=Path(assets) if assets else Path(__file__).resolve().parent/'assets'
        self.names=set(self.archive.namelist());require(len(self.names)==len(self.archive.namelist()),'В кэше повторяются имена файлов.')
        require('assets/aa/catalog.json' in self.names,'В OBB отсутствует каталог ресурсов Beat Saber.')
        self._packs={}
    def __enter__(self):return self
    def __exit__(self,*args):self.archive.close();self._packs.clear()
    def pack(self,path):
        if path not in self._packs:
            info=self.archive.getinfo(path);require(info.file_size<1024**3,'Меню пака слишком велико.')
            env=UnityPy.load(self.archive.read(path));objects={o.path_id:o for o in env.objects}
            collections=[o.parse_as_dict() for o in objects.values() if o.type.name=='MonoBehaviour' and isinstance(o.parse_as_dict().get('_beatmapLevels'),list)]
            require(len(collections)==1,'Неоднозначная коллекция песен: '+path)
            collection=collections[0]
            definitions=[o.parse_as_dict() for o in objects.values() if o.type.name=='MonoBehaviour' and '_packID' in o.parse_as_dict()]
            require(len(definitions)==1,'Неоднозначное определение пака: '+path)
            definition=definitions[0];pack_id=Path(path).name.split('_pack_assets_all_')[0]
            bundle=next(o.parse_as_dict() for o in objects.values() if o.type.name=='AssetBundle')
            root=next(p for p,_ in bundle['m_Container'] if p.casefold().endswith('packdefinition.asset'))
            prefix=root.replace('\\','/').rsplit('/SO/',1)[0].casefold()
            if prefix==root.casefold():prefix=root.casefold().rsplit('/so/',1)[0]
            self._packs[path]={'id':pack_id,'name':definition.get('_packName',pack_id),'path':path,
                'collection':collection,'definition':definition,'objects':objects,'env':env,
                'package_prefix':prefix,'crc32':f'{info.CRC:08x}','bytes':info.file_size}
        return self._packs[path]
    def inventory(self,log=lambda text:None):
        packs=[];songs=[];warnings=[]
        paths=[n for n in self.archive.namelist() if '_pack_assets_all_' in n and n.endswith('.bundle')]
        for index,path in enumerate(paths,1):
            log(f'Чтение паков: {index}/{len(paths)}')
            try:
                pack=self.pack(path);seen=set();pack_songs=[]
                for pointer in pack['collection']['_beatmapLevels']:
                    require(pointer['m_FileID']==0 and pointer['m_PathID'] in pack['objects'],'Повреждена ссылка на песню: '+path)
                    data=pack['objects'][pointer['m_PathID']].parse_as_dict();level_id=data['_levelID']
                    require(level_id not in seen,'Повторяющийся ID в паке: '+level_id);seen.add(level_id)
                    asset='assets/BeatmapLevelsData/'+level_id.lower();present=asset in self.names;header=False
                    if present:
                        with self.archive.open(asset) as stream:header=stream.read(8).startswith(b'UnityFS')
                    custom=level_id not in self.stock;subtitle=data.get('_songSubName','');mapper=data.get('_levelAuthorName','')
                    kind=('AI' if 'InfernoSaber' in mapper or '(AI map)' in subtitle or '(AI trained)' in subtitle else
                          'Старый Auto' if '(Auto map)' in subtitle or 'Pico Song Patcher Auto' in mapper else
                          'Готовая карта' if custom else 'Оригинал')
                    difficulties=[]
                    for group in data.get('_previewDifficultyBeatmapSets',[]):
                        for chart in group.get('_previewDifficultyBeatmaps',[]):
                            label=DIFFICULTIES.get(chart['_difficulty'],str(chart['_difficulty']))
                            if label not in difficulties:difficulties.append(label)
                    song={'id':level_id,'title':data.get('_songName',level_id),'artist':data.get('_songAuthorName',''),
                        'subtitle':subtitle,'mapper':mapper,'kind':kind,'custom':custom,'duration':data.get('_songDuration',0),
                        'difficulties':difficulties,'gameplay_present':present,'unity_header':header,
                        'pack_id':pack['id'],'pack_name':pack['name'],'menu_path':path,'menu_path_id':pointer['m_PathID']}
                    pack_songs.append(song)
                songs.extend(pack_songs)
                packs.append({key:pack[key] for key in ('id','name','path','package_prefix','crc32','bytes')}|
                    {'total':len(pack_songs),'custom':sum(s['custom'] for s in pack_songs),
                     'buy_option':pack['definition'].get('_packBuyOption',0)})
            except (KeyError,ValueError,RuntimeError,StopIteration,PatcherError) as error:
                warnings.append(path+': '+str(error))
        require(packs,'Не удалось прочитать ни один пак Beat Saber из этого OBB.')
        after=self.path.stat();require((self.before.st_size,self.before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'Кэш менялся во время чтения. Дождись окончания копирования.')
        ids={s['id'] for s in songs if s['custom']}
        return {'path':str(self.path),'bytes':after.st_size,'mtime_ns':after.st_mtime_ns,'packs':packs,'songs':songs,
            'arenas':environments_in_archive(self.names,self.assets),
            'total':len(songs),'custom':len(ids),'ai':len({s['id'] for s in songs if s['kind']=='AI'}),
            'auto':len({s['id'] for s in songs if s['kind']=='Старый Auto'}),
            'missing_gameplay':sorted({s['id'] for s in songs if s['custom'] and (not s['gameplay_present'] or not s['unity_header'])}),
            'warnings':warnings,'read_only':True}
    def song_material(self,song):
        pack=self.pack(song['menu_path']);level=copy.deepcopy(pack['objects'][song['menu_path_id']].parse_as_dict())
        pointer=level['_previewAudioClip'];require(pointer['m_FileID']==0,'Превью этой карты хранится во внешнем ресурсе.')
        preview=pack['objects'][pointer['m_PathID']].parse_as_dict();resource=preview['m_Resource']
        name=resource['m_Source'].rsplit('/',1)[-1];require(name in pack['env'].file.files,'В меню отсутствует ресурс превью.')
        raw=pack['env'].file.files[name].bytes;offset,size=resource['m_Offset'],resource['m_Size']
        fsb=raw[offset:offset+size];require(len(fsb)==size and fsb.startswith(b'FSB'),'Повреждён звук превью.')
        image_pointer=level['_coverImage'];require(image_pointer['m_FileID']==0,'Обложка этой карты хранится во внешнем ресурсе.')
        image=pack['objects'][image_pointer['m_PathID']].parse_as_object().image
        buffer=io.BytesIO();image.save(buffer,format='PNG')
        return {'level':level,'preview':preview,'preview_frames':round(preview['m_Length']*44100),
                'package_prefix':pack['package_prefix']},fsb,buffer.getvalue()


def inspect_cache(path,assets=None,log=lambda text:None):
    with CacheArchive(path,assets) as archive:return archive.inventory(log)


def write_cached_material(archive,song,root):
    metadata,preview,cover=archive.song_material(song)
    asset='assets/BeatmapLevelsData/'+song['id'].lower();native=archive.archive.read(asset)
    require(native.startswith(b'UnityFS'),'Файл песни не является UnityFS.')
    key=hashlib.sha256(song['id'].encode()+native+json_bytes(metadata)).hexdigest()[:24]
    folder=Path(root)/'cache_maps'/key;folder.mkdir(parents=True,exist_ok=True)
    files={'level.bundle':native,'preview.fsb':preview,'cover.png':cover,'menu.json':json_bytes(metadata)}
    for name,raw in files.items():(folder/name).write_bytes(raw)
    manifest={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}
    (folder/'files.json').write_bytes(json_bytes(manifest))
    return folder,hashlib.sha256(json_bytes(manifest)).hexdigest()


def cached_parsed(folder):
    folder=Path(folder);files=read_json(folder/'files.json')
    require(all(hash_file(folder/name)==sha for name,sha in files.items()),'Локальная копия песни из кэша изменилась.')
    material=read_json(folder/'menu.json');level=material['level'];info={key:copy.deepcopy(level.get(key,'')) for key in
        ('_songName','_songSubName','_songAuthorName','_levelAuthorName','_beatsPerMinute','_songTimeOffset','_shuffle','_shufflePeriod','_previewStartTime','_previewDuration')}
    info['_coverImageFilename']='cover.png';info['_environmentName']=level.get('_environmentName',{}).get('_environmentName','DefaultEnvironment')
    info['_environmentNames']=[e['_environmentName'] for e in level.get('_environmentNames',[])] or [info['_environmentName']]
    difficulties=[];mode_ids={}
    for group in level['_previewDifficultyBeatmapSets']:
        mode_id=group['_beatmapCharacteristic']['m_PathID'];mode=MODES.get(mode_id,'Native_'+str(mode_id));mode_ids[mode]=mode_id
        for chart in group['_previewDifficultyBeatmaps']:
            name=DIFFICULTIES[chart['_difficulty']]
            config={'_characteristic':mode,'_noteJumpMovementSpeed':chart.get('_noteJumpMovementSpeed',0),
                '_noteJumpStartBeatOffset':chart.get('_noteJumpStartBeatOffset',0),'_environmentNameIdx':chart.get('_environmentNameIdx',0)}
            count=chart.get('_notesCount',0);extra=max(0,chart.get('_cuttableBeatmapObjectsCount',count)-count)
            counts={'colorNotes':[None]*count,'bombNotes':[None]*chart.get('_bombsCount',0),
                'obstacles':[None]*chart.get('_obstaclesCount',0),'chains':[{'ci':0}] if extra else [],'chainsData':[{'c':extra+1}] if extra else []}
            difficulties.append((name,config,counts,{}))
    return {'info':info,'folder':folder,'difficulties':difficulties,'mode_ids':mode_ids},material


def cached_audio(folder,destination):
    env=UnityPy.load((Path(folder)/'level.bundle').read_bytes())
    clips=[o.parse_as_object() for o in env.objects if o.type.name=='AudioClip']
    require(clips,'В песне не найден AudioClip.')
    clip=max(clips,key=lambda value:value.m_Length)
    material=Path(folder)/'menu.json'
    if material.is_file():
        expected=read_json(material)['level'].get('_songDuration',0)
        require(not expected or abs(clip.m_Length-expected)<1.,'В игровом файле не найдено полное аудио песни.')
    samples=clip.samples;require(samples,'Не удалось извлечь звук песни.')
    Path(destination).write_bytes(next(iter(samples.values())))
    return Path(destination)


def text_report(inventory):
    clean=lambda value:' '.join(str(value).split())
    rows=['ПЕСНИ В КЭШЕ — ВСЕ ПРОЧИТАННЫЕ ПАКИ',inventory['path'],'',
          f"Паков: {len(inventory['packs'])}; записей: {inventory['total']}; добавленных песен: {inventory['custom']}; AI: {inventory['ai']}.",'']
    for song in inventory['songs']:
        rows.append(f"[{song['pack_name']}] {clean(song['title'])} — {clean(song['artist'])} [{song['kind']}] · "+', '.join(song['difficulties']))
    if inventory.get('warnings'):rows+=['','Не удалось прочитать:',*inventory['warnings']]
    return '\n'.join(rows)+'\n'
