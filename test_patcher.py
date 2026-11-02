import sys,json,tempfile,zipfile,unittest,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LAB=ROOT.parent/'beat_saber_lab'
sys.path.insert(0,str(LAB/'python_packages'))
from maps import convert,audio_metadata,PatcherError
from library import Library,inspect_map

BILLIE=Path(r'C:\Users\home\Downloads\2b59 (Billie Jean - petrofsilvers)')
LUCKY=Path(r'C:\Users\home\Downloads\55654 (feeling lucky - uiop327)')

class Tests(unittest.TestCase):
    def test_v2_preserves_both_charts(self):
        parsed=inspect_map(BILLIE)
        self.assertEqual([d[0] for d in parsed['difficulties']],['Hard','Expert'])
        for name,_,out,_ in parsed['difficulties']:
            src=json.loads((BILLIE/(name+'.dat')).read_text())
            expanded=[dict(_time=n['b'],_lineIndex=out['colorNotesData'][n['i']]['x'],
                _lineLayer=out['colorNotesData'][n['i']]['y'],_type=out['colorNotesData'][n['i']]['c'],
                _cutDirection=out['colorNotesData'][n['i']]['d']) for n in out['colorNotes']]
            self.assertEqual(expanded,src['_notes'])
            self.assertEqual(len(out['obstacles']),len(src['_obstacles']))
    def test_v3_arcs_and_lighting(self):
        parsed=inspect_map(LUCKY);out=parsed['difficulties'][0][2]
        src=json.loads((LUCKY/'ExpertPlusStandard.dat').read_text())
        self.assertEqual(len(out['colorNotes']),777);self.assertEqual(len(out['arcs']),22)
        for a,b in zip(out['arcs'],src['sliders']):
            h,t,d=out['colorNotesData'][a['hi']],out['colorNotesData'][a['ti']],out['arcsData'][a['ai']]
            self.assertEqual((a['hb'],a['tb'],h['x'],t['x'],t['d'],d['m'],d['tm'],d['a']),
                             (b['b'],b['tb'],b['x'],b['tx'],b['tc'],b['mu'],b['tmu'],b['m']))
        light=parsed['difficulties'][0][3]
        self.assertEqual(len(light['basicEvents']),3050)
        for a,b in zip(light['basicEvents'],src['basicBeatmapEvents']):
            d=light['basicEventsData'][a['i']]
            self.assertEqual((a['b'],d['t'],d['i'],d['f']),(b['b'],b['et'],b['i'],b['f']))
    def test_tempo_rounding(self):
        p=inspect_map(LUCKY);data=audio_metadata(p['info']['_beatsPerMinute'],p['events'],4612077)
        for r in data['bpmData']:
            def seconds(beat):
                t=prev=0.;bpm=p['info']['_beatsPerMinute']
                for e in p['events']:
                    if e['b']>beat:break
                    t+=(e['b']-prev)*60/bpm;prev=e['b'];bpm=e['m']
                return t+(beat-prev)*60/bpm
            self.assertLessEqual(abs(seconds(r['sb'])-r['si']/44100),1/44100)
    def test_zip_library_duplicate_and_paths(self):
        temp_root=ROOT/'test_work';temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as td:
            lib=Library(Path(td)/'data');z=Path(td)/'map.zip'
            with zipfile.ZipFile(z,'w') as dst:
                for p in BILLIE.iterdir():dst.write(p,'nested/'+p.name)
            first,added=lib.import_path(z);self.assertTrue(added)
            second,added=lib.import_path(BILLIE);self.assertFalse(added)
            self.assertEqual(first['level_id'],second['level_id']);self.assertEqual(len(lib.state['songs']),1)
            with zipfile.ZipFile(z,'w') as dst:dst.writestr('../escape.txt','bad')
            with self.assertRaises(PatcherError):lib.import_path(z)
            self.assertFalse((Path(td)/'escape.txt').exists())
    def test_modded_object_rejected(self):
        src=json.loads((LUCKY/'ExpertPlusStandard.dat').read_text())
        src['colorNotes'][0]['customData']={'coordinates':[50,40]}
        with self.assertRaises(PatcherError):convert(src)

if __name__=='__main__':unittest.main()
