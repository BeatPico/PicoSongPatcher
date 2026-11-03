"""Extend the local reference set with a bounded number of BeatSaver downloads."""
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys

import numpy as np

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import collect_training_examples as source
from collect_additional_examples import audio_hashes, digest, GENRES

OLD = APP / 'training_examples_40'
ROOT = APP / 'training_examples_70'
NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def describe(item):
    folder = ROOT / item['training_package']
    info = source.read_json(source.locate(folder, 'Info.dat'))
    chart = source.read_json(source.locate(folder, item['charts'][-1]['filename']))
    beats = np.array([n['b'] for n in chart['colorNotes']] if 'colorNotes' in chart else
                     [n['_time'] for n in chart['_notes'] if n['_type'] in (0, 1)])
    integer = abs(beats - np.rint(beats)) < .015
    half = abs(beats * 2 - np.rint(beats * 2)) < .03
    quarter = abs(beats * 4 - np.rint(beats * 4)) < .06
    thirds = abs(beats * 3 - np.rint(beats * 3)) < .045
    grid = dict(zip(('beat', 'half_only', 'quarter_only', 'triplet_only', 'other'),
                    map(float, (integer.mean(), (half & ~integer).mean(),
                                (quarter & ~half).mean(), (thirds & ~quarter).mean(),
                                (~quarter & ~thirds).mean()))))
    audio = source.locate(folder, info['_songFilename'])
    pcm = subprocess.check_output([str(APP / 'assets/tools/ffmpeg.exe'), '-v', 'error',
        '-nostdin', '-ss', '8', '-i', str(audio), '-t', '90', '-ac', '1', '-ar', '11025',
        '-f', 'f32le', 'pipe:1'], creationflags=subprocess.CREATE_NO_WINDOW)
    samples = np.frombuffer(pcm, dtype='<f4')
    frames = np.lib.stride_tricks.sliding_window_view(samples, 8192)[::4096]
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(8192)))
    freq = np.fft.rfftfreq(8192, 1 / 11025)
    bins = (freq >= 55) & (freq <= 2200)
    pitches = np.rint(69 + 12 * np.log2(freq[bins] / 440)).astype(int) % 12
    energy = np.sqrt(spectrum[:, bins]).mean(axis=0)
    chroma = np.bincount(pitches, weights=energy, minlength=12) / np.bincount(pitches, minlength=12)
    profiles = ((6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88),
                (6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17))
    scores = sorted((float(np.corrcoef(chroma, np.roll(template, pitch))[0,1]), pitch, mode)
                    for mode, template in zip(('major', 'minor'), profiles)
                    for pitch in range(12))
    score, pitch, mode = scores[-1]
    return {'bpm': info['_beatsPerMinute'], 'grid': grid, 'grid_difficulty': item['charts'][-1]['difficulty'],
            'estimated_key': NAMES[pitch] + ' ' + mode, 'key_score': score,
            'key_margin': score - scores[-2][0]}


def main():
    source.ROOT = ROOT
    for name in ('api', 'archives', 'raw', 'packages'):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    old_manifest = OLD / 'manifest.json'
    prior = source.read_json(old_manifest)
    old = prior['packages']
    assert len(old) == 40
    for item in old:
        for field in ('archive', 'raw_package', 'training_package'):
            item[field] = str((OLD / item[field]).resolve())
    manifest = ROOT / 'manifest.json'
    resumed = source.read_json(manifest) if manifest.exists() else {}
    selected = resumed.get('packages', [])[40:]
    rejected = resumed.get('skipped', [])
    excluded = {item['id'] for item in old + selected + rejected}
    hashes = {item[k] for item in old + selected for k in ('audio_sha256', 'decoded_sha256')}
    titles = {(item['title'].casefold(), item['artist'].casefold()) for item in old + selected}
    queues = {}
    for genre in GENRES:
        path = OLD / 'api' / ('search_genre_all_dates_' + genre + '.json')
        rows = source.read_json(path)['docs']
        queues[genre] = sorted((r for r in rows if r['id'] not in excluded and source.eligible_diffs(r)),
                              key=lambda r: (-len(source.eligible_diffs(r)), -r['stats']['score']))
    by_genre = Counter(item['selection_genre'] for item in selected)
    by_bpm = Counter(int(item['audio_features']['bpm']) // 30 for item in selected)
    attempts = len(selected) + len(rejected)
    while len(selected) < 30 and attempts < 65:
        available = [(genre, row) for genre, rows in queues.items() for row in rows if row['id'] not in excluded]
        if not available:
            break
        genre, row = min(available, key=lambda pair: (
            by_genre[pair[0]], by_bpm[int(pair[1]['metadata']['bpm']) // 30],
            -len(source.eligible_diffs(pair[1])), -pair[1]['stats']['score']))
        identifier = row['id']
        excluded.add(identifier)
        attempts += 1
        try:
            item = source.download(row)
            identity = (item['title'].casefold(), item['artist'].casefold())
            audio = audio_hashes(ROOT / item['training_package'])
            if identity in titles or any(audio[k] in hashes for k in ('audio_sha256', 'decoded_sha256')):
                raise ValueError('Duplicate recording or song and artist')
            features = describe(item)
            item.update(audio, audio_features=features, selection_genre=genre,
                        beatsaver_tags=row.get('tags') or [], collection='additional_30_for_70')
            selected.append(item)
            by_genre[genre] += 1
            by_bpm[int(features['bpm']) // 30] += 1
            titles.add(identity)
            hashes.update(audio[k] for k in ('audio_sha256', 'decoded_sha256'))
            source.log(f"{len(selected)}/30 {item['title']} [{genre}, {features['estimated_key']}]")
        except Exception as error:
            rejected.append({'id': identifier, 'reason': str(error)})
            source.log(f'Skipped {identifier}: {error}')
        source.save(manifest, {'packages': old + selected, 'new_packages': len(selected),
            'original_manifest': str(old_manifest), 'original_manifest_sha256': digest(old_manifest),
            'skipped': rejected, 'attempt_limit': 65})
    if len(selected) != 30:
        raise RuntimeError(f'Collected {len(selected)}/30 after {attempts} bounded attempts')
    source.log(f'Complete: {attempts} attempts; {sum(x["archive_bytes"] for x in selected)/1024**2:.1f} MiB accepted')


if __name__ == '__main__':
    main()
