"""Use a bounded musical-accent decoder when the timing argmax is sparse."""
from bisect import bisect_left, insort

import numpy as np

from ai_engine.section_pacing import RULES


def accent_frames(song, beats, audio, difficulty, requested_nps):
    duration = song.metadata['duration']
    times = song.arrays['frame_times']
    if requested_nps <= 0 or len(times) < 2:
        return []
    _, cap, minimum, _, _ = RULES[difficulty]
    shares = beats['intensity']
    phrase = 8
    starts = np.arange(0, len(shares), phrase)
    ends = np.minimum(starts + phrase, duration)
    mass = .75 * np.add.reduceat(shares, starts) + .25 * (ends - starts) / duration
    totals = np.rint(np.cumsum(mass) * requested_nps * duration / 1.09).astype(int)
    budgets = np.diff(totals, prepend=0)
    accepted, selected = [], []
    frames = set(beats['event_frames'])
    proposed = dict(zip(beats['event_frames'], beats['event_times']))
    for time in audio.peaks:
        frame = min(len(times) - 1, round(time / (times[1] - times[0])))
        proposed[frame] = float(time)
    probabilities = beats['frame_probabilities']
    by_phrase = [[] for _ in starts]
    for frame, time in proposed.items():
        sample = audio.frame(time)
        if not 1.25 <= time < duration - .35 or not audio.audible[sample]:
            continue
        confidence = 1. - float(probabilities[max(0, frame - 4):frame + 5, 0].min())
        accent = min(3., float(audio.accents[sample]))
        by_phrase[int(time // phrase)].append((frame not in frames, -confidence * (1. + accent), time, frame))
    for budget, candidates in zip(budgets, by_phrase):
        for _, _, time, frame in sorted(candidates):
            if budget <= 0:
                break
            index = bisect_left(accepted, time)
            if any(abs(t - time) < minimum for t in accepted[max(0, index - 1):index + 1]):
                continue
            near = accepted[bisect_left(accepted, time - 2.):bisect_left(accepted, time + 2.)]
            if any(sum(start <= t < start + 2. for t in near) >= cap for start in [*near[:bisect_left(near, time)], time]):
                continue
            insort(accepted, time)
            selected.append((frame, time))
            budget -= 1
    return sorted(selected, key=lambda item: item[1])


def postprocess_budget(song, beats, audio_path, logger=None, *, audio, difficulty, target_nps,
                       postprocess_timing):
    if beats['conditioning_nps'] <= 0 or len(beats['event_frames']) >= song.metadata['duration'] * target_nps * .7:
        return postprocess_timing(song, beats, audio_path, logger=logger)
    selected = accent_frames(song, beats, audio, difficulty, target_nps)
    counts = np.zeros_like(beats['counts'])
    frames = np.asarray([frame for frame, _ in selected], dtype=np.int64)
    counts[frames] = np.maximum(1, beats['counts'][frames])
    candidates, report = postprocess_timing(song, {
        **beats, 'event_frames': frames, 'event_times': np.asarray([time for _, time in selected]),
        'counts': counts,
    }, audio_path, logger=logger, skip=('high_difficulty_fill', 'bpm_alignment', 'onset_snap',
                                       'start_end_fill', 'density_fill', 'silence_removal'))
    probabilities = beats['frame_probabilities']
    report.update(raw_events=len(beats['event_frames']), raw_retained_notes=int(beats['counts'].sum()),
                  timing_policy='pico_phrase_accent_budget', budget_nps=target_nps,
                  budgeted_onsets=len(selected),
                  soft_expected_notes=float((probabilities[:, 1] + 2 * probabilities[:, 2]).sum()))
    return candidates, report
