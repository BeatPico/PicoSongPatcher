"""Regression checks for readable Expert approach and same-hand recovery."""
from types import SimpleNamespace
import unittest

import numpy as np
from ai_engine.full_song import blank, finish
from ai_engine.jump_settings import base_half_jump, jump_settings
from ai_engine.section_pacing import apply_pacing
from ai_engine.stock_patterns import add_stock_patterns
from compare_expert_motion import jump_metrics, native_notes
from neural_autogen import DEFAULT_DIFFICULTIES, GAMEPLAY_REVISION, needs_regeneration


class ExpertMotionTests(unittest.TestCase):
    def test_reaction_time_survives_bpm_and_half_jump_thresholds(self):
        for bpm in (40., 60., 90., 120., 171., 210., 320.):
            for name, expected, speed in (('Expert', 950., 14.), ('ExpertPlus', 750., 17.)):
                fields = jump_settings(name, DEFAULT_DIFFICULTIES[name], bpm)
                measured = jump_metrics(bpm, fields['_noteJumpMovementSpeed'], fields['_noteJumpStartBeatOffset'])
                self.assertAlmostEqual(measured['reaction_ms'], expected, places=4)
                self.assertEqual(measured['njs'], speed)

    def test_legacy_expert_bpm60_had_half_second_reaction(self):
        for speed in (18.25, 19.75):
            self.assertEqual(jump_metrics(60., speed, 0.)['reaction_ms'], 500.)
        self.assertEqual(base_half_jump(60., 17.998), 1.)
        self.assertEqual(base_half_jump(60., 18.), .5)

    def test_lower_three_difficulty_jump_settings_are_preserved(self):
        for name in ('Easy', 'Normal', 'Hard'):
            nps = DEFAULT_DIFFICULTIES[name]
            self.assertEqual(jump_settings(name, nps),
                             {'_noteJumpMovementSpeed': min(20, 10 + nps * 1.5), '_noteJumpStartBeatOffset': 0})

    def test_native_bpm_regions_use_samples_instead_of_menu_tempo(self):
        chart = {'version': '4.0.0', 'colorNotesData': [{'x': 1, 'c': 0, 'd': 1}],
                 'colorNotes': [{'b': 1.}, {'b': 3.}]}
        audio = {'songFrequency': 100, 'bpmData': [{'eb': 2., 'ei': 100}, {'sb': 2., 'eb': 4., 'si': 100, 'ei': 300}]}
        self.assertEqual([n['b'] for n in native_notes(chart, audio, 60)], [.5, 2.])

    def test_pacing_and_added_pairs_respect_fast_hand_recovery(self):
        for difficulty, gap in (('Expert', .20), ('ExpertPlus', .16)):
            chart = blank()
            chart['colorNotes'] = [dict(b=2. + i * .13, c=0, x=1, y=0, d=i % 2) for i in range(100)]
            # A same-hand aligned stack is one swing and must remain legal.
            chart['colorNotes'].insert(1, dict(chart['colorNotes'][0], y=1))
            audio = SimpleNamespace(active=np.ones(30, dtype=bool), audible=np.ones(30, dtype=bool),
                                    accents=np.ones(30), peaks=np.array([]), frame=int)
            apply_pacing(chart, difficulty, audio)
            add_stock_patterns(chart, difficulty, audio)
            finish(chart, 30., hand_flow=True, difficulty=difficulty, audio=audio)
            self.assertEqual(sum(n['b'] == 2. for n in chart['colorNotes']), 2)
            for hand in (0, 1):
                times = sorted({round(n['b'], 5) for n in chart['colorNotes'] if n['c'] == hand})
                self.assertTrue(all(b - a >= gap - 1e-6 for a, b in zip(times, times[1:])))

    def test_existing_v160_generation_is_marked_for_regeneration(self):
        from ai_engine.gameplay_options import normalize_gameplay_options
        song = {'auto_algorithm': __import__('neural_autogen').ALGORITHM, 'ai_model': 'base',
                'ai_gameplay_revision': 'motion-1.5-v10', 'ai_difficulties': DEFAULT_DIFFICULTIES,
                'ai_gameplay_options': normalize_gameplay_options({})}
        self.assertTrue(needs_regeneration(song, {'id': 'base'}, {'walls': False, 'bombs': False}))
        song['ai_gameplay_revision'] = GAMEPLAY_REVISION
        self.assertFalse(needs_regeneration(song, {'id': 'base'}, {'walls': False, 'bombs': False}))


if __name__ == '__main__':
    unittest.main()
