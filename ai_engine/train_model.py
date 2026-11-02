"""Warm-start seven stages, validate a candidate and register it for A/B use."""
import hashlib
import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'infernosaber')]
os.chdir(ROOT / 'infernosaber')
for key, value in {'CUDA_VISIBLE_DEVICES': '-1', 'TF_CPP_MIN_LOG_LEVEL': '2',
                   'TF_NUM_INTRAOP_THREADS': '2', 'TF_NUM_INTEROP_THREADS': '2',
                   'OMP_NUM_THREADS': '2', 'MPLBACKEND': 'Agg',
                   'NUMBA_CACHE_DIR': str(ROOT / 'cache/numba')}.items():
    os.environ[key] = value


def log(message):
    print('@training ' + json.dumps({'log': str(message)}, ensure_ascii=True), flush=True)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, record):
    path = Path(path)
    pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    pending.replace(path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def check_splits(beat, v2):
    """Reject group/audio leakage before any optimizer can run."""
    identities = {}
    counts = {}
    for song in beat:
        meta = song.metadata
        split = meta['split']
        counts[split] = counts.get(split, 0) + 1
        for key in ('group_id', 'audio_sha256', 'decoded_sha256'):
            value = meta.get(key)
            if value:
                identity = (key, value)
                if identity in identities and identities[identity] != split:
                    raise ValueError('Recording leakage between splits: ' + key)
                identities[identity] = split
    if not all(counts.get(split) for split in ('train', 'validation', 'test')):
        raise ValueError('Training, validation and test recordings are all required')
    beat_ids = {song.song_id: song.metadata['split'] for song in beat}
    for record in v2.manifest['songs']:
        if beat_ids.get(record['song_id']) != record['split']:
            raise ValueError('Beat and downstream dataset splits disagree')
    return counts


def run(job):
    from ffmpeg_source import install
    install(job['ffmpeg'])
    os.environ['PICO_TRAINING_FFMPEG'] = str(job['ffmpeg'])
    from tools.config import config
    from training.beat_models.artifacts import load_dataset as load_beat
    from training.pipeline_v2.artifacts import load_dataset as load_v2
    from finetune_beats import finetune as train_beat
    from finetune_stages import finetune as train_stage
    source, run_dir = Path(job['source_bundle']).resolve(), Path(job['run']).resolve()
    if source == run_dir or source.is_relative_to(run_dir) or run_dir.is_relative_to(source):
        raise ValueError('Candidate and source bundle must be separate')
    config.v1_lighting_model_path = str(source / 'v1_lighting/tf_event_gen_2_12_5_5__22_18.h5')
    config.v1_lighting_vocabulary_path = str(source / 'v1_lighting/onehot_encoder_events.pkl')
    original = {str(p.relative_to(source)): sha(p) for p in source.rglob('*') if p.is_file()}
    result = {'id': job['id'], 'status': 'training', 'source_bundle': str(source),
              'created_at': datetime.now(timezone.utc).isoformat(), 'stages': {},
              'active_model_changed': False, 'training_device': 'CPU'}
    write(run_dir / 'result.json', result)
    try:
        log('Проверка разделения записей и совместимости данных…')
        songs, manifest = load_beat(job['beat_dataset'])
        downstream = load_v2(job['v2_dataset'])
        result['split_charts'] = check_splits(songs, downstream)
        result['dataset_hashes'] = {key: sha(Path(job[key]) / 'manifest.json') for key in ('beat_dataset', 'v2_dataset')}
        del songs, manifest, downstream
        for stage in ('intensity', 'timing', 'positions', 'arrows', 'multiplier', 'hazards', 'arcs'):
            log('Дообучение: ' + stage)
            trainer = train_beat if stage in ('intensity', 'timing') else train_stage
            dataset = job['beat_dataset'] if stage in ('intensity', 'timing') else job['v2_dataset']
            record = trainer(stage, dataset, source / stage, run_dir / 'stages' / stage,
                             epochs=job['epochs'], learning_rate=job['learning_rate'], seed=job['seed'], log=log)
            result['stages'][stage] = record
            write(run_dir / 'result.json', result)
            import gc
            import tensorflow as tf
            tf.keras.backend.clear_session()
            gc.collect()
        log('Сборка отдельной модели для сравнения…')
        candidate = run_dir / 'bundle'
        shutil.copytree(source, candidate)
        bundle = read(candidate / 'bundle.json')
        for stage, record in result['stages'].items():
            trained = run_dir / 'stages' / stage
            if not (trained / 'best.keras').is_file():
                continue
            # Replace files only inside this new, disposable candidate.
            shutil.copytree(trained, candidate / stage, dirs_exist_ok=True)
            if stage in ('hazards', 'arcs') and sha(trained / 'best.keras') != original.get(stage + '/best.keras'):
                bundle.get('calibrations', {}).pop(stage, None)
                result.setdefault('limitations', []).append(stage + ': pretrained calibration removed after weight update; frozen default threshold used, no test-set fitting')
        # Stale provenance such as the original CPU verification is not evidence
        # for this candidate. Runtime validation below supplies new evidence.
        for key in ('cpu_verification', 'assembly', 'evaluation_summary'):
            bundle.pop(key, None)
        bundle['fine_tuning'] = {'id': job['id'], 'source_bundle_sha256': original['bundle.json'],
                                 'dataset_hashes': result['dataset_hashes'], 'stages': result['stages']}
        for name, record in bundle.get('files', {}).items():
            path = candidate / name
            if path.is_file():
                record['sha256'] = sha(path)
                if 'bytes' in record:
                    record['bytes'] = path.stat().st_size
        write(candidate / 'bundle.json', bundle)
        result['status'] = 'evaluating'
        write(run_dir / 'result.json', result)
        log('Итоговое сравнение на отложенной записи…')
        from evaluate_candidate import evaluate
        result['evaluation'] = evaluate(source, candidate, job['beat_dataset'], job['v2_dataset'],
                                        run_dir / 'evaluation', seed=job['seed'], log=log)
        if any(sha(source / name) != digest for name, digest in original.items()):
            raise RuntimeError('Original model changed during training')
        result.update(status='evaluated', trained=True, original_model_unchanged=True,
                      report=str(run_dir / 'evaluation'), candidate_bundle=str(candidate))
        write(run_dir / 'result.json', result)
        write(run_dir / 'profile.json', {'id': job['id'], 'status': 'evaluated',
            'label': 'Дообученная · ' + job['id'][9:24], 'experimental': True,
            'report': 'evaluation', 'created_at': result['created_at']})
        log('Дообучение и сравнение завершены. Новая модель доступна для выбора; исходная сохранена.')
        return result
    except Exception as error:
        result.update(status='failed', error=str(error))
        write(run_dir / 'result.json', result)
        raise


if __name__ == '__main__':
    try:
        run(read(sys.argv[1]))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
