"""Fit AI charts to audible accents and difficulty-specific movement limits."""
from bisect import bisect_left, insort
from itertools import groupby
import wave

import numpy as np
from ai_engine.full_song import same_swing


class AudioPacing:
    def __init__(self, wav_path):
        from scipy.ndimage import uniform_filter1d, maximum_filter1d

        with wave.open(str(wav_path), 'rb') as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise ValueError('Pacing requires mono 16-bit PCM')
            rate = audio.getframerate()
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')[::2]
        hop, size = 256, 1024
        self.step = 2 * hop / rate
        samples = np.pad(samples.astype(np.float32) / 32768., size // 2)
        frames = np.lib.stride_tricks.sliding_window_view(samples, size)[::hop]
        rms, flux = np.empty(len(frames)), np.empty(len(frames))
        previous = None
        window = np.hanning(size)
        for start in range(0, len(frames), 512):
            batch = frames[start:start + 512]
            end = start + len(batch)
            rms[start:end] = np.sqrt(np.mean(batch * batch, axis=1))
            spectrum = np.log1p(12 * np.abs(np.fft.rfft(batch * window, axis=1)))
            flux[start:end] = np.maximum(0, np.diff(spectrum, axis=0, prepend=spectrum[:1] if previous is None else previous)).mean(axis=1)
            previous = spectrum[-1:]
        scale = max(float(np.quantile(flux, .9)), 1e-6)
        self.accents = maximum_filter1d(flux / scale, 9)
        self.audible = rms > max(.0025, float(np.quantile(rms, .9)) * .055)
        energy = uniform_filter1d(np.log1p(rms * 35) + .22 * np.minimum(flux / scale, 3), round(8 / self.step))
        low, high = np.quantile(energy, [.25, .8])
        self.active = energy > (low + .65 * (high - low)) if high - low > .07 else np.ones(len(rms), dtype=bool)
        peaks = (flux == maximum_filter1d(flux, 13)) & (flux > scale * .32) & self.audible
        self.peaks = np.flatnonzero(peaks) * self.step

    def frame(self, time):
        return min(len(self.active) - 1, max(0, round(time / self.step)))


RULES = {
    'Easy': (4, 5, .26, .42, 3.8),
    'Normal': (6, 8, .19, .30, 3.1),
    'Hard': (9, 12, .125, .21, 2.55),
    # Official DLC fast-hand gaps are typically .255s/.186s (5th percentile).
    # Leave room for fast passages without demanding 8 same-hand swings/sec.
    'Expert': (13, 18, .095, .20, 2.25),
    'ExpertPlus': (16, 22, .075, .16, 1.95),
}


def apply_pacing(chart, difficulty, audio, easier_notes=()):
    if difficulty not in RULES or not (chart['colorNotes'] or easier_notes):
        return {'removed': 0, 'gap_notes': 0}
    quiet, lively, minimum, hand_gap, max_gap = RULES[difficulty]
    crouch_max = {'Easy': 1.35, 'Normal': 1.7, 'Hard': 2.1, 'Expert': 2.4, 'ExpertPlus': 2.65}[difficulty]
    shortened_walls = 0
    for wall in chart['obstacles']:
        limit = crouch_max if wall['y'] >= 2 and wall['w'] >= 3 else crouch_max + 1.
        if wall['d'] > limit:
            wall['d'] = limit
            shortened_walls += 1
    original = sorted(chart['colorNotes'], key=lambda note: note['b'])
    intro_end = (original or easier_notes)[0]['b'] + 12
    accepted, by_hand = [], [[], []]

    def fits(time, notes):
        at = bisect_left(accepted, (time,))
        if any(abs(time - row[0]) < minimum - 1e-6 for row in accepted[max(0, at - 1):at + 1]):
            return False
        for note in notes:
            hand = by_hand[note['c']]
            i = bisect_left(hand, time)
            if any(abs(time - other) < hand_gap - 1e-6 for other in hand[max(0, i - 1):i + 1]):
                return False
        nearby = accepted[bisect_left(accepted, (time - 2.,)):bisect_left(accepted, (time + 2.,))]
        for start in [time, *(other for other, _ in nearby if other <= time)]:
            middle = (start + time) / 2
            limit = lively if audio.active[audio.frame(middle)] else quiet
            if difficulty == 'Easy' and start < intro_end:
                limit = 4
            if len(notes) + sum(len(group) for other, group in nearby if start <= other < start + 2 - 1e-6) > limit:
                return False
        return True

    def add(time, notes):
        insort(accepted, (time, notes))
        for note in notes:
            insort(by_hand[note['c']], time)

    def clear(time, hand):
        return (not any(w['b'] - .25 <= time <= w['b'] + w['d'] + .35 for w in chart['obstacles'])
                and not any(abs(b['b'] - time) < .4 for b in chart['bombNotes'])
                and not any(a['c'] == hand and a['b'] < time < a['tb']
                            for a in chart['sliders'] + chart['burstSliders']))

    candidates = []
    for time, group in groupby(original, key=lambda note: round(note['b'], 4)):
        notes = []
        for note in group:
            if len(notes) < 2 and all(
                    (n['c'] != note['c'] and (n['x'], n['y']) != (note['x'], note['y']))
                    or same_swing(n, note) for n in notes):
                notes.append(note)
        frame = audio.frame(time)
        if audio.audible[frame]:
            candidates.append((time, notes))
    for time, notes in sorted(candidates, key=lambda item: (-audio.accents[audio.frame(item[0])], item[0])):
        if fits(time, notes):
            add(time, notes)
    removed = len(original) - sum(len(group) for _, group in accepted)
    filled = 0
    for (left, left_notes), (right, _) in zip(accepted[:], accepted[1:]):
        cursor = left
        while True:
            # A fuller phrase can carry a second accent between the same
            # anchors. Give quiet phrases room, even when they are audible.
            gap = max_gap * (.72 if audio.active[audio.frame(left + 1.)] else 1.)
            if right - cursor <= gap:
                break
            target = cursor + gap * .72
            peaks = audio.peaks[(audio.peaks >= target - .65) & (audio.peaks <= min(right - .6, target + .65))]
            hand = 1 - left_notes[-1]['c']
            prior = next((group[-1] for time, group in reversed(accepted[:bisect_left(accepted, (target,))])
                          if group[-1]['c'] == hand), None)
            direction = 0 if prior and prior['d'] == 1 else 1
            for t in sorted(peaks, key=lambda value: abs(value - target)):
                time = round(float(t), 5)
                if not clear(time, hand):
                    continue
                note = {'b': time, 'x': 1 + hand, 'y': 0, 'c': hand, 'd': direction, 'a': 0}
                if fits(time, [note]):
                    add(time, [note])
                    left, left_notes = time, [note]
                    cursor = time
                    filled += 1
                    break
            else:
                cursor = target
    supplemented = 0
    if easier_notes:
        deficit = len(easier_notes) + 1 - sum(len(group) for _, group in accepted)
        if deficit > 0:
            # Restore shared musical accents only where the denser chart has room.
            for note in sorted(easier_notes, key=lambda n: (-audio.accents[audio.frame(n['b'])], n['b'])):
                time, hand = note['b'], note['c']
                candidate = dict(note, x=1 + hand, y=0)
                if clear(time, hand) and fits(time, [candidate]):
                    add(time, [candidate])
                    supplemented += 1
                    if supplemented == deficit:
                        break
    chart['colorNotes'] = [note for _, group in accepted for note in group]
    return {'removed': removed, 'gap_notes': filled, 'shortened_walls': shortened_walls,
            'active_sections_fraction': float(np.mean(audio.active)),
            'shared_accents_added': supplemented}


def limit_note_count(chart, limit, audio):
    """Thin crowded passages first while retaining the first and last onsets."""
    notes = chart['colorNotes']
    if len(notes) <= limit:
        return 0
    groups = [(time, list(group)) for time, group in groupby(notes, key=lambda n: round(n['b'], 5))]
    remaining = len(notes)
    while remaining > limit:
        index = min(range(1, len(groups) - 1), key=lambda i: (
            groups[i + 1][0] - groups[i - 1][0], audio.accents[audio.frame(groups[i][0])]))
        _, group = groups[index]
        if len(group) > remaining - limit:
            group.pop()
            remaining -= 1
        else:
            groups.pop(index)
            remaining -= len(group)
    chart['colorNotes'] = [note for _, group in groups for note in group]
    return len(notes) - remaining
