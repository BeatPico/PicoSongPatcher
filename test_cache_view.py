import sys,json,tempfile,zipfile,unittest,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT.parent/'beat_saber_lab/python_packages'),str(ROOT)]
from cache_view import inspect_cache,compare_library,ORIGINAL_IDS,text_report
from maps import PatcherError

BASE=Path(r'C:\Users\home\Downloads\PICO_Beat_Saber_-MODDED-_1.40.8_OVRport_3.2.2\com.beatgames.beatsaber\main.1716.com.beatgames.beatsaber.obb')
OLD=ROOT/'dist_v2/Pico Song Patcher 1.1/output/PicoSongs_2026-11-01_18-31-19_357409/Android/obb/com.beatgames.beatsaber'/BASE.name

class CacheTests(unittest.TestCase):
    def test_original_and_old_cache_actual_contents(self):
        original=inspect_cache(BASE);self.assertEqual(original['total'],10);self.assertEqual(original['custom'],0)
        self.assertEqual({s['id'] for s in original['songs']},ORIGINAL_IDS)
        before=OLD.stat();cached=inspect_cache(OLD)
        self.assertEqual((cached['total'],cached['custom'],cached['ai'],cached['auto']),(43,33,0,9))
        self.assertFalse(cached['missing_gameplay']);self.assertEqual((before.st_size,before.st_mtime_ns),(OLD.stat().st_size,OLD.stat().st_mtime_ns))
        self.assertIn('Mr. Saxobeat',text_report(cached))
    def test_actual_ai_identification(self):
        result=json.loads((ROOT/'validation_v12/results.json').read_text(encoding='utf-8'))
        inventory=inspect_cache(Path(result['output'])/'Android/obb/com.beatgames.beatsaber'/BASE.name)
        self.assertEqual((inventory['custom'],inventory['ai'],inventory['auto']),(1,1,0))
        self.assertEqual([s['id'] for s in inventory['songs'] if s['custom']],[result['level_id']])
    def test_compare_by_id_and_never_by_title(self):
        cache={'songs':[{'id':'old','title':'Same title','custom':True},{'id':'foreign','title':'Other','custom':True}]}
        entries=[{'level_id':'old','title':'Same title','enabled':False},{'level_id':'new','title':'Same title','enabled':True}]
        before=json.dumps(entries,sort_keys=True)
        result=compare_library(cache,entries)
        self.assertEqual(result['selected_not_in_cache'],['new']);self.assertEqual(result['cached_not_selected'],['old'])
        self.assertEqual(result['cached_not_in_library'],['foreign']);self.assertEqual(before,json.dumps(entries,sort_keys=True))
    def test_missing_gameplay_is_reported(self):
        work=ROOT/'test_work';work.mkdir(exist_ok=True)
        with zipfile.ZipFile(OLD) as source,tempfile.TemporaryDirectory(dir=work) as td:
            path=Path(td)/'menu_only.obb';name=next(n for n in source.namelist() if '/ostvol1_pack_assets_all_' in n)
            with zipfile.ZipFile(path,'w') as target:target.writestr(name,source.read(name))
            result=inspect_cache(path)
            self.assertEqual(len(result['missing_gameplay']),43)
            path.write_bytes(b'not a zip')
            with self.assertRaises(PatcherError):inspect_cache(path)

if __name__=='__main__':unittest.main()
