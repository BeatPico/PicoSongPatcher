import sys,json,unittest,tempfile,wave,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT.parent/'beat_saber_lab/python_packages'),str(ROOT),str(ROOT/'ai_engine')]
from full_song import windows,blank,append_window,finish
from library import Library,inspect_map
from neural_autogen import generate
from engine import build,OBB

def log(text):print(str(text).encode('ascii','backslashreplace').decode(),flush=True)

class FullSongTests(unittest.TestCase):
    def test_no_duration_limit_and_no_missing_cores(self):
        for duration in (45,240,301,1850,13000):
            plan=windows(duration)
            self.assertEqual(plan[0][2],0);self.assertEqual(plan[-1][3],duration)
            for a,b in zip(plan,plan[1:]):
                self.assertEqual(a[3],b[2]);self.assertGreater(a[1],b[0])
            self.assertAlmostEqual(sum(p[3]-p[2] for p in plan),duration)
    def test_chart_timeline_and_spans_at_seam(self):
        target=blank();chart=blank()
        chart['colorNotes']=[dict(b=9.,x=0,y=1,c=0,d=1),dict(b=10.,x=3,y=0,c=1,d=0)]
        chart['sliders']=[dict(b=9.,tb=11.,x=0,y=1,c=0,d=1,tx=1,ty=0,tc=0,mu=1,tmu=1,m=0)]
        append_window(target,chart,172.,180.,360.)
        self.assertEqual([n['b'] for n in target['colorNotes']],[181.,182.])
        self.assertEqual(target['sliders'][0]['tb'],183.)
        finish(target,400,[180]);self.assertEqual(len(target['sliders']),0)
    def test_join_keeps_nearby_notes_playable(self):
        chart=blank();chart['colorNotes']=[dict(b=179.99,x=0,y=1,c=0,d=1),dict(b=180.01,x=1,y=0,c=0,d=0),dict(b=180.3,x=1,y=0,c=1,d=0)]
        report=finish(chart,400,[180])
        self.assertEqual(len(chart['colorNotes']),2);self.assertEqual(report['seam_or_edge_notes_removed'],1)

def main():
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(FullSongTests)
    assert unittest.TextTestRunner().run(suite).wasSuccessful()
    if '--generate' not in sys.argv:return
    root=ROOT/'validation_v12';root.mkdir(exist_ok=True)
    lib=Library(root/'data')
    source=Path(r'C:\Users\home\Downloads\Alexandra_Stan_-_Mr_Saxobeat.mp3')
    entry,added=generate(source,lib,ROOT/'assets',log=log)
    parsed=inspect_map(lib.root/entry['folder'])
    report=json.loads((lib.root/entry['analysis_file']).read_text(encoding='utf-8'))
    self_duration=entry['full_audio_seconds'];assert self_duration>180
    assert report['cropped_audio'] is False and report['duration_limit'] is None
    for name,cfg,chart,light in parsed['difficulties']:
        times=[n['b'] for n in chart['colorNotes']]
        assert max(times)>self_duration-15,(name,max(times),self_duration)
        assert max(times)<=self_duration
        assert len({tuple(d.get(k,0) for k in ('x','y','d')) for d in chart['colorNotesData']})>=8
        # Every finite-interval report covers the real model's entire recording.
        assert all(t>=0 for t in times)
    again,added=generate(source,lib,ROOT/'assets',log=log)
    assert not added and again['level_id']==entry['level_id']
    (root/'results.json').write_text(json.dumps({'status':'PASS','full_audio_seconds':self_duration,
        'notes':report['difficulties'],'model_revision':report['model_revision'],'level_id':entry['level_id']},indent=2),encoding='utf-8')
    if '--build' in sys.argv:
        base=r'C:\Users\home\Downloads\PICO_Beat_Saber_-MODDED-_1.40.8_OVRport_3.2.2\com.beatgames.beatsaber\main.1716.com.beatgames.beatsaber.obb'
        out=build(base,[entry],lib.root,root/'output',ROOT/'assets',log)
        result=json.loads((root/'results.json').read_text(encoding='utf-8'));result['output']=str(out)
        (root/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    log('FULL AI VALIDATION PASS')
if __name__=='__main__':main()
