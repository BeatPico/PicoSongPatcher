"""Validate/downloaded references and cache model-compatible learning inputs.

Only the frozen audio encoder runs. No optimizer, training or model replacement.
"""
import os,sys,json,hashlib,time
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
APP=Path(__file__).resolve().parent;ENGINE=APP/'ai_engine';BUNDLE=ENGINE/'models/v2_expert'
ROOT=APP/'training_examples'
for key,value in {'CUDA_VISIBLE_DEVICES':'-1','TF_CPP_MIN_LOG_LEVEL':'2',
    'TF_NUM_INTRAOP_THREADS':'2','TF_NUM_INTEROP_THREADS':'2','OMP_NUM_THREADS':'2',
    'MPLBACKEND':'Agg','NUMBA_CACHE_DIR':str(ENGINE/'cache/numba')}.items():os.environ[key]=value
sys.path[:0]=[str(ENGINE),str(ENGINE/'infernosaber')];os.chdir(ENGINE/'infernosaber')
from ffmpeg_source import install
install(APP/'assets/tools/ffmpeg.exe')

def log(message):print(str(message).encode('ascii','backslashreplace').decode(),flush=True)
def main():
    from training.beat_models.data import prepare_songs
    from training.beat_models.features import normalize_songs
    from training.beat_models.artifacts import load_statistics,read_json,write_dataset as write_beat,load_dataset as load_beat
    from training.pipeline_v2.runtime import EncoderFactory
    from training.pipeline_v2.data import prepare_dataset
    from training.pipeline_v2.artifacts import DatasetWriter,load_dataset as load_v2,load_checkpoint_manifest
    from training.pipeline_v2.contracts import validate
    selection=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    assert len(selection['packages'])==10
    sources=[str(ROOT/p['training_package']) for p in selection['packages']]
    model_paths=[BUNDLE/'intensity/best.keras',BUNDLE/'timing/best.keras',BUNDLE/'positions/best.keras',BUNDLE/'arrows/best.keras']
    hash_file=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    model_hashes={str(p.relative_to(BUNDLE)):hash_file(p) for p in model_paths}
    run=ROOT/'prepared'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');run.mkdir(parents=True)
    beat=run/'beat';v2=run/'v2';started=time.perf_counter()
    stats=load_statistics(BUNDLE/'intensity')
    log('Reading all selected Standard charts and complete audio recordings...')
    songs,report=prepare_songs(source_root=sources)
    report['normalization_from']=str(BUNDLE/'intensity')
    log('Beat features accepted: '+str(len(songs))+'; skipped: '+str(len(report['skipped'])))
    songs=normalize_songs(songs,stats)
    write_beat(beat,songs,stats,report);load_beat(beat)
    settings=read_json(BUNDLE/'bundle.json')['data_settings'];vocabulary=read_json(BUNDLE/'vocabularies.json')
    log('Preparing frozen-encoder inputs for positions, directions and other stages...')
    encoder=EncoderFactory(BUNDLE/'encoder/encoder.h5',settings);writer=DatasetWriter(v2)
    def progress(record):
        if record.get('phase')=='examples':log('Prepared chart '+str(record.get('index'))+'/'+str(record.get('total')))
    dataset=prepare_dataset(songs,settings=settings,encoder=encoder,vocabularies=vocabulary,
                            emit=writer.add,collect=False,progress=progress)
    dataset.manifest.update(base_preparation=report,beat_normalization=stats,evaluation_only=False)
    dataset.manifest['preparation_songs']=dataset.manifest.pop('songs',[])
    compatibility={}
    for stage in ('positions','arrows','multiplier','hazards','arcs'):
        try:load_checkpoint_manifest(BUNDLE/stage,stage,dataset);compatibility[stage]='compatible'
        except ValueError as error:compatibility[stage]=str(error)
    writer.finish(dataset.vocabularies,dataset.manifest)
    loaded=load_v2(v2)
    splits={};counts=Counter();hazards=Counter();nps=[]
    for song in loaded.songs:
        validate(song);base=song.base;meta=base.metadata
        splits.setdefault(meta['group_id'],meta['split'])
        counts[meta['split']]+=1
        nps.append(len(base.arrays['retained_indices'])/meta['duration'])
        for key in ('bombs','walls','arcs','chains'):hazards[key]+=len(song.objects.get(key,[]))
    assert len(splits)==10,(len(splits),report['skipped'])
    assert all(hash_file(BUNDLE/name)==digest for name,digest in model_hashes.items())
    result={'status':'prepared_and_validated','packages':len(selection['packages']),'charts':len(loaded.songs),
        'beat_charts':len(songs),'split_recordings':dict(Counter(splits.values())),'split_charts':dict(counts),
        'actual_retained_nps_min':min(nps),'actual_retained_nps_max':max(nps),'object_counts':dict(hazards),
        'beat_dataset':str(beat),'v2_dataset':str(v2),'compatibility_with_existing_heads':compatibility,
        'skipped_beat_charts':report['skipped'],'skipped_v2_charts':dataset.manifest['skipped'],
        'normalization_reused':True,'vocabularies_reused':True,'encoder_only_frozen':True,
        'trained':False,'active_model_changed':False,'minutes':(time.perf_counter()-started)/60}
    (run/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'preparation_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    log('PREPARATION COMPLETE: '+json.dumps({k:v for k,v in result.items() if not k.startswith('skipped')},ensure_ascii=True))
if __name__=='__main__':main()
