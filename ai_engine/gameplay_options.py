"""Opt-in movement-object profile for Pico Song Patcher 1.4.

No options (None) preserves legacy worker jobs. An explicit object activates
these defaults. Apply after _difficulty_settings: sporty mode otherwise also
changes note-intensity conditioning in that legacy helper.
"""
from copy import deepcopy
from contextlib import contextmanager
from functools import wraps
import math
import random

GAMEPLAY_REVISION = 'motion-1.5-v10'
DEFAULT_DIFFICULTIES = {'Easy': 1.2, 'Normal': 1.8, 'Hard': 4.0, 'Expert': 5.5, 'ExpertPlus': 6.5}
# Median note rates measured across 12 official DLC maps, in notes per second.
EXPERT_TIMING_TARGETS = {'Expert': 4.12, 'ExpertPlus': 4.54}
DEFAULT_GAMEPLAY_OPTIONS = {
    'wall_modes': ['side', 'crouch'],
    'arc_probability': .35,
    'arc_radius': .5,
    'arc_max_row': 1,
    'bombs_probability': .6,
}


def normalize_gameplay_options(value):
    """Return a detached JSON-safe profile, or None for a legacy job."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('object_options must be an object')
    unknown = set(value) - set(DEFAULT_GAMEPLAY_OPTIONS)
    if unknown:
        raise ValueError('Unknown object_options: ' + ', '.join(sorted(unknown)))
    options = deepcopy(DEFAULT_GAMEPLAY_OPTIONS)
    options.update(deepcopy(value))
    modes = options['wall_modes']
    if not isinstance(modes, (list, tuple)) or not modes or any(mode not in ('side', 'crouch') for mode in modes):
        raise ValueError('wall_modes must contain side and/or crouch')
    options['wall_modes'] = [mode for mode in ('side', 'crouch') if mode in modes]
    for key, low, high in (('arc_probability', 0., 1.), ('arc_radius', .5, 2.),
                           ('bombs_probability', 0., 1.)):
        number = options[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError(key + ' is outside its valid bounds')
        options[key] = float(number)
    row = options['arc_max_row']
    if isinstance(row, bool) or not isinstance(row, int) or not 0 <= row <= 2:
        raise ValueError('arc_max_row must be an integer from 0 to 2')
    return options


def apply_gameplay_options(config, options):
    """Use existing rule-generator controls without editing notes or margins."""
    if options is None:
        return
    config.sporty_obstacles = True
    # The existing sporty generator chooses inner/outer side walls and, when
    # both inner lanes are free, a full-width ceiling requiring a crouch.
    config.slider_probability = options['arc_probability']
    config.slider_radius_multiplier = options['arc_radius']
    config.slider_turbo_start = False
    config.v2_bombs_prob = options['bombs_probability']


def filter_gameplay_objects(state, options):
    """Omit high arcs; never move colored anchors or shorten attached spans.

    Low/middle-row endpoints plus the reduced existing radius control avoid
    tall guidance curves. This is an endpoint-height policy, not a simulator
    of the game's rendered Bezier curve. Existing hazard clearance stays in
    force. Filtering walls only implements optional mode selections.
    """
    if options is None:
        return None
    notes = {note['id']: note for note in state['notes']}
    arcs = state.get('arcs', [])
    accepted = []
    for arc in arcs:
        ids = arc.get('endpoint_ids', [])
        if len(ids) != 2 or any(identity not in notes for identity in ids) or arc.get('synthetic_head') is not None:
            continue
        if any(notes[identity]['y'] > options['arc_max_row'] for identity in ids):
            continue
        accepted.append(arc)
    state['arcs'] = accepted
    walls = state.get('walls', [])
    modes = options['wall_modes']
    state['walls'] = [wall for wall in walls
                      if ('crouch' if wall['y'] == 2 and wall['width'] == 4 else 'side') in modes]
    return {'revision': GAMEPLAY_REVISION, 'removed_high_or_unattached_arcs': len(arcs) - len(accepted),
            'removed_wall_modes': len(walls) - len(state['walls'])}


def space_beginner_hazards(chart, difficulty):
    """Leave a recovery gap after each wall or bomb in the BPM-60 export."""
    gap = {'Easy': 8., 'Normal': 5.}.get(difficulty)
    if gap is None:
        return
    fields = ('obstacles', 'bombNotes')
    hazards = [(item['b'], field, item) for field in fields for item in chart[field]]
    kept = {field: [] for field in fields}
    available = -math.inf
    for time, field, item in sorted(hazards, key=lambda event: event[0]):
        if time >= available:
            kept[field].append(item)
            available = time + (item['d'] if field == 'obstacles' else 0.) + gap
    chart.update(kept)


@contextmanager
def seeded_generation(seed, timing_postprocessor=None):
    """Temporarily seed this single worker/evaluation generation context.

    V2Runtime imports pipeline.generate at call time. Supplying its existing
    rng argument fixes the decoder's otherwise unseeded default_rng without
    changing NumPy globally. Legacy obstacle/arc helpers use Python random,
    so their state and the legacy NumPy state are scoped and restored too.
    Use one context per song variant; explicit caller generators are honored.
    """
    import numpy as np
    from map_creation import v2_pipeline, v2_postprocessing

    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError('Generation seed must be a nonnegative integer')
    seed = int(seed)
    previous = v2_pipeline.generate
    previous_timing = v2_postprocessing.postprocess_timing
    python_state, numpy_state = random.getstate(), np.random.get_state()

    @wraps(previous)
    def generate(*args, **kwargs):
        if kwargs.get('rng') is None:
            kwargs['rng'] = np.random.default_rng(seed)
        return previous(*args, **kwargs)

    random.seed(seed)
    np.random.seed(seed % (2 ** 32))
    v2_pipeline.generate = generate
    if timing_postprocessor is not None:
        v2_postprocessing.postprocess_timing = timing_postprocessor
    try:
        yield
    finally:
        v2_pipeline.generate = previous
        v2_postprocessing.postprocess_timing = previous_timing
        random.setstate(python_state)
        np.random.set_state(numpy_state)
