"""Update a compatible cache, reusing native songs and preserving stock packs."""
import base64
import copy
import gc
import hashlib
import io
import json
import re
from pathlib import Path
import shutil
import struct
import tempfile
from threading import Lock
import time
import wave
import zipfile
from datetime import datetime

import UnityPy
from UnityPy.streams import EndianBinaryReader
from cache_archive import CacheArchive,inspect_cache,cached_parsed,read_json,text_report
from engine import (OBB,Menu,bank,gameplay,run_ffmpeg,digest,encoded,parse_extra,check_pointers)
from library import inspect_map,resolve_file
from maps import require,steady_lights


_OBB_ZIP_LOCK = Lock()


def write_obb(source, destination, blobs=None, native_files=None, pruned=()):
    """Write classic ZIP offsets up to their unsigned 32-bit limit."""
    blobs, native_files = blobs or {}, native_files or {}
    with _OBB_ZIP_LOCK:
        # CPython promotes offsets to ZIP64 at 2 GiB. The game's ZIP reader
        # can use unsigned 32-bit offsets, and may reject ZIP64 records.
        previous_limit = zipfile.ZIP64_LIMIT
        zipfile.ZIP64_LIMIT = 0xFFFFFFFE
        try:
            with zipfile.ZipFile(destination, 'w', allowZip64=False) as result:
                for item in source.infolist():
                    if item.filename in pruned or item.filename in native_files:
                        continue
                    target = copy.copy(item)
                    extras, offset = bytearray(), 0
                    while offset + 4 <= len(item.extra):
                        tag, size = struct.unpack_from('<HH', item.extra, offset)
                        end = offset + 4 + size
                        if tag != 1:
                            extras.extend(item.extra[offset:end])
                        offset = end
                    target.extra = bytes(extras)
                    if item.extract_version == 45 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        target.extract_version = 20
                        if target.create_version == 45:
                            target.create_version = 20
                    target._compresslevel = 1
                    if item.filename in blobs:
                        result.writestr(target, blobs[item.filename])
                    else:
                        with source.open(item) as reader, result.open(target, 'w') as writer:
                            shutil.copyfileobj(reader, writer, 4 * 1024 * 1024)
                for name, path in native_files.items():
                    result.write(path, name, compress_type=zipfile.ZIP_STORED)
        except zipfile.LargeZipFile as error:
            require(False, 'OBB превышает предел обычного ZIP (4 ГиБ). Убери часть песен и собери заново: ' + str(error))
        finally:
            zipfile.ZIP64_LIMIT = previous_limit


def patch_catalog(catalog,replacements):
    """Locate bundle options by their addressable internal path, across rebuilds."""
    old=base64.b64decode(catalog['m_ExtraDataString']);entries=bytearray(base64.b64decode(catalog['m_EntryDataString']))
    count=struct.unpack_from('<i',entries,0)[0];require(len(entries)==4+28*count,'Неизвестный формат записей каталога.')
    targets={Path(path).name:(raw,crc) for path,(raw,crc) in replacements.items()};patches={};found=set()
    for i in range(count):
        row=4+28*i;internal=struct.unpack_from('<i',entries,row)[0];extra=struct.unpack_from('<i',entries,row+16)[0]
        if 0<=internal<len(catalog['m_InternalIds']):
            name=catalog['m_InternalIds'][internal].replace('\\','/').rsplit('/',1)[-1]
            if name in targets and extra>=0:patches[extra]=targets[name];found.add(name)
    require(found==set(targets),'В каталоге не найдены изменяемые паки: '+', '.join(set(targets)-found))
    new=bytearray();offsets={};old_values={}
    for start,lp,end,value in parse_extra(old):
        offsets[start]=len(new);old_values[start]=value
        if start in patches:
            raw,crc=patches[start];value=dict(value,m_Crc=crc,m_BundleSize=len(raw),m_Hash=hashlib.md5(raw).hexdigest())
            text=json.dumps(value,separators=(',',':')).encode('utf-16-le')
            new+=old[start:lp]+struct.pack('<I',len(text))+text
        else:new+=old[start:end]
    for i in range(count):
        position=4+28*i+16;old_offset=struct.unpack_from('<i',entries,position)[0]
        if old_offset>=0:
            require(old_offset in offsets,'Каталог ссылается на неизвестную запись.')
            struct.pack_into('<i',entries,position,offsets[old_offset])
    after={record[0]:record[3] for record in parse_extra(new)}
    require(all(after[offsets[key]]==value for key,value in old_values.items() if key not in patches),'Изменились посторонние записи каталога.')
    result=copy.deepcopy(catalog);result.update(m_ExtraDataString=base64.b64encode(new).decode(),m_EntryDataString=base64.b64encode(entries).decode())
    return encoded(result)


def retarget_native(raw,level_id,prefix):
    env=UnityPy.load(raw);objects=list(env.objects);bundle=next(o for o in objects if o.type.name=='AssetBundle');data=bundle.parse_as_dict()
    path=prefix+'/so/'+level_id.lower()+'/'+level_id.lower()+'beatmapleveldata.asset'
    if len(data['m_Container'])==1 and data['m_Container'][0][0].casefold()==path.casefold():return raw
    root_ids={o.path_id for o in objects if o.type.name=='MonoBehaviour' and '_difficultyBeatmapSets' in o.parse_as_dict()}
    found=any(metadata['asset']['m_PathID'] in root_ids for name,metadata in data['m_Container'])
    data['m_Container']=[[path if metadata['asset']['m_PathID'] in root_ids else name,metadata] for name,metadata in data['m_Container']]
    require(found,'В готовой песне не найден корень BeatmapLevelData.')
    originals={o.path_id:o.get_raw_data() for o in objects if o.path_id!=bundle.path_id}
    bundle.patch(data);result=env.file.save(packer='original');check=UnityPy.load(result);check_pointers(check)
    require(all(o.get_raw_data()==originals[o.path_id] for o in check.objects if o.path_id in originals),'Перенос между паками изменил данные песни.')
    return result


def add_cached(menu,folder,level_id):
    parsed,material=cached_parsed(folder);source=material['level'];before=len(menu.new_ids)
    menu.add(parsed,level_id,(Path(folder)/'preview.fsb').read_bytes(),material['preview_frames'],source['_songDuration'],source.get('_integratedLufs',-8.))
    by_id={o.path_id:o for o in menu.env.objects};level=by_id[menu.new_ids[before]];preview=by_id[menu.new_ids[before+1]]
    def updated(obj):
        # UnityPy's parser reads its original reader even after patch(). Read the
        # staged serialized bytes when modifying a newly created object twice.
        staged=copy.copy(obj);staged.reader=EndianBinaryReader(obj.data,endian=obj.reader.endian)
        staged.byte_start=0;staged.byte_size=len(obj.data)
        return staged.parse_as_dict()
    data=updated(level);standard=next(g for g in menu.template_level.parse_as_dict()['_previewDifficultyBeatmapSets'] if g['_beatmapCharacteristic']['m_PathID']==-7286399427822119286)
    groups=copy.deepcopy(source['_previewDifficultyBeatmapSets'])
    for group in groups:group['_beatmapCharacteristic']['m_FileID']=standard['_beatmapCharacteristic']['m_FileID']
    for key in ('_colorSchemes','_allDirectionsEnvironmentName','_environmentName','_environmentNames','_previewStartTime','_previewDuration'):
        if key in source:data[key]=copy.deepcopy(source[key])
    data['_previewDifficultyBeatmapSets']=groups;level.patch(data)
    clip=updated(preview)
    for key,value in material.get('preview',{}).items():
        if key not in ('m_Name','m_Resource'):clip[key]=copy.deepcopy(value)
    preview.patch(clip)


def finalize_folder(stage,final,log=print):
    stage,final=Path(stage).resolve(),Path(final).resolve()
    require(stage.parent==final.parent and stage!=final and stage!=stage.parent,'Неверный путь завершения сборки.')
    gc.collect()
    for attempt in range(8):
        try:stage.rename(final);return final
        except PermissionError:
            if attempt<7:time.sleep(.25)
    log('Windows блокирует переименование папки. Проверенные файлы готовы в '+str(stage))
    return stage


def _new_level(entry,library_root,assets,work,ffmpeg=None):
    ffmpeg=ffmpeg or assets/'tools/ffmpeg.exe'
    folder=Path(library_root)/entry['folder'];parsed=inspect_map(folder,entry.get('compatible',False))
    require(parsed['hash']==entry['hash'],'Файлы в библиотеке изменились: '+entry['title'])
    info=parsed['info'];wav=work/'audio.wav'
    run_ffmpeg(ffmpeg,['-i',resolve_file(folder,info['_songFilename']),'-ac','2','-ar','44100','-c:a','pcm_s16le',wav,'-y'])
    fsb,frames,pcm_hash=bank(wav);preview=work/'preview.wav'
    loudness=run_ffmpeg(ffmpeg,['-i',wav,'-af','ebur128','-f','null','NUL'])
    match=re.search(r'Integrated loudness:\s+I:\s+([-\d.]+) LUFS',loudness)
    lufs=float(match.group(1)) if match else -8.
    with wave.open(str(wav),'rb') as audio:
        start=max(0,min(round(info.get('_previewStartTime',0)*44100),max(0,frames-44100)));audio.setpos(start)
        pcm=audio.readframes(max(1,round(min(30,max(1,info.get('_previewDuration',10)))*44100)))
    with wave.open(str(preview),'wb') as audio:audio.setnchannels(2);audio.setsampwidth(2);audio.setframerate(44100);audio.writeframes(pcm)
    preview_fsb,preview_frames,_=bank(preview)
    raw,metadata=gameplay(assets/'gameplay_template.bundle',parsed,fsb,frames,entry['level_id'])
    check=UnityPy.load(raw);clip=next(o.parse_as_object() for o in check.objects if o.type.name=='AudioClip')
    with wave.open(io.BytesIO(next(iter(clip.samples.values()))),'rb') as audio:
        require(audio.getnframes()==frames and hashlib.sha256(audio.readframes(frames)).hexdigest()==pcm_hash,'Не прошла проверка звука: '+entry['title'])
    return raw,parsed,preview_fsb,preview_frames,frames/44100,lufs


def build(base,entries,library_root,output_root,assets,log=print,target_pack='ostvol1'):
    base=Path(base).resolve();assets=Path(assets).resolve();output_root=Path(output_root).resolve();library_root=Path(library_root).resolve()
    require(base.is_file(),'Выбери исходный OBB. Подходит кэш с уже добавленными песнями.')
    from portable_assets import game_assets,ensure_ffmpeg
    packaged_assets=assets
    assets=game_assets(base,assets,library_root/'runtime_assets',log)
    require(len({e['level_id'] for e in entries})==len(entries),'В библиотеке повторяется ID песни.')
    for entry in entries:require('/' not in entry['level_id'] and '\\' not in entry['level_id'] and entry['level_id'] not in ('.','..'),'Небезопасный ID песни.')
    output_root.mkdir(parents=True,exist_ok=True);stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')
    stage=output_root/('.building_'+stamp);stage.mkdir();final=output_root/('PicoSongs_'+stamp)
    destination=stage/'Android/obb/com.beatgames.beatsaber';destination.mkdir(parents=True)
    reports=[];completed=False
    try:
        log('Чтение исходного кэша и всех паков…')
        with CacheArchive(base,assets) as source, tempfile.TemporaryDirectory(prefix='work_',dir=stage) as temp:
            inventory=source.inventory(log);packs={p['id']:p for p in inventory['packs']};require(target_pack in packs,'Выбранный пак отсутствует в исходном кэше.')
            songs={};locations={}
            for song in inventory['songs']:
                if song['custom']:
                    songs.setdefault(song['id'],song);locations.setdefault(song['id'],set()).add(song['pack_id'])
            desired={entry['level_id']:({entry['target_pack']} if entry.get('target_pack') else locations.get(entry['level_id'],{entry.get('cached_pack',target_pack) if entry.get('cached_pack') in packs else target_pack})) for entry in entries}
            require(all(pack in packs for where in desired.values() for pack in where),'Один из назначенных паков отсутствует в этом OBB.')
            menus={};changes=set();native_files={};work=Path(temp)
            def menu(pack_id):
                if pack_id not in menus:menus[pack_id]=Menu(source.archive.read(packs[pack_id]['path']))
                return menus[pack_id]
            for pack in inventory['packs']:
                removed={song['id'] for song in inventory['songs'] if song['custom'] and song['pack_id']==pack['id'] and pack['id'] not in desired.get(song['id'],set())}
                if removed:
                    current=menu(pack['id']);by_id={o.path_id:o for o in current.env.objects}
                    current.levels['_beatmapLevels']=[p for p in current.levels['_beatmapLevels'] if by_id[p['m_PathID']].parse_as_dict()['_levelID'] not in removed]
                    current.loud['_perceivedLoudnessPerLevel']=[s for s in current.loud['_perceivedLoudnessPerLevel'] if s['_levelId'] not in removed]
                    changes.add(pack['id'])
            for index,entry in enumerate(entries,1):
                level_id=entry['level_id'];where=desired[level_id];existing=locations.get(level_id,set());asset='assets/BeatmapLevelsData/'+level_id.lower()
                log(f'Песня {index}/{len(entries)}: '+entry['title'])
                if entry.get('ai_arena') and not existing:
                    require(entry['ai_arena'] in inventory['arenas'],
                            'В этом OBB нет арены для песни «'+entry['title']+'». Выбери другую арену и пересоздай AI.')
                if existing and where<=existing:
                    require(asset in source.names,'В исходном OBB нет игровых данных: '+entry['title'])
                    reports.append({'id':level_id,'title':entry['title'],'reused_native':True,'packs':sorted(where)});continue
                if existing or entry.get('storage')=='cached':
                    if existing:
                        from cache_archive import write_cached_material
                        folder,_=write_cached_material(source,songs[level_id],work)
                    else:folder=library_root/entry['folder']
                    parsed,material=cached_parsed(folder);raw=(folder/'level.bundle').read_bytes()
                    for pack_id in where-existing:add_cached(menu(pack_id),folder,level_id);changes.add(pack_id)
                    target=sorted(where)[0];native=retarget_native(raw,level_id,packs[target]['package_prefix'])
                    path=work/(hashlib.sha256(level_id.encode()).hexdigest()+'.bundle');path.write_bytes(native);native_files[asset]=path
                    reports.append({'id':level_id,'title':entry['title'],'reused_native':True,'packs':sorted(where)})
                else:
                    require(entry.get('storage')!='cache-reference','Для переноса этой песни нужны её локальные данные или исходный OBB: '+entry['title'])
                    ffmpeg=ensure_ffmpeg(packaged_assets,library_root/'runtime_assets',log)
                    raw,parsed,preview,frames,duration,lufs=_new_level(entry,library_root,assets,work,ffmpeg)
                    for pack_id in where:menu(pack_id).add(parsed,level_id,preview,frames,duration,lufs);changes.add(pack_id)
                    native=retarget_native(raw,level_id,packs[sorted(where)[0]]['package_prefix'])
                    path=work/(hashlib.sha256(level_id.encode()).hexdigest()+'.bundle');path.write_bytes(native);native_files[asset]=path
                    reports.append({'id':level_id,'title':entry['title'],'reused_native':False,'audio_verified':True,'packs':sorted(where)})
            replacements={packs[key]['path']:menus[key].save() for key in changes}
            blobs={path:raw for path,(raw,crc) in replacements.items()}
            if replacements:blobs['assets/aa/catalog.json']=patch_catalog(json.loads(source.archive.read('assets/aa/catalog.json')),replacements)
            pruned={'assets/BeatmapLevelsData/'+level_id.lower() for level_id in songs if level_id not in desired}
            if not blobs and not pruned and not native_files and source.archive.start_dir <= zipfile.ZIP64_LIMIT:
                log('Все выбранные песни уже готовы: копирование исходного OBB…');shutil.copy2(base,destination/OBB)
            else:
                log('Запись кэша с сохранением готовых песен…')
                write_obb(source.archive,destination/OBB,blobs,native_files,pruned)
            log('Проверка готового кэша…')
            with zipfile.ZipFile(destination/OBB) as result:
                require(result.testzip() is None,'Повреждён итоговый ZIP-кэш.')
                expected=(source.names-pruned)|set(native_files)
                require(set(result.namelist())==expected,'Итоговый список файлов отличается от ожидаемого.')
                for item in source.archive.infolist():
                    if item.filename not in pruned and item.filename not in blobs and item.filename not in native_files:
                        after=result.getinfo(item.filename);require((after.CRC,after.file_size)==(item.CRC,item.file_size),'Изменился исходный ресурс: '+item.filename)
            shutil.copy2(assets/'original_espresso',destination/'espresso')
        actual=inspect_cache(destination/OBB,assets)
        require(actual['warnings']==inventory['warnings'],'Не удалось прочитать пак после сборки: '+'; '.join(actual['warnings']))
        require({s['id'] for s in actual['songs'] if s['custom']}==set(desired),'Список добавленных песен отличается от выбранного.')
        require({(s['id'],s['pack_id']) for s in actual['songs'] if s['custom']}=={(key,pack) for key,where in desired.items() for pack in where},'Песня попала в другой пак.')
        require(not actual['missing_gameplay'],'В готовом кэше отсутствуют данные песни.')
        before_stock={(s['id'],s['pack_id']) for s in inventory['songs'] if not s['custom']}
        require(before_stock=={(s['id'],s['pack_id']) for s in actual['songs'] if not s['custom']},'Изменился список штатных песен.')
        report={'app_version':'1.4','source':str(base),'output_sha256':digest(destination/OBB),'songs':reports,
            'local_checks_passed':True,'source_files_written':False,'device_written':False,'custom_song_count':actual['custom'],
            'reused_native_songs':sum(s['reused_native'] for s in reports),'warnings':actual['warnings']}
        (stage/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (stage/'КАК_УСТАНОВИТЬ.txt').write_text('Закрой игру на очках. Сохрани текущие OBB и espresso. Перенеси два файла из Android/obb/com.beatgames.beatsaber в такую же папку очков. Песни расположены в паках, указанных в ПЕСНИ_В_КЭШЕ.txt. Проверь новые карты в игре.',encoding='utf-8')
        completed=True;ready=finalize_folder(stage,final,log)
        actual['path']=str(ready/'Android/obb/com.beatgames.beatsaber'/OBB)
        (ready/'cache_inventory.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf-8')
        (ready/'ПЕСНИ_В_КЭШЕ.txt').write_text(text_report(actual),encoding='utf-8')
        (ready/'ГОТОВО.txt').write_text('Кэш прошёл проверки и готов к ручному переносу.',encoding='utf-8')
        log('Готово: '+str(ready));return ready
    except Exception:
        if stage.exists() and not completed:(stage/'СБОРКА_НЕ_ГОТОВА.txt').write_text('Проверки не завершены. Не переносите на очки.',encoding='utf-8')
        raise
