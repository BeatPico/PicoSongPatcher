"""Isolated CPU inference. Run with the bundled Python, never system Python."""
import os,sys,json,math,subprocess,time,traceback,wave
from pathlib import Path
from contextlib import nullcontext
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'infernosaber'),str(ROOT.parent)]
os.chdir(ROOT/'infernosaber')
for key,value in {'CUDA_VISIBLE_DEVICES':'-1','TF_CPP_MIN_LOG_LEVEL':'2',
    'TF_NUM_INTRAOP_THREADS':'2','TF_NUM_INTEROP_THREADS':'2','OMP_NUM_THREADS':'2',
    'HF_HUB_OFFLINE':'1','GRADIO_ANALYTICS_ENABLED':'False','MPLBACKEND':'Agg',
    'NUMBA_CACHE_DIR':str(ROOT/'cache/numba'),'PYTHONUTF8':'1'}.items():os.environ[key]=value

def emit(text):print('@pico '+json.dumps({'log':str(text)},ensure_ascii=True),flush=True)
def run(job):
    from ffmpeg_source import install
    install(job['ffmpeg'])
    from tools.config import config
    from map_creation.v2_generation import configure_inference,scoped_settings,_difficulty_settings
    from map_creation.v2_runtime import V2Runtime
    from map_creation.v2_export import serialize_beatmap
    from full_song import windows,blank,append_window,finish
    from section_pacing import AudioPacing, apply_pacing
    import numpy as np
    from gameplay_options import (normalize_gameplay_options, apply_gameplay_options, filter_gameplay_objects,
                                  seeded_generation, space_beginner_hazards, GAMEPLAY_REVISION)
    gameplay=normalize_gameplay_options(job.get('object_options'))
    seed=int(job.get('seed',127))
    np.random.seed(seed)
    config.random_seed=seed
    for key in list(vars(config)):
        if key.endswith('_random_seed'):setattr(config,key,seed)
    from profiles import resolve_profile
    profile=resolve_profile(ROOT,job.get('model_profile','base'))
    if profile['id']!='base' and job.get('model_revision')!=profile['revision']:
        raise ValueError('AI-модель изменилась после запуска задания')
    bundle=profile['bundle']
    config.v2_model_bundle_path=str(bundle)
    config.v1_lighting_model_path=str(bundle/'v1_lighting/tf_event_gen_2_12_5_5__22_18.h5')
    config.v1_lighting_vocabulary_path=str(bundle/'v1_lighting/onehot_encoder_events.pkl')
    config.v2_arrows_variety=0.;config.v2_inference_threads=2;config.v2_timing_batch_size=8
    config.v2_calm_effects=False
    config.add_bombs_flag=bool(job.get('bombs',False))
    config.add_obstacle_flag=bool(job.get('walls',False))
    config.add_slider_flag=True
    config.bs_mapping_version='v3';config.create_expert_flag=False
    configure_inference();emit('Загрузка обученных моделей…')
    emit('Объекты: стены '+('вкл.' if config.add_obstacle_flag else 'выкл.')+', бомбы '+('вкл.' if config.add_bombs_flag else 'выкл.'))
    runtime=V2Runtime(bundle)
    with wave.open(job['audio'],'rb') as wav:duration=wav.getnframes()/wav.getframerate()
    plan=windows(duration);charts={name:blank() for name in job['difficulties']};reports=[]
    work=Path(job['output']);work.mkdir(parents=True,exist_ok=True)
    for i,(start,end,core_start,core_end) in enumerate(plan,1):
        audio=Path(job['audio'])
        if len(plan)>1:
            audio=work/f'context_{i}.wav'
            command=[job['ffmpeg'],'-v','error','-nostdin','-i',job['audio'],'-ss',str(start),'-t',str(end-start),
                '-ac','1','-ar','44100','-c:a','pcm_s16le',str(audio),'-y']
            subprocess.run(command,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        emit(f'Анализ записи: часть {i}/{len(plan)} ({core_start:.0f}–{core_end:.0f} с)')
        try:prepared=runtime.prepare_audio(audio,job.get('bpm'))
        except ValueError as error:
            # Quiet/ambient sections have no stable pulse. A neutral conditioning
            # tempo enables the trained silence/intensity model without inventing
            # beats. Serialization still uses actual elapsed seconds.
            if 'No positive tempo' not in str(error):raise
            prepared=runtime.prepare_audio(audio,120.)
            emit('В этом фрагменте нет устойчивого BPM: используется анализ акцентов.')
        for difficulty_index,(name,nps) in enumerate(job['difficulties'].items()):
            emit(f'{name}: моменты ударов, рисунок рук и направления…')
            with scoped_settings():
                _difficulty_settings(4*float(nps))
                apply_gameplay_options(config,gameplay)
                variant_seed=(seed+1000003*(i-1)+1009*difficulty_index)%(2**32)
                with seeded_generation(variant_seed) if gameplay is not None else nullcontext():
                    result=runtime.generate(prepared,float(nps),logger=lambda message:None)
                object_report=filter_gameplay_objects(result.state,gameplay)
                if object_report is not None:
                    object_report['seed']=variant_seed
                    result.diagnostics['gameplay_objects']=object_report
                    result.diagnostics['final_counts'].update(arcs=len(result.state['arcs']),walls=len(result.state['walls']))
                # The network conditions on the real local pulse rate; exported
                # BPM60 makes source elapsed seconds independent of that rate.
                chart,omissions=serialize_beatmap(result.state,60.,version='v3')
                append_window(charts[name],chart,start,core_start,core_end)
                reports.append({'part':i,'difficulty':name,'requested_nps':nps,
                    'estimated_bpm':prepared.song.metadata['bpm'],
                    'notes_before_crop':len(chart['colorNotes']),
                    'diagnostics':result.diagnostics,'omissions':omissions})
        if len(plan)>1:audio.unlink()
    seams=[p[2] for p in plan[1:]];finishes={}
    pacing=AudioPacing(job['audio']) if gameplay is not None else None
    for name,chart in charts.items():
        timing_report=apply_pacing(chart,name,pacing) if pacing is not None else None
        finishes[name]=finish(chart,duration,seams)
        if timing_report is not None:
            finishes[name]['section_pacing']=timing_report
        if gameplay is not None:
            space_beginner_hazards(chart, name)
        if len(chart['colorNotes'])<4:raise ValueError(name+': запись слишком тихая или не содержит подходящих музыкальных акцентов')
        (work/(name+'.dat')).write_text(json.dumps(chart,separators=(',',':'),allow_nan=False),encoding='utf-8')
    def ordinary(value):
        if hasattr(value,'tolist'):return value.tolist()
        if isinstance(value,Path):return str(value)
        return str(value)
    report={'backend':'InfernoSaber v2','audio_seconds':duration,'serialized_bpm':60.,'chunks':len(plan),
        'cropped_audio':False,'duration_limit':None,'difficulties':{n:len(c['colorNotes']) for n,c in charts.items()},
        'parts':reports,'stitching':finishes,'model_revision':job.get('model_revision','e0f4c8b53f4c52fc30c5b66fd7384c0ce85884ff'),
        'model_profile':profile['id'],'model_label':profile['label'],
        'gameplay_revision':GAMEPLAY_REVISION if gameplay is not None else None,'object_options':gameplay,
        'objects_enabled':{'walls':bool(job.get('walls',False)),'bombs':bool(job.get('bombs',False))},
        'objects':{name:{'walls':len(chart['obstacles']),'bombs':len(chart['bombNotes']),
                         'arcs':len(chart['sliders'])} for name,chart in charts.items()}}
    (work/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=ordinary,allow_nan=False),encoding='utf-8')
    emit('Нейросетевые карты готовы: '+', '.join(n+' '+str(len(c['colorNotes'])) for n,c in charts.items()))
    for name,counts in report['objects'].items():emit(name+': стен — '+str(counts['walls'])+', бомб — '+str(counts['bombs']))
    return report

if __name__=='__main__':
    try:
        job=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'));run(job)
    except Exception as error:
        traceback.print_exc();emit('Ошибка AI: '+str(error));sys.exit(1)
