import sys,json,math,tempfile,wave,zipfile,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'beat_saber_lab'/'python_packages'))
import numpy as np
from library import Library,inspect_map
from maps import audio_metadata
from autogen import analyse,draft_charts,generate
from engine import build,run_ffmpeg,OBB,gameplay,bank,Menu
import UnityPy

CODES={'5544b','5547d','55488','5549e','55528','5552a','55587','555af','555b6','555da','555f0','55624','53a7a'}
WORK=ROOT/'validation_v11';WORK.mkdir(exist_ok=True)
def log(s):print(str(s).encode('ascii','backslashreplace').decode(),flush=True)

def rhythm_tests():
    results=[]
    for bpm in (90,120,160):
        sr=22050;duration=32;x=np.zeros(sr*duration,dtype=np.float32)
        for t in np.arange(0.37,duration,60/bpm):
            if 12<t<17:continue
            n=round(t*sr);p=np.arange(round(sr*0.07))/sr
            click=0.8*np.sin(2*np.pi*180*p)*np.exp(-p*65)
            x[n:n+len(click)]+=click[:len(x)-n]
        a=analyse(x,sr);charts=draft_charts(a)
        assert abs(a['bpm']-bpm)<1,(bpm,a['bpm'])
        counts=[len(charts[n]['colorNotes']) for n in ('Easy','Normal','Hard')]
        assert counts[0]<counts[1]<=counts[2],counts
        for name,c in charts.items():
            last=[-100,-100]
            for n in c['colorNotes']:
                t=n['b']*60/a['bpm']
                assert not 12.2<t<16.8,(name,t)
                assert t-last[n['c']]>=0.269,(name,t,last)
                last[n['c']]=t
                distance=abs(((t-0.37+30/bpm)%(60/bpm))-30/bpm)
                assert distance<0.06,(name,t,distance)
        results.append({'source_bpm':bpm,'estimated_bpm':a['bpm'],'counts':counts})
    return results

def main():
    result={'rhythm':rhythm_tests(),'maps':[]};log(result['rhythm'])
    lib=Library(WORK/'data')
    paths=sorted(p for p in Path(r'C:\Users\home\Downloads').glob('*.zip') if p.name.split(' ')[0] in CODES)
    assert len(paths)==13,len(paths)
    for path in paths:
        strict=None
        try:
            strict=Library(WORK/'strict').import_path(path)[0]
        except Exception as e:strict={'error':str(e)}
        entry,_=lib.import_path(path,compatible=True)
        p=inspect_map(lib.root/entry['folder'],True)
        for name,cfg,chart,light in p['difficulties']:
            assert len(chart['colorNotes'])>0
        row={'zip':path.name,'strict':strict.get('difficulties',strict.get('error')),
             'difficulties':entry['difficulties'],'warnings':entry['warnings']}
        result['maps'].append(row);log(path.name+': '+', '.join(entry['difficulties']))
    # Decode/encode a real provided song to MP3, then exercise the public autogen route.
    ffmpeg=ROOT/'assets/tools/ffmpeg.exe'
    billie=Path(r'C:\Users\home\Downloads\2b59 (Billie Jean - petrofsilvers)')
    original=inspect_map(billie)
    mp3=WORK/'Billie Jean test.mp3'
    run_ffmpeg(ffmpeg,['-i',billie/original['info']['_songFilename'],'-c:a','libmp3lame','-q:a','3','-metadata','title=Billie Jean Auto Test',mp3,'-y'])
    auto,_=generate(mp3,lib,ROOT/'assets',log=log)
    again,added=generate(mp3,lib,ROOT/'assets',log=log)
    assert not added and again['level_id']==auto['level_id']
    result['auto']={k:auto[k] for k in ('title','difficulties','auto_bpm','auto_confidence')}
    (WORK/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if '--build' in sys.argv:
        base=r'C:\Users\home\Downloads\PICO_Beat_Saber_-MODDED-_1.40.8_OVRport_3.2.2\com.beatgames.beatsaber\main.1716.com.beatgames.beatsaber.obb'
        out=build(base,lib.state['songs'],lib.root,WORK/'output',ROOT/'assets',log)
        with zipfile.ZipFile(out/'Android/obb/com.beatgames.beatsaber'/OBB) as z:
            menu=UnityPy.load(z.read(next(n for n in z.namelist() if '/ostvol1_pack_assets_all' in n)))
            levels=[o.parse_as_dict() for o in menu.objects if o.type.name=='MonoBehaviour' and o.peek_name().endswith('BeatmapLevel')]
            assert len(levels)==24,len(levels)
            single=next(e for e in lib.state['songs'] if any(d.startswith('OneSaber/') for d in e['difficulties']))
            t=next(l for l in levels if l['_levelID']==single['level_id'])
            assert t['_previewDifficultyBeatmapSets'][0]['_beatmapCharacteristic']['m_PathID']==-5623662769225589684
            for entry in lib.state['songs']:
                env=UnityPy.load(z.read('assets/BeatmapLevelsData/'+entry['level_id'].lower()))
                tree=next(o.parse_as_dict() for o in env.objects if o.type.name=='MonoBehaviour')
                p=inspect_map(lib.root/entry['folder'],entry.get('compatible',False))
                expected={(c.get('_characteristic','Standard'),n):m for n,c,m,l in p['difficulties']}
                from engine import decode_text
                objects={o.path_id:o for o in env.objects}
                for group in tree['_difficultyBeatmapSets']:
                    mode=group['_beatmapCharacteristicSerializedName']
                    for d in group['_difficultyBeatmaps']:
                        name=['Easy','Normal','Hard','Expert','ExpertPlus'][d['_difficulty']]
                        assert decode_text(objects[d['_beatmapAsset']['m_PathID']])==expected[(mode,name)]
            result['output']=str(out);result['build']='PASS'
            (WORK/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    log('VALIDATION PASS')

if __name__=='__main__':main()
