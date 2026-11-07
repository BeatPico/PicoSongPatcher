"""Bounded context windows and exact elapsed-second chart stitching."""
import copy,math
from bisect import bisect_left, bisect_right
from collections import deque
from itertools import groupby

ARRAYS=('colorNotes','bombNotes','obstacles','sliders','burstSliders','basicBeatmapEvents','colorBoostBeatmapEvents')
OPPOSITE = {0: 1, 1: 0, 2: 3, 3: 2, 4: 7, 7: 4, 5: 6, 6: 5}
HIGH_ROWS = {'Easy': (.03, 4.), 'Normal': (.05, 3.5), 'Hard': (.08, 3.),
             'Expert': (.10, 2.5), 'ExpertPlus': (.12, 2.)}


def same_swing(first, second):
    if (first['c'] != second['c'] or first['d'] != second['d']
            or first.get('a', 0) != second.get('a', 0)
            or abs(first['b'] - second['b']) > .0001):
        return False
    return ((first['d'] in (0, 1) and first['x'] == second['x'] and abs(first['y'] - second['y']) == 1)
            or (first['d'] in (2, 3) and first['y'] == second['y'] and abs(first['x'] - second['x']) == 1))


def crossover_allowed(chart, times, time, difficulty=None):
    left, right = bisect_left(times, time - .0001), bisect_right(times, time + .0001)
    before = time - times[left - 1] if left else math.inf
    after = times[right] - time if right < len(times) else math.inf
    limit = {'Easy': 3, 'Normal': 4, 'Hard': 5, 'Expert': 6, 'ExpertPlus': 7}.get(difficulty, 5)
    return (min(before, after) >= .55
            and bisect_right(times, time + 1.) - bisect_left(times, time - 1.) <= limit
            and not any(w['b'] - .8 <= time <= w['b'] + w['d'] + .8 for w in chart['obstacles'])
            and not any(abs(b['b'] - time) < .8 for b in chart['bombNotes'])
            and not any(a['b'] - .5 <= time <= a['tb'] + .5
                        for a in chart['sliders'] + chart['burstSliders']))


def _lower_high_notes(chart, notes, difficulty, audio):
    if difficulty not in HIGH_ROWS:
        return notes, set(), {}
    fraction, spacing = HIGH_ROWS[difficulty]
    quota = int(len(notes) * fraction)
    high = [i for i, note in enumerate(notes) if note['y'] == 2]
    ranked = sorted(high, key=lambda i: (
        -float(audio.accents[audio.frame(notes[i]['b'])]) if audio is not None else 0., notes[i]['b']))
    retained, high_times = set(), []
    for index in ranked:
        time = notes[index]['b']
        if len(retained) < quota and all(abs(time - other) >= spacing for other in high_times):
            retained.add(index)
            high_times.append(time)
    occupied = {(round(n['b'], 5), n['x'], n['y']) for n in notes}
    result, changed = [], set()
    lowered = dropped = 0
    for index, note in enumerate(notes):
        if note['y'] != 2 or index in retained:
            result.append(note)
            continue
        time, hand, column = note['b'], note['c'], note['x']
        old = (round(time, 5), hand, column, 2)
        for lane, row in ((column, 1), (column, 0), (1 + hand, 1), (1 + hand, 0), (3 * hand, 1), (3 * hand, 0)):
            cell = (round(time, 5), lane, row)
            if (cell in occupied or any(w['b'] <= time < w['b'] + w['d'] and
                    w['x'] <= lane < w['x'] + w['w'] and w['y'] <= row < w['y'] + w['h']
                    for w in chart['obstacles'])
                    or any(b['x'] == lane and b['y'] == row and abs(b['b'] - time) < .035
                           for b in chart['bombNotes'])):
                continue
            note.update(x=lane, y=row)
            occupied.add(cell)
            changed.update((old, (round(time, 5), hand, lane, row)))
            lowered += 1
            result.append(note)
            break
        else:
            changed.add(old)
            dropped += 1
    return result, changed, {'upper_notes_lowered': lowered, 'upper_notes_dropped': dropped,
                             'upper_row_limit': quota}


def _hand_flow(chart, notes, difficulty=None):
    previous, changed = {}, set()
    result = []
    moved = reversed_cuts = blocked = crossovers = 0
    last_crossover = -math.inf
    times = [n['b'] for n in notes]
    at_time, previous_group = None, {}
    for index, note in enumerate(notes):
        time, hand, column, row = note['b'], note['c'], note['x'], note['y']
        if at_time is None or abs(time - at_time) > .0001:
            previous.update(previous_group)
            previous_group = {}
            at_time = time
        if column // 2 != hand:
            isolated = (crossover_allowed(chart, times, time, difficulty)
                        and (time - last_crossover >= 3. or time == last_crossover))
            preferred = 3 - column
            lanes = (preferred, 4 * hand + 1 - preferred)
            if isolated:
                lanes = (column, *lanes)
            for lane in lanes:
                wall = any(w['b'] <= time < w['b'] + w['d'] and w['x'] <= lane < w['x'] + w['w']
                           and w['y'] <= row < w['y'] + w['h'] for w in chart['obstacles'])
                bomb = any(b['x'] == lane and b['y'] == row and abs(b['b'] - time) < .035
                           for b in chart['bombNotes'])
                occupied = any(n is not note and abs(n['b'] - time) < .035
                               and n['x'] == lane and n['y'] == row
                               and (n['x']//2 == n['c'] or isolated) for n in notes)
                if not wall and not bomb and not occupied:
                    note['x'] = lane
                    if lane == column:
                        last_crossover = time
                        crossovers += 1
                    else:
                        moved += 1
                    break
            else:
                blocked += 1
                continue
        prior = previous.get(hand)
        if note['d'] in OPPOSITE:
            # Reverse a running swing. After a pause the hand can reset and
            # the model can start a different axis for the next phrase.
            if prior is not None and time - prior['b'] < 1.:
                direction = OPPOSITE[prior['d']]
                if note['d'] != direction or note.get('a', 0) != prior.get('a', 0):
                    note['d'] = direction
                    note['a'] = prior.get('a', 0)
                    reversed_cuts += 1
                    changed.add((round(time, 5), hand, column, row))
        simultaneous = next((n for n in reversed(result[-3:]) if abs(n['b'] - time) <= .0001 and n['c'] == hand), None)
        if simultaneous is not None and not same_swing(simultaneous, note):
            blocked += 1
            continue
        if note['x'] != column:
            changed.add((round(time, 5), hand, column, row))
        result.append(note)
        previous_group[hand] = note if note['d'] in OPPOSITE else None
    return result, changed, {'moved_to_own_side': moved, 'directions_adjusted': reversed_cuts,
                             'blocked_notes_removed': blocked, 'isolated_crossovers': crossovers}

def windows(duration,core=180.,context=8.):
    if not math.isfinite(duration) or duration<=0:raise ValueError('Invalid audio duration')
    if duration<=300:return [(0.,duration,0.,duration)]
    result=[];start=0.
    while start<duration:
        end=min(duration,start+core)
        result.append((max(0.,start-context),min(duration,end+context),start,end));start=end
    return result

def blank():
    return {'version':'3.3.0','bpmEvents':[],'rotationEvents':[],**{k:[] for k in ARRAYS},
            'lightColorEventBoxGroups':[],'lightRotationEventBoxGroups':[],
            'lightTranslationEventBoxGroups':[],'useNormalEventsAsCompatibleEvents':True}

def append_window(target,chart,offset,core_start,core_end):
    """Input/output BPM is 60; beat times are elapsed seconds, exactly once."""
    for family in ARRAYS:
        for record in chart.get(family,[]):
            obj=copy.deepcopy(record);head=obj.get('b',0)+offset
            if not core_start<=head<core_end:continue
            obj['b']=head
            if 'tb' in obj:
                obj['tb']+=offset
                # A span crossing a seam would refer to an endpoint from another
                # independent prediction. Its colored head is retained separately.
                if obj['tb']>=core_end:continue
            if family=='obstacles':obj['d']=min(obj['d'],core_end-head)
            target[family].append(obj)
    # Carry lighting state into the retained core after discarding warm-up context.
    if core_start>0:
        previous={}
        for event in chart.get('basicBeatmapEvents',[]):
            if event.get('b',0)+offset<core_start:previous[event['et']]=event
        for record in previous.values():target['basicBeatmapEvents'].append(dict(record,b=core_start))

def finish(chart,duration,seams=(),easy_intro=False,hand_flow=False,difficulty=None,audio=None):
    for family in ARRAYS:chart[family].sort(key=lambda x:x.get('b',0))
    kept=[];removed=[];last={};opposite={0:1,1:0,2:3,3:2,4:7,7:4,5:6,6:5,8:8};recut=0
    for note in chart['colorNotes']:
        t=note['b'];hand=note['c'];near=any(abs(t-s)<.30 for s in seams)
        if not 1.25<=t<duration-.35:removed.append(note);continue
        previous=last.get(hand)
        if near and previous and t-previous['b']<.12 and not same_swing(previous,note):
            removed.append(note);continue
        if near and previous and .12<=t-previous['b']<1.0 and note['d']==previous['d'] and note['d']!=8:
            note['d']=opposite[note['d']];recut+=1
        if near and any(abs(n['b']-t)<.035 and (n['x'],n['y'])==(note['x'],note['y']) for n in kept[-4:]):
            removed.append(note);continue
        kept.append(note);last[hand]=note
    before_intro=len(kept)
    if easy_intro and kept:
        end=kept[0]['b']+12.
        recent=deque()
        accepted=[]
        for time,group in groupby(kept,key=lambda n:round(n['b'],5)):
            notes=list(group)
            if time<end:
                while recent and time-recent[0]>=2.:
                    recent.popleft()
                if len(recent)+len(notes)>4 or (recent and time-recent[-1]<.24):
                    continue
                recent.extend([time]*len(notes))
            accepted.extend(notes)
        kept=accepted
    intro_removed=before_intro-len(kept)
    flow_report={}
    changed=set()
    if hand_flow:
        kept,changed,height_report=_lower_high_notes(chart,kept,difficulty,audio)
        kept,flow_changed,flow_report=_hand_flow(chart,kept,difficulty)
        changed.update(flow_changed)
        flow_report.update(height_report)
    chart['colorNotes']=kept
    keys={(round(n['b'],5),n['c'],n['x'],n['y']) for n in kept}
    keys.difference_update(changed)
    def attached(a):return (round(a['b'],5),a['c'],a['x'],a['y']) in keys
    chart['sliders']=[a for a in chart['sliders'] if attached(a) and
        (round(a['tb'],5),a['c'],a['tx'],a['ty']) in keys]
    chart['burstSliders']=[a for a in chart['burstSliders'] if attached(a) and
        (not hand_flow or a['tx']//2==a['c'])]
    return {'seam_or_edge_notes_removed':len(removed),'seam_directions_adjusted':recut,
            'easy_intro_notes_removed':intro_removed,**flow_report}
