"""Read actual OST Vol.1 menu entries from an OBB, without modifying it."""
import json,zipfile
from pathlib import Path
import UnityPy
from maps import PatcherError,require

DIFFICULTIES={0:'Easy',1:'Normal',2:'Hard',3:'Expert',4:'ExpertPlus'}
ORIGINAL_IDS={'BeatSaber','Breezer','CommercialPumping','CountryRounds','Escape',
              'Legend','LvlInsane','BalearicPumping','100Bills','TurnMeOn'}

def inspect_cache(path):
    path=Path(path).resolve()
    require(path.is_file(),'Кэш OBB не найден.')
    before=path.stat()
    try:
        with zipfile.ZipFile(path) as archive:
            candidates=[n for n in archive.namelist() if '/ostvol1_pack_assets_all_' in n and n.endswith('.bundle')]
            require(len(candidates)==1,'В этом OBB не найден однозначный список OST Vol. 1.')
            menu_path=candidates[0];menu_info=archive.getinfo(menu_path)
            require(menu_info.file_size<1024**3,'Список песен слишком велик для просмотра.')
            env=UnityPy.load(archive.read(menu_path))
            objects={o.path_id:o for o in env.objects}
            collection=next((o.parse_as_dict() for o in env.objects if o.type.name=='MonoBehaviour' and
                             o.peek_name()=='OstVol1BeatmapLevelCollection'),None)
            require(collection is not None,'Не найдена коллекция OST Vol. 1.')
            names=set(archive.namelist());songs=[];seen=set()
            for reference in collection['_beatmapLevels']:
                require(reference['m_FileID']==0 and reference['m_PathID'] in objects,'В меню повреждена ссылка на песню.')
                data=objects[reference['m_PathID']].parse_as_dict()
                level_id=data['_levelID']
                require(level_id not in seen,'В меню повторяется ID песни: '+level_id);seen.add(level_id)
                asset='assets/BeatmapLevelsData/'+level_id.lower()
                present=asset in names;header=False
                if present:
                    with archive.open(asset) as stream:header=stream.read(8).startswith(b'UnityFS')
                subtitle=data.get('_songSubName','');author=data.get('_levelAuthorName','')
                kind=('AI' if '(AI map)' in subtitle or 'InfernoSaber' in author else
                      'Старый Auto' if '(Auto map)' in subtitle or 'Pico Song Patcher Auto' in author else
                      'Оригинал' if level_id in ORIGINAL_IDS else 'Готовая карта')
                difficulties=[]
                for group in data.get('_previewDifficultyBeatmapSets',[]):
                    for chart in group.get('_previewDifficultyBeatmaps',[]):
                        label=DIFFICULTIES.get(chart['_difficulty'],str(chart['_difficulty']))
                        if label not in difficulties:difficulties.append(label)
                songs.append({'id':level_id,'title':data.get('_songName',level_id),
                    'artist':data.get('_songAuthorName',''),'subtitle':subtitle,'mapper':author,
                    'kind':kind,'custom':level_id not in ORIGINAL_IDS,'duration':data.get('_songDuration',0),
                    'difficulties':difficulties,'gameplay_present':present,'unity_header':header})
    except (zipfile.BadZipFile,KeyError,ValueError,RuntimeError,StopIteration) as error:
        raise PatcherError('Не удалось прочитать список песен: '+str(error)) from error
    after=path.stat()
    require((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'Кэш менялся во время чтения. Дождись копирования и открой его заново.')
    return {'path':str(path),'bytes':after.st_size,'mtime_ns':after.st_mtime_ns,
            'menu_crc32':f'{menu_info.CRC:08x}','menu_bytes':menu_info.file_size,
            'songs':songs,'total':len(songs),'custom':sum(s['custom'] for s in songs),
            'ai':sum(s['kind']=='AI' for s in songs),'auto':sum(s['kind']=='Старый Auto' for s in songs),
            'missing_gameplay':[s['id'] for s in songs if not s['gameplay_present'] or not s['unity_header']],
            'read_only':True}

def text_report(inventory):
    clean=lambda value:' '.join(str(value).split())
    rows=['ПЕСНИ В КЭШЕ — OST Vol. 1',inventory['path'],'',
        f"Всего: {inventory['total']}; добавленных: {inventory['custom']}; AI: {inventory['ai']}; старых Auto: {inventory['auto']}.",
        'Список прочитан из самого OBB. Наличие файла не подтверждает запуск в игре.','']
    for i,song in enumerate(inventory['songs'],1):
        status='файл данных есть' if song['gameplay_present'] and song['unity_header'] else 'файл данных не найден'
        rows.append(f"{i}. {clean(song['title'])} — {clean(song['artist'])} [{song['kind']}]\n   "+
                    ', '.join(song['difficulties'])+'; '+status)
    return '\n'.join(rows)+'\n'

def compare_library(inventory,entries):
    cached={s['id'] for s in inventory['songs']}
    selected={e['level_id'] for e in entries if e.get('enabled',True)}
    known={e['level_id'] for e in entries}
    return {'selected_in_cache':sorted(selected&cached),'selected_not_in_cache':sorted(selected-cached),
            'cached_not_selected':sorted((cached&known)-selected),
            'cached_not_in_library':[s['id'] for s in inventory['songs'] if s['custom'] and s['id'] not in known]}
