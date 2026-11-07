"""Focused source-only profile checks; no models loaded."""
import copy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import random
import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP / 'ai_engine'), str(APP / 'ai_engine/infernosaber')]
from gameplay_options import (GAMEPLAY_REVISION, DEFAULT_GAMEPLAY_OPTIONS,
    normalize_gameplay_options, apply_gameplay_options, filter_gameplay_objects, seeded_generation)


class GameplayOptionsTests(unittest.TestCase):
    def setUp(self):
        previous = Path.cwd()
        os.chdir(APP / 'ai_engine/infernosaber')
        self.addCleanup(os.chdir, previous)

    def test_legacy_job_is_inert(self):
        config = SimpleNamespace(slider_probability=.8, sporty_obstacles=False)
        before = vars(config).copy()
        options = normalize_gameplay_options(None)
        apply_gameplay_options(config, options)
        self.assertEqual(vars(config), before)
        state = {'notes': [], 'arcs': [{'sentinel': True}]}
        self.assertIsNone(filter_gameplay_objects(state, options))
        self.assertEqual(state['arcs'], [{'sentinel': True}])

    def test_profile_detached_validated_and_existing_margins_preserved(self):
        options = normalize_gameplay_options({})
        self.assertEqual(options, DEFAULT_GAMEPLAY_OPTIONS)
        options['wall_modes'].pop()
        self.assertEqual(DEFAULT_GAMEPLAY_OPTIONS['wall_modes'], ['side', 'crouch'])
        config = SimpleNamespace(v2_bomb_note_time_gap=.8, v2_bomb_note_cell_gap=1,
                                 obstacle_time_gap=[.28, .74], add_beat_intensity=95)
        apply_gameplay_options(config, normalize_gameplay_options({}))
        self.assertTrue(config.sporty_obstacles)
        self.assertEqual(config.v2_bombs_prob, .6)
        self.assertEqual(config.slider_probability, .35)
        self.assertEqual(config.slider_radius_multiplier, .5)
        self.assertFalse(config.slider_turbo_start)
        self.assertEqual(config.v2_bomb_note_time_gap, .8)
        self.assertEqual(config.v2_bomb_note_cell_gap, 1)
        self.assertEqual(config.obstacle_time_gap, [.28, .74])
        self.assertEqual(config.add_beat_intensity, 95)
        for invalid in ([], {'bogus': 1}, {'arc_probability': True}, {'arc_probability': float('nan')},
                        {'arc_max_row': 1.0}, {'wall_modes': []}, {'arc_radius': .2}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_gameplay_options(invalid)

    def test_arc_filter_preserves_endpoints_and_every_note(self):
        state = {'notes': [{'id': i, 'time': i + 1., 'x': i % 4, 'y': y, 'hand': 0, 'd': 3}
                           for i, y in enumerate((0, 1, 2))],
                 'arcs': [{'endpoint_ids': [0, 1], 'time': 1., 'end_time': 2., 'mu': .5},
                          {'endpoint_ids': [1, 2], 'time': 2., 'end_time': 3., 'mu': .5},
                          {'endpoint_ids': [0], 'synthetic_head': {'x': 0, 'y': 0, 'd': 3}}],
                 'walls': [{'y': 2, 'width': 4}, {'y': 0, 'width': 1}]}
        before = copy.deepcopy(state)
        report = filter_gameplay_objects(state, normalize_gameplay_options({}))
        self.assertEqual(state['notes'], before['notes'])
        self.assertEqual(state['arcs'], before['arcs'][:1])
        self.assertEqual(state['walls'], before['walls'])
        self.assertEqual(report['removed_high_or_unattached_arcs'], 2)
        filter_gameplay_objects(state, normalize_gameplay_options({'wall_modes': ['crouch']}))
        self.assertEqual(state['walls'], before['walls'][:1])

    def test_scoped_seed_uses_public_rng_and_restores_after_failure(self):
        from map_creation import v2_pipeline
        def draw(*args, **kwargs):
            return (kwargs['rng'].integers(100000), random.randrange(100000), np.random.randint(100000))
        python_state, numpy_state = random.getstate(), np.random.get_state()
        with patch.object(v2_pipeline, 'generate', draw):
            with seeded_generation(127):
                first = v2_pipeline.generate()
            self.assertIs(v2_pipeline.generate, draw)
            with seeded_generation(127):
                second = v2_pipeline.generate()
            self.assertEqual(first, second)
            caller_rng = np.random.default_rng(99)
            with seeded_generation(127):
                self.assertEqual(v2_pipeline.generate(rng=caller_rng)[0], np.random.default_rng(99).integers(100000))
            with self.assertRaises(RuntimeError):
                with seeded_generation(127):
                    raise RuntimeError('generation failed')
            self.assertIs(v2_pipeline.generate, draw)
        self.assertEqual(random.getstate(), python_state)
        current = np.random.get_state()
        self.assertEqual(current[0], numpy_state[0])
        self.assertTrue(np.array_equal(current[1], numpy_state[1]))
        self.assertEqual(current[2:], numpy_state[2:])

    def test_sport_mode_is_applied_after_difficulty_note_settings(self):
        from tools.config import config
        from map_creation.v2_generation import scoped_settings, _difficulty_settings
        with scoped_settings():
            config.sporty_obstacles = False
            _difficulty_settings(6.)
            before = (config.add_beat_intensity, config.jump_speed_offset, config.max_speed)
            apply_gameplay_options(config, normalize_gameplay_options({}))
            self.assertEqual((config.add_beat_intensity, config.jump_speed_offset, config.max_speed), before)

    def test_timing_override_restores_after_failure_and_keeps_original_fallback(self):
        from map_creation import v2_postprocessing
        original = v2_postprocessing.postprocess_timing
        calls = []
        def fallback(*args, **kwargs):
            calls.append('original')
            return []
        def decode(*args, **kwargs):
            return fallback(*args, **kwargs)
        with patch.object(v2_postprocessing, 'postprocess_timing', fallback):
            with self.assertRaises(RuntimeError):
                with seeded_generation(127, decode):
                    self.assertEqual(v2_postprocessing.postprocess_timing(), [])
                    raise RuntimeError('generation failed')
            self.assertIs(v2_postprocessing.postprocess_timing, fallback)
        self.assertIs(v2_postprocessing.postprocess_timing, original)
        self.assertEqual(calls, ['original'])


if __name__ == '__main__':
    unittest.main()
