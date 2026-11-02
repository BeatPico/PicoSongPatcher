import zipfile,json,collections
from pathlib import Path
codes={'5544b','5547d','55488','5549e','55528','5552a','55587','555af','555b6','555da','555f0','55624','53a7a'}
results=[]
for path in Path(r'C:\Users\home\Downloads').glob('*.zip'):
    if path.name.split(' ')[0] not in codes:continue
    with zipfile.ZipFile(path) as z:
        name=next(n for n in z.namelist() if n.lower().endswith('info.dat'))
        info=json.loads(z.read(name).decode('utf-8-sig'));prefix=name[:-len('info.dat')]
        row={'zip':path.name,'info_version':info.get('_version',info.get('version')),'info':info,'charts':[]}
        refs=[]
        for group in info.get('_difficultyBeatmapSets',[]):
            if group['_beatmapCharacteristicName']=='Standard':refs += group['_difficultyBeatmaps']
        refs += [d for d in info.get('difficultyBeatmaps',[]) if d.get('characteristic')=='Standard']
        for d in refs:
            fn=d.get('_beatmapFilename',d.get('beatmapDataFilename'))
            m=json.loads(z.read(prefix+fn).decode('utf-8-sig'))
            notes=m.get('colorNotes',m.get('_notes',[]));coords=[];custom=collections.Counter()
            for n in notes:
                custom.update(n.get('customData',n.get('_customData',{})).keys())
                coords.append((n.get('x',n.get('_lineIndex',0)),n.get('y',n.get('_lineLayer',0))))
            row['charts'].append({'config':d,'version':m.get('version',m.get('_version')),
                'arrays':{k:len(v) for k,v in m.items() if isinstance(v,list)},
                'note_custom_keys':dict(custom),'coord_bounds':(min(coords),max(coords)) if coords else None,
                'sample_notes':notes[:2],'custom_keys':list(m.get('customData',m.get('_customData',{}))),
                'note_metadata':m.get('colorNotesData',[])[:2]})
        results.append(row)
out=Path(__file__).parent/'new_maps_audit.json'
out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
for r in results:
    print(r['zip'],r['info_version'])
    for c in r['charts']:
        d=c['config'];print(' ',d.get('_difficulty',d.get('difficulty')),c['version'],
            d.get('_customData',d.get('customData')),c['note_custom_keys'],c['coord_bounds'])
print('AUDIT',out)
