"""Accent patterns calibrated on the 30 Standard charts in OST Vol. 1."""
from bisect import bisect_left
from itertools import groupby
import math

from ai_engine.full_song import OPPOSITE, crossover_allowed, same_swing
from ai_engine.section_pacing import RULES


# Counts from the ten stock recordings, independent of any generated chart.
# Notes, onsets, two-hand pairs, dots, crossed two-hand pairs.
OST_COUNTS = {'Easy': (1343, 1226, 117, 31, 6),
              'Normal': (1819, 1665, 154, 39, 11),
              'Hard': (2648, 2421, 223, 68, 27)}


def add_stock_patterns(chart, difficulty, audio):
    if difficulty not in RULES or not chart['colorNotes']:
        return None
    stock_notes, stock_onsets, stock_pairs, stock_dots, stock_crosses = OST_COUNTS.get(difficulty, OST_COUNTS['Hard'])
    notes = sorted(chart['colorNotes'], key=lambda n: n['b'])
    groups = [(time, list(group)) for time, group in groupby(notes, key=lambda n: round(n['b'], 5))]
    times = [time for time, _ in groups]
    anchors = {(round(a['b'], 5), a['c']) for key in ('sliders', 'burstSliders') for a in chart[key]}
    anchors.update((round(a['tb'], 5), a['c']) for a in chart['sliders'])
    quiet, lively, _, hand_gap, _ = RULES[difficulty]

    def safe(time):
        return (not any(w['b'] - .2 <= time <= w['b'] + w['d'] + .2 for w in chart['obstacles'])
                and not any(abs(b['b'] - time) < .4 for b in chart['bombNotes']))

    def room(index):
        time = times[index]
        first = bisect_left(times, time - 2 + 1e-6)
        last = bisect_left(times, time + 2 - 1e-6)
        for start in range(first, index + 1):
            end = min(last, bisect_left(times, times[start] + 2 - 1e-6))
            cap = lively if audio.active[audio.frame(times[start])] else quiet
            if difficulty == 'Easy' and times[start] < times[0] + 12:
                cap = 4
            if 1 + sum(len(group) for _, group in groups[start:end]) > cap:
                return False
        return True

    def separation(index):
        left = times[index] - times[index - 1] if index else math.inf
        right = times[index + 1] - times[index] if index + 1 < len(times) else math.inf
        return min(left, right)

    ranked = sorted(range(len(groups)), key=lambda i: (-audio.accents[audio.frame(times[i])], times[i]))
    added = dots = crossed = 0
    pairs = sum(len(group) == 2 and group[0]['c'] != group[1]['c'] for _, group in groups)
    pair_goal = round(len(groups) * stock_pairs / stock_onsets)
    last_pair = []
    for i in ranked:
        if pairs >= pair_goal:
            break
        time, group = groups[i]
        if len(group) != 1 or separation(i) < hand_gap or not room(i):
            continue
        note = group[0]
        hand = 1 - note['c']
        if (time, note['c']) in anchors or note['x']//2 != note['c'] or any(abs(time - p) < 2. for p in last_pair):
            continue
        before = next((n for _, nearby in reversed(groups[:i]) for n in nearby if n['c'] == hand), None)
        after = next((n for _, nearby in groups[i+1:] for n in nearby if n['c'] == hand), None)
        nearby_cuts = {(n['d'], n.get('a', 0)) for n in (before, after)
                       if n is not None and abs(n['b'] - time) < 1 and n['d'] in OPPOSITE}
        if len(nearby_cuts) > 1:
            continue
        direction, angle = next(iter(nearby_cuts), (0, 0))
        if safe(time):
            group.append({'b': note['b'], 'x': 1 + hand, 'y': 0, 'c': hand, 'd': OPPOSITE[direction], 'a': angle})
            last_pair.append(time)
            pairs += 1
            added += 1

    stacks = sum(len(group) == 2 and same_swing(*group) for _, group in groups)
    stack_goal = round(len(groups) * {'Easy': .025, 'Normal': .035, 'Hard': .04,
                                      'Expert': .045, 'ExpertPlus': .05}[difficulty])
    stack_times = [time for time, group in groups if len(group) == 2 and same_swing(*group)]
    stacked = 0
    for i in ranked:
        if stacks >= stack_goal:
            break
        time, group = groups[i]
        if len(group) != 1 or separation(i) < hand_gap or not room(i) or not safe(time):
            continue
        note = group[0]
        if (note['d'] not in (0, 1) or note.get('a', 0) != 0 or note['y'] not in (0, 1)
                or note['x']//2 != note['c'] or (time, note['c']) in anchors
                or any(abs(time - other) < 8. for other in stack_times)):
            continue
        before = next((n for _, nearby in reversed(groups[:i]) for n in nearby if n['c'] == note['c']), None)
        after = next((n for _, nearby in groups[i+1:] for n in nearby if n['c'] == note['c']), None)
        if any(n is not None and abs(n['b'] - time) < hand_gap for n in (before, after)):
            continue
        group.append(dict(note, y=1-note['y']))
        stack_times.append(time)
        stacks += 1
        stacked += 1

    dot_goal = round((len(notes) + added) * stock_dots / stock_notes)
    existing_dots = sum(n['d'] == 8 for _, group in groups for n in group)
    for i in ranked:
        if existing_dots + dots >= dot_goal:
            break
        time, group = groups[i]
        if len(group) != 1 or separation(i) < .65:
            continue
        note = group[0]
        if note['d'] != 8 and (time, note['c']) not in anchors:
            note.update(d=8, a=0)
            dots += 1

    if difficulty == 'Hard':
        cross_goal = round(len(groups) * stock_crosses / stock_onsets)
        previous = -math.inf
        isolated = [n['b'] for n in notes if n['x']//2 != n['c']]
        note_times = [time for time, group in groups for _ in group]
        for i, (time, group) in enumerate(groups):
            if crossed >= cross_goal:
                break
            if (len(group) != 2 or {n['c'] for n in group} != {0, 1} or separation(i) < .75
                    or time - previous < 14. or any(abs(time - t) < 3. for t in isolated)
                    or not crossover_allowed(chart, note_times, time, difficulty)):
                continue
            if any((time, n['c']) in anchors or n['x']//2 != n['c'] for n in group):
                continue
            for note in group:
                note.update(x=2-note['c'], d=8, a=0)
            previous = time
            crossed += 1
    chart['colorNotes'] = [n for _, group in groups for n in group]
    return {'two_hand_pairs_added': added, 'same_hand_pairs_added': stacked,
            'single_dots_added': dots, 'crossed_pairs': crossed}
