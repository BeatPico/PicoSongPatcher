"""Actual CPU generation with all four object combinations, in a test library."""
import json
from pathlib import Path
import sys
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT.parent/'beat_saber_lab/python_packages')]
from library import Library
from maps import convert
from neural_autogen import generate


def main():
    output=ROOT/'validation_objects'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    library=Library(output/'data')
    library.state['ai_model']='finetune-20261102T113535114682Z'
    source=Path(json.loads((ROOT/'validation_training/user-audio-20261102/comparison.json').read_text(encoding='utf-8'))[0]['source'])
    records=[];identifiers=set()
    for walls,bombs in ((False,False),(True,False),(False,True),(True,True)):
        library.state.update(ai_walls=walls,ai_bombs=bombs)
        entry,added=generate(source,library,ROOT/'assets',difficulties={'Easy':1.5},log=lambda line:print(line,flush=True))
        assert added and entry['level_id'] not in identifiers
        identifiers.add(entry['level_id'])
        folder=library.root/entry['folder']
        chart=json.loads((folder/'Easy.dat').read_text(encoding='utf-8'))
        report=json.loads((folder/'AI_analysis.json').read_text(encoding='utf-8'))
        native,lighting,bpm_events=convert(chart)
        assert len(native['obstacles'])==len(chart['obstacles'])
        assert len(native['bombNotes'])==len(chart['bombNotes'])
        assert bool(chart['obstacles'])==walls,(walls,bombs,len(chart['obstacles']))
        assert bool(chart['bombNotes'])==bombs,(walls,bombs,len(chart['bombNotes']))
        assert report['objects_enabled']=={'walls':walls,'bombs':bombs}
        assert abs(report['audio_seconds']-135.20979591836735)<0.03
        repeated,added=generate(source,library,ROOT/'assets',difficulties={'Easy':1.5},log=lambda line:None)
        assert not added and repeated['level_id']==entry['level_id']
        records.append({'walls_enabled':walls,'bombs_enabled':bombs,'level_id':entry['level_id'],
            'notes':len(chart['colorNotes']),'walls':len(chart['obstacles']),'bombs':len(chart['bombNotes']),
            'duration':report['audio_seconds'],'native_version':native['version']})
        (output/'results.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    print('PASS '+str(output),flush=True)


if __name__=='__main__':main()
