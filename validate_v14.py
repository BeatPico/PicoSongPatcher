"""Actual modified-cache preservation and relocation checks; local outputs only."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT.parent/'beat_saber_lab/python_packages')]
from cache_archive import inspect_cache,CacheArchive,write_cached_material
from cache_builder import build
from engine import digest,OBB


def main():
    source=ROOT/'dist_v3/Pico Song Patcher 1.2/output/PicoSongs_2026-11-02_19-49-58_746656/Android/obb/com.beatgames.beatsaber'/OBB
    output=ROOT/'validation_v14';output.mkdir(exist_ok=True)
    before=digest(source);inventory=inspect_cache(source,ROOT/'assets')
    entries=[{'level_id':s['id'],'title':s['title'],'artist':s['artist'],'enabled':True} for s in inventory['songs'] if s['custom']]
    entries=list({e['level_id']:e for e in entries}.values())
    assert len(entries)==29 and len(inventory['packs'])>=30
    exact=build(source,entries,output/'unused-data',output/'unchanged',ROOT/'assets',log=lambda line:print(line,flush=True))
    assert digest(exact/'Android/obb/com.beatgames.beatsaber'/OBB)==before
    moved=[dict(e) for e in entries];removed=moved.pop();moved[0]['target_pack']='ostvol2'
    result=build(source,moved,output/'unused-data',output/'relocated',ROOT/'assets',log=lambda line:print(line,flush=True))
    after=inspect_cache(result/'Android/obb/com.beatgames.beatsaber'/OBB,ROOT/'assets')
    assert after['custom']==28 and not after['missing_gameplay']
    assert all(s['id']!=removed['level_id'] for s in after['songs'])
    assert {s['pack_id'] for s in after['songs'] if s['id']==moved[0]['level_id']}=={'ostvol2'}
    restore_root=output/'restore-data'
    with CacheArchive(source,ROOT/'assets') as archive:
        original=archive.inventory();song=next(s for s in original['songs'] if s['id']==removed['level_id'])
        folder,sha=write_cached_material(archive,song,restore_root)
    restored={'level_id':removed['level_id'],'title':removed['title'],'storage':'cached','folder':str(folder.relative_to(restore_root)),'hash':sha,'target_pack':'ostvol1'}
    keep=[{'level_id':s['id'],'title':s['title']} for s in after['songs'] if s['custom']]
    returning=build(result/'Android/obb/com.beatgames.beatsaber'/OBB,keep+[restored],restore_root,output/'restored',ROOT/'assets',log=lambda line:print(line,flush=True))
    restored_inventory=inspect_cache(returning/'Android/obb/com.beatgames.beatsaber'/OBB,ROOT/'assets')
    assert restored_inventory['custom']==29 and not restored_inventory['warnings']
    assert digest(source)==before
    (output/'backend_result.json').write_text(json.dumps({'status':'PASS','packs':len(inventory['packs']),
        'unchanged_output':str(exact),'relocated_output':str(result),'removed':removed['level_id'],'moved':moved[0]['level_id'],
        'restored_output':str(returning),'deleted_song_can_return':True,'original_unchanged':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print('BACKEND PASS',flush=True)


if __name__=='__main__':main()
