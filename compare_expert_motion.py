"""Read-only comparison of stock and AI Standard charts on elapsed seconds.

Game files/audio stay local; the report contains aggregate timing statistics.
Run with the application's UnityPy environment, --cache OBB --library DATA.
"""
import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
from pathlib import Path
from statistics import median

import UnityPy
from cache_archive import CacheArchive, DIFFICULTIES
from engine import decode_text

STANDARD = -7286399427822119286
DLC = {'pokerface', 'numb', 'americanidiot', 'dynamite',
       'scarymonstersandnicesprites', 'ginandjuice', 'getlucky',
       'dontstopmenow', 'thunder', 'toxic', 'blindinglights', 'wewillrockyou'}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low = int(position)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)


def jump_metrics(bpm, njs, offset):
    if bpm <= 0 or njs <= 0:
        # Old stock menus can request the game's per-difficulty fallback.
        # Do not silently treat an unset speed as zero or invent a value.
        return {'njs': None, 'raw_njs': njs, 'offset_beats': offset,
                'reaction_ms': None, 'jump_distance_m': None}
    beat_seconds = 60 / bpm
    half = 4.
    while njs * beat_seconds * half > 17.999:
        half /= 2
    half = max(.25, half + offset)
    reaction = half * beat_seconds
    return {'njs': njs, 'offset_beats': offset, 'reaction_ms': reaction * 1000,
            'jump_distance_m': 2 * njs * reaction}


def metrics(notes, duration):
    times = sorted(note['b'] for note in notes)
    hands = [sorted({round(n['b'], 5) for n in notes if n.get('c', 0) == color}) for color in (0, 1)]
    gaps = [b - a for hand in hands for a, b in zip(hand, hand[1:])]
    gaps.sort()
    onsets = sorted(set(round(t, 5) for t in times))
    peak = lambda size: max((bisect_left(times, t + size - 1e-6) - i for i, t in enumerate(times)), default=0)
    hand_peak = max((bisect_left(hand, t + 1 - 1e-6) - i for hand in hands for i, t in enumerate(hand)), default=0)
    return {'notes': len(notes), 'nps_song': len(notes) / duration,
            'nps_active': len(notes) / max(1., times[-1] - times[0]) if times else 0,
            'peak_notes_1s': peak(1.), 'peak_notes_2s': peak(2.), 'peak_hand_swings_1s': hand_peak,
            'hand_gap_p05_ms': percentile(gaps, .05) * 1000 if gaps else None,
            'hand_gap_p50_ms': median(gaps) * 1000 if gaps else None,
            'hand_gaps_under_150ms_pct': 100 * bisect_left(gaps, .15 - 1e-6) / max(1, len(gaps)),
            'hand_gaps_under_200ms_pct': 100 * bisect_left(gaps, .2 - 1e-6) / max(1, len(gaps)),
            'simultaneous_extra_notes_pct': 100 * (len(notes) - len(onsets)) / max(1, len(notes))}


def native_notes(chart, audio, bpm):
    # v4 BPM regions map chart beats to audio sample indices. They can change
    # inside one recording; using just the menu BPM would distort swing speed.
    regions = audio.get('bpmData', [])
    starts = [region.get('sb', 0) for region in regions]
    def seconds(beat):
        if not regions:
            return beat * 60 / bpm
        region = regions[max(0, bisect_right(starts, beat) - 1)]
        start = region.get('sb', 0)
        first = region.get('si', 0)
        sample = first + (region['ei'] - first) * (beat - start) / (region['eb'] - start)
        return sample / audio['songFrequency']
    if chart.get('version', '').startswith('4.'):
        rows = [dict(chart['colorNotesData'][n.get('i', 0)], b=n.get('b', 0)) for n in chart['colorNotes']]
    else:
        rows = chart['colorNotes']
    return [dict(n, b=seconds(n.get('b', 0))) for n in rows]


def native_charts(raw, menu):
    env = UnityPy.load(raw)
    objects = {o.path_id: o for o in env.objects}
    level = next(t for o in objects.values() if o.type.name == 'MonoBehaviour'
                 and '_difficultyBeatmapSets' in (t := o.parse_as_dict()))
    standard = next(g for g in level['_difficultyBeatmapSets'] if g['_beatmapCharacteristicSerializedName'] == 'Standard')
    audio = decode_text(objects[level['_audioDataAsset']['m_PathID']])
    previews = next(g for g in menu['_previewDifficultyBeatmapSets']
                    if g['_beatmapCharacteristic']['m_PathID'] == STANDARD)['_previewDifficultyBeatmaps']
    by_rank = {p['_difficulty']: p for p in previews}
    result = []
    for item in standard['_difficultyBeatmaps']:
        rank = item['_difficulty']
        if rank < 2 or rank not in by_rank:
            continue
        preview = by_rank[rank]
        notes = native_notes(decode_text(objects[item['_beatmapAsset']['m_PathID']]), audio, menu['_beatsPerMinute'])
        result.append({'difficulty': DIFFICULTIES[rank], **metrics(notes, menu['_songDuration']),
                       **jump_metrics(menu['_beatsPerMinute'], preview['_noteJumpMovementSpeed'],
                                      preview['_noteJumpStartBeatOffset'])})
    return result


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['kind'], row['difficulty'])].append(row)
    result = {}
    for (kind, difficulty), group in grouped.items():
        stats = {'charts': len(group), 'charts_with_explicit_jump_speed': sum(r.get('njs') is not None for r in group)}
        for key in ('njs', 'reaction_ms', 'nps_song', 'peak_notes_2s', 'peak_hand_swings_1s',
                    'hand_gap_p05_ms', 'hand_gap_p50_ms', 'hand_gaps_under_150ms_pct', 'simultaneous_extra_notes_pct'):
            values = [r[key] for r in group if r.get(key) is not None]
            stats[key] = {name: percentile(values, q) for name, q in (('p10', .1), ('median', .5), ('p90', .9))}
        result.setdefault(kind, {})[difficulty] = stats
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--dlc', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, skipped = [], []
    with CacheArchive(args.cache) as cache:
        inventory = cache.inventory()
        # Early OST packs give an accessible baseline; later OST packs often
        # intentionally contain challenge maps and are listed separately.
        selected = [s for s in inventory['songs'] if not s['custom'] and
                    (s['pack_id'] in ('ostvol1', 'ostvol2', 'ostvol3') or s['id'].lower() in DLC)]
        for song in selected:
            menu = cache.pack(song['menu_path'])['objects'][song['menu_path_id']].parse_as_dict()
            asset = 'assets/BeatmapLevelsData/' + song['id'].lower()
            if asset in cache.names:
                raw = cache.archive.read(asset)
            elif args.dlc and (args.dlc / song['id'].lower()).is_file():
                raw = (args.dlc / song['id'].lower()).read_bytes()
            else:
                skipped.append({'id': song['id'], 'reason': 'Native gameplay file absent'})
                continue
            for chart in native_charts(raw, menu):
                rows.append({'id': song['id'], 'title': song['title'], 'kind': 'official_ost' if song['pack_id'].startswith('ost') else 'official_dlc',
                             'pack': song['pack_id'], 'bpm': menu['_beatsPerMinute'], **chart})
            print('Compared official:', song['title'], flush=True)
    state = json.loads((args.library / 'library.json').read_text(encoding='utf-8'))
    for song in state['songs']:
        if not song.get('autogenerated') or not song.get('enabled'):
            continue
        folder = args.library / song['folder']
        info_path = folder / 'Info.dat'
        if not info_path.is_file():
            skipped.append({'id': song['level_id'], 'reason': 'AI source Info.dat absent'})
            continue
        info = json.loads(info_path.read_text(encoding='utf-8-sig'))
        for group in info['_difficultyBeatmapSets']:
            if group['_beatmapCharacteristicName'] != 'Standard':
                continue
            for item in group['_difficultyBeatmaps']:
                if item['_difficulty'] not in ('Hard', 'Expert', 'ExpertPlus'):
                    continue
                chart = json.loads((folder / item['_beatmapFilename']).read_text(encoding='utf-8-sig'))
                seconds = [dict(n, b=n['b'] * 60 / info['_beatsPerMinute']) for n in chart['colorNotes']]
                rows.append({'id': song['level_id'], 'title': song['title'], 'kind': 'generated',
                             'difficulty': item['_difficulty'], 'bpm': info['_beatsPerMinute'],
                             'revision': song.get('ai_gameplay_revision'),
                             **metrics(seconds, song['full_audio_seconds']),
                             **jump_metrics(info['_beatsPerMinute'], item['_noteJumpMovementSpeed'], item['_noteJumpStartBeatOffset'])})
    report = {'read_only': True, 'timing_units': 'elapsed audio seconds; simultaneous same-hand blocks count as one swing',
              'summary': summarize(rows), 'charts': rows, 'skipped': skipped,
              'jump_formula_source': 'ChroMapper / BSMG, start HJD=4, max half-distance=17.999m, offset then min=.25 beat',
              'limitations': 'Reaction time is computed from map defaults; player jump-distance overrides and headset rendering are not measured.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2), flush=True)
    print('Report:', args.output, 'Skipped:', len(skipped), flush=True)


if __name__ == '__main__':
    main()
