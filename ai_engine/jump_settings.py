"""Approach speed and reaction time, independent of note density and BPM.

Calibrated against Standard charts in 21 OST and 12 official DLC recordings.
DLC median Expert/Expert+ NJS is 14/17; measured reaction time is .939/.712s.
Use slightly more reading time for the generated, less familiar patterns.
"""
import math

EXPERT_APPROACH = {'Expert': (14., .95), 'ExpertPlus': (17., .75)}


def base_half_jump(bpm, njs):
    if not all(math.isfinite(value) and value > 0 for value in (bpm, njs)):
        raise ValueError('BPM and note jump speed must be positive and finite')
    half = 4.
    while njs * 60 / bpm * half > 17.999:
        half /= 2
    return half


def jump_settings(difficulty, requested_nps, bpm=60.):
    """Return Info.dat jump fields; offset is expressed in serialized beats.

    The worker exports elapsed seconds at BPM60. Copying a zero offset from
    real-tempo maps makes NJS > 17.999 halve reading time from 1s to .5s.
    Derive the offset from a target reaction time instead of assuming zero.
    """
    if difficulty in EXPERT_APPROACH:
        njs, reaction = EXPERT_APPROACH[difficulty]
        offset = reaction * bpm / 60 - base_half_jump(bpm, njs)
    else:
        njs, offset = min(20., 10 + requested_nps * 1.5), 0.
    return {'_noteJumpMovementSpeed': njs, '_noteJumpStartBeatOffset': round(offset, 8)}
