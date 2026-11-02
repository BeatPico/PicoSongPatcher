import sys,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'beat_saber_lab'/'python_packages'))
from library import Library
from engine import build,OBB
import UnityPy

lib=Library(ROOT/'integration_data')
lib.import_path(r'C:\Users\home\Downloads\2b59 (Billie Jean - petrofsilvers)',level_id='BillieJean2b59')
lib.import_path(r'C:\Users\home\Downloads\55654 (feeling lucky - uiop327)')
base=r'C:\Users\home\Downloads\PICO_Beat_Saber_-MODDED-_1.40.8_OVRport_3.2.2\com.beatgames.beatsaber\main.1716.com.beatgames.beatsaber.obb'
out=build(base,lib.state['songs'],lib.root,ROOT/'integration_output',ROOT/'assets',lambda s:print(s,flush=True))
with zipfile.ZipFile(out/'Android'/'obb'/'com.beatgames.beatsaber'/OBB) as z:
    name=next(n for n in z.namelist() if '/ostvol1_pack_assets_all' in n)
    env=UnityPy.load(z.read(name))
    levels=[o.parse_as_dict() for o in env.objects if o.type.name=='MonoBehaviour' and o.peek_name().endswith('BeatmapLevel')]
    assert len(levels)==12
    assert {l['_songName'] for l in levels}>={'Billie Jean','feeling lucky','Beat Saber'}
    billie=next(l for l in levels if l['_levelID']=='BillieJean2b59')
    assert [d['_difficulty'] for d in billie['_previewDifficultyBeatmapSets'][0]['_previewDifficultyBeatmaps']]==[2,3]
    assert len(z.infolist())==866
(ROOT/'integration_result.json').write_text(json.dumps({'output':str(out),'status':'PASS','songs':2,'ost1_songs':12,'obb_entries':866},indent=2),encoding='utf-8')
print('INTEGRATION PASS',flush=True)
