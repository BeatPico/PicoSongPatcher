"""Prepare the expanded 40-song dataset with frozen production preprocessing.

Original ten prepared songs retain exact IDs/splits/arrays. Only the retained
encoder performs inference; no optimizer, fitting, or active model update occurs.
"""
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent
ENGINE = APP / 'ai_engine'
ROOT = APP / 'training_examples_40'
ORIGINAL = APP / 'training_examples/prepared/20261101T192705Z'
BUNDLE = ENGINE / 'models/v2_expert'
if '--expand-70' in sys.argv:
    previous = json.loads((ROOT / 'preparation_result.json').read_text(encoding='utf-8'))
    ORIGINAL = Path(previous['beat_dataset']).parent
    ROOT = APP / 'training_examples_70'
    BUNDLE = ENGINE / 'models/experiments/finetune-20261102T174826024561Z/bundle'
PRIOR_COUNT = 40 if '--expand-70' in sys.argv else 10
TOTAL_COUNT = PRIOR_COUNT + 30
PRIOR_SPLITS = {'train': PRIOR_COUNT - (8 if PRIOR_COUNT == 40 else 2),
                'validation': 4 if PRIOR_COUNT == 40 else 1, 'test': 4 if PRIOR_COUNT == 40 else 1}
EXPECTED_SPLITS = Counter(PRIOR_SPLITS) + Counter(train=24, validation=3, test=3)
for key, value in {'CUDA_VISIBLE_DEVICES': '-1', 'TF_CPP_MIN_LOG_LEVEL': '2',
    'TF_NUM_INTRAOP_THREADS': '2', 'TF_NUM_INTEROP_THREADS': '2',
    'OMP_NUM_THREADS': '2', 'MPLBACKEND': 'Agg',
    'NUMBA_CACHE_DIR': str(ENGINE / 'cache/numba')}.items():
    os.environ[key] = value
sys.path[:0] = [str(ENGINE), str(ENGINE / 'infernosaber')]
os.chdir(ENGINE / 'infernosaber')
from ffmpeg_source import install
install(APP / 'assets/tools/ffmpeg.exe')


def log(value):
    print(str(value).encode('ascii', 'backslashreplace').decode(), flush=True)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def choose_new_splits(songs, vocabulary, read_objects):
    """Fix recording splits from source objects before any model runs.

    Reserve three validation and three test recordings. Use deterministic
    source-object stratification to make scarce walls/bombs/arcs measurable.
    Test predictions/losses never enter this procedure.
    """
    from training.pipeline_v2.targets import _key, wall_template
    known = {_key(template) for template in vocabulary['walls']}
    groups = defaultdict(list)
    for song in songs:
        groups[song.metadata['group_id']].append(song)
    assert len(groups) == 30, 'Each additional choice must be a unique recording'
    support = {}
    for group, members in groups.items():
        counts = Counter()
        for song in members:
            objects, _ = read_objects(song)
            counts['known_walls'] += sum(_key(wall_template(w)) in known for w in objects['walls'])
            counts['bombs'] += len(objects['bombs'])
            counts['arcs'] += len(objects['arcs'])
        support[group] = dict(counts)
    order = sorted(groups, key=lambda group: hashlib.sha256(('expanded40-seed3:' + group).encode()).hexdigest())
    available = set(groups)
    assigned = {}
    for split in ('validation', 'test'):
        covered = Counter()
        for _ in range(3):
            def priority(group):
                s = support[group]
                missing = sum(s.get(k, 0) > 0 and covered[k] == 0 for k in ('known_walls', 'bombs', 'arcs'))
                present = sum(s.get(k, 0) > 0 for k in ('known_walls', 'bombs', 'arcs'))
                return (-missing, -present, order.index(group))
            group = min(available, key=priority)
            assigned[group] = split
            available.remove(group)
            covered.update({key: int(value > 0) for key, value in support[group].items()})
    assigned.update({group: 'train' for group in available})
    for group, members in groups.items():
        for song in members:
            song.metadata['split'] = assigned[group]
    return {'seed': 3, 'method': 'Deterministic recording-group stratification on source object presence before model execution',
        'test_predictions_used': False, 'normalization_fitted': False,
        'scarce_targets': ['known_vocabulary_walls', 'bombs', 'arcs'],
        'new_group_splits': assigned, 'new_group_source_object_counts': support,
        'new_split_recordings': dict(Counter(assigned.values()))}


def main():
    from training.beat_models.data import prepare_songs, prior_splits_from_manifest
    from training.beat_models.features import normalize_songs
    from training.beat_models.artifacts import load_statistics, read_json, write_dataset as write_beat, load_dataset as load_beat
    from training.pipeline_v2.runtime import EncoderFactory
    from training.pipeline_v2.data import prepare_dataset, read_objects, _preparation_record, _preparation_audit
    from training.pipeline_v2.artifacts import DatasetWriter, load_dataset as load_v2, load_checkpoint_manifest
    from training.pipeline_v2.supervision import validate_support

    started = time.perf_counter()
    selection = read_json(ROOT / 'manifest.json')
    assert len(selection['packages']) == TOTAL_COUNT and selection['new_packages'] == 30
    original_selection = Path(selection['original_manifest'])
    assert digest(original_selection) == selection['original_manifest_sha256']
    model_hashes = {str(path.relative_to(BUNDLE)): digest(path)
                    for path in BUNDLE.rglob('*') if path.is_file()}
    run = ROOT / 'prepared' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run.mkdir(parents=True)
    beat, v2 = run / 'beat', run / 'v2'
    statistics = load_statistics(BUNDLE / 'intensity')
    settings = read_json(BUNDLE / 'bundle.json')['data_settings']
    vocabulary = read_json(BUNDLE / 'vocabularies.json')
    original_songs, original_manifest = load_beat(ORIGINAL / 'beat')
    original_splits = prior_splits_from_manifest(ORIGINAL / 'beat')
    assert Counter(original_splits.values()) == PRIOR_SPLITS
    assert original_manifest['normalization'] == statistics
    sources = [str((ROOT / p['training_package']).resolve()) for p in selection['packages'][PRIOR_COUNT:]]
    log('Extracting unmodified beat features for 30 additional recordings...')
    new_songs, report = prepare_songs(source_root=sources)
    assert not report['skipped'], report['skipped']
    assert not report['reserved'], report['reserved']
    all_old_ids = {s.metadata[k] for s in original_songs for k in ('audio_sha256', 'decoded_sha256')}
    new_ids = {s.metadata[k] for s in new_songs for k in ('audio_sha256', 'decoded_sha256')}
    assert not all_old_ids & new_ids, 'Additional data overlaps an original recording'
    split_audit = choose_new_splits(new_songs, vocabulary, read_objects)
    new_songs = normalize_songs(new_songs, statistics)
    songs = list(original_songs) + new_songs
    for song in songs:
        if song.metadata['group_id'] in original_splits:
            assert song.metadata['split'] == original_splits[song.metadata['group_id']]
    report.update(original_dataset=str(ORIGINAL / 'beat'),
        original_preparation=original_manifest.get('preparation'), normalization_from=str(BUNDLE / 'intensity'),
        split_policy=split_audit, success_count=len(songs), group_count=TOTAL_COUNT)
    report['songs'] = [{'song_id': s.song_id, 'group_id': s.metadata['group_id'],
        'split': s.metadata['split'], 'duration': s.metadata['duration'],
        'original_notes': s.metadata['original_colored_note_count'],
        'retained_notes': s.metadata['retained_colored_note_count'],
        'unique_onsets': s.metadata['unique_onset_count']} for s in songs]
    save(run / 'split_audit.json', split_audit)
    log(f'Writing beat dataset: {len(songs)} charts, splits {dict(EXPECTED_SPLITS)}')
    write_beat(beat, songs, statistics, report)
    load_beat(beat)

    writer = DatasetWriter(v2)
    prior_v2 = load_v2(ORIGINAL / 'v2')
    assert prior_v2.vocabularies == vocabulary
    assert prior_v2.manifest['data_settings'] == settings
    records = []
    for song in prior_v2.songs:
        writer.add(song)
        records.append(_preparation_record(song))
    log(f'Preserved {len(records)} original v2 chart inputs; encoding only the new charts on CPU...')
    encoder = EncoderFactory(BUNDLE / 'encoder/encoder.h5', settings)
    def progress(event):
        if event.get('phase') == 'examples':
            log('Prepared new chart ' + str(event['index']) + '/' + str(event['total']))
    dataset = prepare_dataset(new_songs, settings=settings, encoder=encoder,
        vocabularies=vocabulary, emit=writer.add, collect=False, progress=progress)
    records.extend(dataset.manifest.pop('songs', []))
    dataset.manifest.update(base_preparation=report, beat_normalization=statistics,
        evaluation_only=False, preparation_songs=records,
        preparation_audit=_preparation_audit(records, {}, None), song_count=len(records),
        source_original_dataset=str(ORIGINAL / 'v2'), split_policy=split_audit)
    compatibility = {}
    for stage in ('positions', 'arrows', 'multiplier', 'hazards', 'arcs'):
        load_checkpoint_manifest(BUNDLE / stage, stage, dataset)
        compatibility[stage] = 'compatible'
    writer.finish(dataset.vocabularies, dataset.manifest)
    loaded = load_v2(v2)
    groups, chart_counts, object_counts, nps = {}, Counter(), Counter(), []
    for song in loaded.songs:
        meta = song.base.metadata
        groups[meta['group_id']] = meta['split']
        chart_counts[meta['split']] += 1
        nps.append(len(song.base.arrays['retained_indices']) / meta['duration'])
        for key in ('bombs', 'walls', 'arcs', 'chains'):
            object_counts[key] += len(song.objects.get(key, []))
    assert len(groups) == TOTAL_COUNT and Counter(groups.values()) == EXPECTED_SPLITS
    assert len(loaded.songs) == len(songs), dataset.manifest['skipped']
    assert all(digest(BUNDLE / path) == value for path, value in model_hashes.items())
    assert digest(original_selection) == selection['original_manifest_sha256']
    support = dataset.manifest['preparation_audit']['support']
    sparse_support = {}
    for stage in ('hazards', 'arcs'):
        try:
            validate_support(support[stage], stage)
            sparse_support[stage] = 'train_and_validation_positive_and_negative_support'
        except ValueError as error:
            sparse_support[stage] = str(error)
    result = {'status': 'prepared_and_validated', 'packages': TOTAL_COUNT,
        'charts': len(loaded.songs), 'beat_charts': len(songs),
        'split_recordings': dict(Counter(groups.values())), 'split_charts': dict(chart_counts),
        'actual_retained_nps_min': min(nps), 'actual_retained_nps_max': max(nps),
        'object_counts': dict(object_counts), 'beat_dataset': str(beat), 'v2_dataset': str(v2),
        'compatibility_with_existing_heads': compatibility, 'sparse_stage_support': sparse_support,
        'stage_support': support, 'skipped_beat_charts': report['skipped'],
        'duplicate_beat_charts': report['duplicates'], 'preparation_warnings': report['warnings'],
        'skipped_v2_charts': dataset.manifest['skipped'], 'normalization_reused': True,
        'vocabularies_reused': True, 'encoder_only_frozen': True,
        'original_ten_arrays_and_splits_preserved': True,
        'source_model_unchanged': True, 'original_selection_unchanged': True,
        'source_model_sha256': model_hashes, 'trained': False,
        'active_model_changed': False, 'minutes': (time.perf_counter() - started) / 60}
    save(run / 'summary.json', result)
    save(ROOT / 'preparation_result.json', result)
    log('PREPARATION COMPLETE: ' + json.dumps({k: v for k, v in result.items()
        if k not in ('stage_support', 'source_model_sha256')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
