"""Bounded context windows and exact elapsed-second chart stitching."""
import copy,math

ARRAYS=('colorNotes','bombNotes','obstacles','sliders','burstSliders','basicBeatmapEvents','colorBoostBeatmapEvents')
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

def finish(chart,duration,seams=()):
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
    chart['colorNotes']=kept
    keys={(round(n['b'],5),n['c'],n['x'],n['y']) for n in kept}
    def attached(a):return (round(a['b'],5),a['c'],a['x'],a['y']) in keys
    chart['sliders']=[a for a in chart['sliders'] if attached(a) and
        (round(a['tb'],5),a['c'],a['tx'],a['ty']) in keys]
    chart['burstSliders']=[a for a in chart['burstSliders'] if attached(a)]
    return {'seam_or_edge_notes_removed':len(removed),'seam_directions_adjusted':recut}
