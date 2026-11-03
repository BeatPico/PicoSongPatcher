"""Warm-start the existing timing and intensity heads with new human references."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'ai_engine'
sys.path[:0] = [str(ROOT), str(ENGINE)]
from train_model import log, read, write
from ffmpeg_source import install
from finetune_beats import evaluate, finetune
from training.beat_models.artifacts import load_dataset, load_statistics, write_dataset
from training.beat_models.data import prepare_songs
from training.beat_models.features import normalize_songs

DATA = ROOT / 'training_examples_pacing'
WORK = DATA / 'experiment'
MODEL = 'beginner-70-152'


def main():
    ffmpeg = ROOT / 'assets/tools/ffmpeg.exe'
    install(ffmpeg)
    os.environ['PICO_TRAINING_FFMPEG'] = str(ffmpeg)
    origin = ROOT.parent / 'Pico152/ai_engine/models/experiments' / MODEL / 'bundle'
    dataset = WORK / 'beat'
    if not (dataset / 'manifest.json').is_file():
        old_path = read(ROOT / 'training_examples_70/preparation_result.json')['beat_dataset']
        old, _ = load_dataset(old_path)
        old = [song for song in old if song.metadata['provenance'].get('difficulty') in ('Easy', 'Normal', 'Hard')]
        refs = read(DATA / 'manifest.json')['packages']
        assert len(refs) == 15
        paths = [DATA / row['training_package'] for row in refs]
        songs, audit = prepare_songs(source_root=paths)
        assert len({s.metadata['group_id'] for s in songs}) == 15, audit['skipped']
        identity = {s.metadata[key] for s in old for key in ('audio_sha256', 'decoded_sha256')}
        split = {path.name: ('validation' if index in (3, 10) else 'test' if index in (6, 13) else 'train')
                 for index, path in enumerate(paths)}
        for song in songs:
            assert not any(song.metadata[key] in identity for key in ('audio_sha256', 'decoded_sha256'))
            song.metadata['split'] = split[Path(song.metadata['provenance']['package_path']).name]
        stats = load_statistics(origin / 'intensity')
        write_dataset(dataset, old + normalize_songs(songs, stats), stats,
                      {'original_splits_preserved': True, 'new_recording_splits': split, 'new_charts': len(songs)})
        log(f'Prepared {len(songs)} new charts and replayed {len(old)} previous charts')
    results = {}
    for stage in ('intensity', 'timing'):
        target = WORK / stage
        results[stage] = {'training': finetune(stage, dataset, origin / stage, target,
                                              epochs=1, learning_rate=1e-5, seed=127, log=log),
                          'before': evaluate(stage, dataset, origin / stage),
                          'after': evaluate(stage, dataset, target)}
        write(WORK / 'result.json', {'source_profile': MODEL, 'stages': results, 'new_recordings': 15})


if __name__ == '__main__':
    main()
