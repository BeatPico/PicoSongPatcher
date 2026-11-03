"""Bounded context windows and exact elapsed-second chart stitching."""
import copy,math
from collections import deque
from itertools import groupby

ARRAYS=('colorNotes','bombNotes','obstacles','sliders','burstSliders','basicBeatmapEvents','colorBoostBeatmapEvents')
OPPOSITE = {0: 1, 1: 0, 2: 3, 3: 2, 4: 7, 7: 4, 5: 6, 6: 5}


def _hand_flow(chart, notes):
    previous, changed = {}, set()
    result = []
    moved = reversed_cuts = blocked = crossovers = 0
    last_crossover = -math.inf
    for index, note in enumerate(notes):
        time, hand, column, row = note['b'], note['c'], note['x'], note['y']
        if column // 2 != hand:
            before = time - notes[index - 1]['b'] if index else math.inf
            after = notes[index + 1]['b'] - time if index + 1 < len(notes) else math.inf
            isolated = min(before, after) >= .55 and time - last_crossover >= 3.
            preferred = 3 - column
            lanes = (preferred, 4 * hand + 1 - preferred)
            if isolated:
                lanes = (column, *lanes)
            for lane in lanes:
                wall = any(w['b'] <= time < w['b'] + w['d'] and w['x'] <= lane < w['x'] + w['w']
                           and w['y'] <= row < w['y'] + w['h'] for w in chart['obstacles'])
                bomb = any(b['x'] == lane and b['y'] == row and abs(b['b'] - time) < .035
                           for b in chart['bombNotes'])
                if not wall and not bomb:
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
            previous[hand] = note
        else:
            previous.pop(hand, None)
        if note['x'] != column:
            changed.add((round(time, 5), hand, column, row))
        result.append(note)
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

def finish(chart,duration,seams=(),easy_intro=False,hand_flow=False):
    for family in ARRAYS:chart[family].sort(key=lambda x:x.get('b',0))
    kept=[];removed=[];last={};opposite={0:1,1:0,2:3,3:2,4:7,7:4,5:6,6:5,8:8};recut=0
    for note in chart['colorNotes']:
        t=note['b'];hand=note['c'];near=any(abs(t-s)<.30 for s in seams)
        if not 1.25<=t<duration-.35:removed.append(note);continue
        previous=last.get(hand)
        if near and previous and t-previous['b']<.12:
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
        kept,changed,flow_report=_hand_flow(chart,kept)
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
