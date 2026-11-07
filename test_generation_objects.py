import unittest
from types import SimpleNamespace
from neural_autogen import ALGORITHM, DEFAULT_DIFFICULTIES, needs_regeneration, object_options, current_gameplay_options, GAMEPLAY_REVISION
from ai_engine.gameplay_options import space_beginner_hazards
from ai_engine.full_song import blank, finish
from song_names import clean_song_name


class GenerationObjectsTest(unittest.TestCase):
    def test_stock_patterns_generate_safe_pairs_and_dots(self):
        from ai_engine.stock_patterns import add_stock_patterns
        from maps import convert
        for difficulty in ('Easy', 'Normal', 'Hard'):
            chart = blank()
            chart['colorNotes'] = [dict(b=2.+i, c=i%2, x=1+i%2, y=0, d=1) for i in range(80)]
            audio = SimpleNamespace(active=[True]*90, accents=[1.]*90, frame=int)
            result = add_stock_patterns(chart, difficulty, audio)
            self.assertGreater(result['two_hand_pairs_added'], 0)
            self.assertGreater(result['single_dots_added'], 0)
            self.assertEqual(bool(result['crossed_pairs']), difficulty == 'Hard')
            converted, _, _ = convert(chart)
            self.assertEqual(len(converted['colorNotes']), len(chart['colorNotes']))

    def test_crossovers_need_space_on_both_sides_and_a_cooldown(self):
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=c, x=x, y=0, d=1) for t, c, x in (
            (2., 0, 1), (2.25, 1, 1), (2.5, 0, 1), (4., 1, 1),
            (5., 0, 2), (7.5, 0, 2), (8.1, 1, 2))]
        report = finish(chart, 10., hand_flow=True)
        self.assertEqual([n['b'] for n in chart['colorNotes'] if n['x']//2 != n['c']], [4., 7.5])
        self.assertEqual(report['isolated_crossovers'], 2)

    def test_crossovers_are_moved_home_during_hazards_and_dense_phrases(self):
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=0, x=2, y=0, d=1) for t in (2., 5., 8.)]
        chart['bombNotes'] = [dict(b=2.7, x=0, y=2)]
        chart['obstacles'] = [dict(b=4., d=1., x=0, y=2, w=4, h=3)]
        chart['colorNotes'] += [dict(b=t, c=1, x=3, y=0, d=1) for t in (7.1, 7.3, 7.4, 8.6)]
        finish(chart, 12., hand_flow=True, difficulty='Normal')
        self.assertTrue(all(n['x']//2 == n['c'] for n in chart['colorNotes']))

    def test_same_hand_stack_is_one_swing_and_next_swing_reverses(self):
        from maps import convert
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=1, x=2, y=y, d=d)
                               for t, y, d in ((2., 0, 0), (2.7, 1, 1), (2.7, 0, 1), (3.4, 0, 1))]
        finish(chart, 8., hand_flow=True)
        self.assertEqual([n['d'] for n in chart['colorNotes']], [0, 1, 1, 0])
        converted, _, _ = convert(chart)
        self.assertEqual(len(converted['colorNotes']), 4)

    def test_generated_same_hand_pairs_survive_final_cleanup(self):
        from ai_engine.full_song import same_swing
        from ai_engine.stock_patterns import add_stock_patterns
        for difficulty in DEFAULT_DIFFICULTIES:
            chart = blank()
            chart['colorNotes'] = [dict(b=2.+i, c=i%2, x=1+i%2, y=0, d=1) for i in range(80)]
            audio = SimpleNamespace(active=[True]*90, accents=[1.]*90, frame=int)
            report = add_stock_patterns(chart, difficulty, audio)
            self.assertGreater(report['same_hand_pairs_added'], 0)
            finish(chart, 90., hand_flow=True, difficulty=difficulty, audio=audio)
            pairs = [(a,b) for a,b in zip(chart['colorNotes'], chart['colorNotes'][1:]) if same_swing(a,b)]
            self.assertGreater(len(pairs), 0)

    def test_upper_row_is_rare_and_lowered_notes_do_not_overlap(self):
        chart = blank()
        chart['colorNotes'] = [dict(b=2.+i*.5, c=i%2, x=1+i%2, y=2, d=1) for i in range(100)]
        finish(chart, 60., hand_flow=True, difficulty='Easy')
        notes = chart['colorNotes']
        self.assertEqual(len(notes), 100)
        self.assertLessEqual(sum(n['y'] == 2 for n in notes), 3)
        self.assertEqual(len({(n['b'], n['x'], n['y']) for n in notes}), 100)

    def test_pacing_retains_aligned_same_hand_stacks(self):
        import numpy as np
        from ai_engine.section_pacing import apply_pacing
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=1, x=2, y=y, d=1)
                               for t, y in ((2., 1), (2., 0), (4., 0), (6., 0))]
        audio = SimpleNamespace(active=np.ones(10, dtype=bool), audible=np.ones(10, dtype=bool),
                                accents=np.ones(10), peaks=np.array([]), frame=int)
        apply_pacing(chart, 'Normal', audio)
        self.assertEqual(len([n for n in chart['colorNotes'] if n['b'] == 2.]), 2)

    def test_running_swings_reverse_per_hand_and_stay_on_own_side(self):
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=c, x=x, y=0, d=d) for t, c, x, d in (
            (2., 0, 2, 1), (2., 1, 1, 4), (2.5, 0, 3, 1), (2.5, 1, 0, 4), (4., 0, 0, 3))]
        chart['sliders'] = [dict(b=2., tb=2.5, c=0, x=2, y=0, tx=3, ty=0)]
        report = finish(chart, 10., hand_flow=True)
        self.assertEqual([(n['x'], n['d']) for n in chart['colorNotes']], [(1, 1), (2, 4), (0, 0), (3, 7), (0, 3)])
        self.assertEqual(chart['sliders'], [])
        self.assertEqual(report['directions_adjusted'], 2)

    def test_section_pacing_caps_finale_and_fills_only_audible_accent(self):
        import numpy as np
        from ai_engine.section_pacing import apply_pacing
        chart = blank()
        times = [2., 3., 4., 10., *[14 + i * .1 for i in range(15)]]
        chart['colorNotes'] = [dict(b=t, c=i % 2, x=1 + i % 2, y=0, d=1) for i, t in enumerate(times)]
        audio = SimpleNamespace(active=np.ones(20, dtype=bool), audible=np.ones(20, dtype=bool),
                                accents=np.ones(20), peaks=np.array([6.2]), frame=int)
        result = apply_pacing(chart, 'Easy', audio)
        beats = [n['b'] for n in chart['colorNotes']]
        self.assertEqual(result['gap_notes'], 1)
        self.assertIn(6.2, beats)
        self.assertLessEqual(max(sum(t <= v < t + 2 for v in beats) for t in beats), 5)

    def test_section_pacing_retries_an_audible_break_after_wall(self):
        import numpy as np
        from ai_engine.section_pacing import apply_pacing
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=0, x=1, y=0, d=1) for t in (2., 10.)]
        chart['obstacles'] = [dict(b=2.5, d=7., x=0, y=2, w=4, h=3)]
        audio = SimpleNamespace(active=np.ones(15, dtype=bool), audible=np.ones(15, dtype=bool),
                                accents=np.ones(15), peaks=np.array([3.4, 4.7, 6.1, 7.5]), frame=int)
        result = apply_pacing(chart, 'Hard', audio)
        self.assertEqual(result['shortened_walls'], 1)
        self.assertGreater(result['gap_notes'], 0)
        self.assertTrue(all(n['b'] >= 4.95 for n in chart['colorNotes'][1:]))

    def test_sparse_normal_uses_safe_accents_from_easy(self):
        import numpy as np
        from ai_engine.section_pacing import apply_pacing
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=0, x=1, y=0, d=1) for t in (2., 5., 8., 10.)]
        chart['bombNotes'] = [{'b': 4.}]
        easier = [dict(b=t, c=1, x=2, y=0, d=1) for t in (2., 3., 4., 5., 6., 8.)]
        audio = SimpleNamespace(active=np.ones(12, dtype=bool), audible=np.ones(12, dtype=bool),
                                accents=np.ones(12), peaks=np.array([]), frame=int)
        report = apply_pacing(chart, 'Normal', audio, easier)
        self.assertEqual(report['shared_accents_added'], 2)
        self.assertEqual([n['b'] for n in chart['colorNotes']], [2., 3., 5., 6., 8., 10.])
        self.assertEqual(easier[1], dict(b=3., c=1, x=2, y=0, d=1))

    def test_easy_density_fallback_keeps_ends_and_thins_busy_passage(self):
        from ai_engine.section_pacing import limit_note_count
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=0, x=1, y=0, d=1) for t in (2., 2.4, 2.8, 6., 9.)]
        audio = SimpleNamespace(accents=[1.]*12, frame=int)
        self.assertEqual(limit_note_count(chart, 4, audio), 1)
        self.assertEqual([n['b'] for n in chart['colorNotes']], [2., 2.8, 6., 9.])

    def test_sparse_timing_budget_respects_silence_and_phrase_spacing(self):
        import numpy as np
        from ai_engine.timing_budget import accent_frames

        times = np.arange(0., 16., .1)
        song = SimpleNamespace(metadata={'duration': 16.}, arrays={'frame_times': times})
        beats = {'event_frames': np.array([], dtype=int), 'event_times': np.array([]),
                 'intensity': np.full(16, 1 / 16), 'frame_probabilities': np.tile([.9, .09, .01], (len(times), 1))}
        peaks = np.array([2., 2.2, 2.5, 4., 6., 7.9, 8.1, 10., 14.])
        audio = SimpleNamespace(peaks=peaks, accents=np.ones(16),
                                audible=[True] * 8 + [False] * 8, frame=int)
        selected = [time for _, time in accent_frames(song, beats, audio, 'Easy', 1.5)]
        self.assertEqual(selected, [2., 2.5, 4., 6., 7.9])
        self.assertEqual(accent_frames(song, beats, audio, 'Easy', 0.), [])

    def test_easy_intro_limits_bursts_without_leaving_an_orphaned_arc(self):
        chart = blank()
        chart['colorNotes'] = [dict(b=t, c=0, x=1, y=0, d=1) for t in (2., 2.1, 2.5, 3., 3.5, 3.8, 16.)]
        chart['sliders'] = [dict(b=2., tb=2.1, c=0, x=1, y=0, tx=1, ty=0)]
        finish(chart, 20., easy_intro=True)
        self.assertEqual([n['b'] for n in chart['colorNotes']], [2., 2.5, 3., 3.5, 16.])
        self.assertEqual(chart['sliders'], [])

    def test_song_names_repair_only_when_confirmed_by_filename(self):
        title = 'Где ты (feat. D.I.P. Project)'
        broken = title.encode('utf-16-be').decode('utf-16-le')
        self.assertEqual(clean_song_name(broken, 'Инфинити - Где ты (hitmos.fm).mp3'), title)
        self.assertEqual(clean_song_name(broken, 'another song.mp3'), ' '.join(broken.split()))
        self.assertEqual(clean_song_name('  Е\u0308лка (hitmos.fm) '), 'Ёлка')
        self.assertEqual(clean_song_name('夜に駆ける', '夜に駆ける.mp3'), '夜に駆ける')

    def test_beginner_hazards_share_a_recovery_gap_after_wall_end(self):
        wall = {'b': 1., 'd': 3.}
        chart = {'obstacles': [wall], 'bombNotes': [{'b': 11.}, {'b': 12.}], 'colorNotes': [{'b': 7.}]}
        space_beginner_hazards(chart, 'Easy')
        self.assertEqual(chart, {'obstacles': [wall], 'bombNotes': [{'b': 12.}], 'colorNotes': [{'b': 7.}]})

    def test_current_maps_remain_current_with_objects_disabled(self):
        song={'auto_algorithm':ALGORITHM,'ai_model':'base','ai_gameplay_revision':GAMEPLAY_REVISION,
              'ai_difficulties':DEFAULT_DIFFICULTIES,
              'ai_gameplay_options':current_gameplay_options(SimpleNamespace(state={}))}
        self.assertFalse(needs_regeneration(song,{'id':'base'},{'walls':False,'bombs':False}))
        song['ai_difficulties'] = {'Easy':1.2,'Normal':1.8,'Hard':4.0}
        self.assertTrue(needs_regeneration(song,{'id':'base'},{'walls':False,'bombs':False}))

    def test_each_object_toggle_requires_a_new_map(self):
        profile={'id':'trained','revision':'same-weights'}
        for walls,bombs in ((False,False),(True,False),(False,True),(True,True)):
            selected={'walls':walls,'bombs':bombs}
            song={'auto_algorithm':ALGORITHM,'ai_model':'trained','ai_model_revision':'same-weights','ai_objects':selected,
                  'ai_difficulties':DEFAULT_DIFFICULTIES,
                  'ai_gameplay_revision':GAMEPLAY_REVISION,'ai_gameplay_options':current_gameplay_options(SimpleNamespace(state={}))}
            self.assertFalse(needs_regeneration(song,profile,selected))
            for flag in ('walls','bombs'):
                self.assertTrue(needs_regeneration(song,profile,{**selected,flag:not selected[flag]}))

    def test_previous_motion_profile_requires_regeneration(self):
        self.assertTrue(needs_regeneration({'auto_algorithm':ALGORITHM,'ai_model':'base'},
                                         {'id':'base'},{'walls':False,'bombs':False}))

    def test_settings_are_independent_and_default_off(self):
        self.assertEqual(object_options(SimpleNamespace(state={})),{'walls':False,'bombs':False})
        self.assertEqual(object_options(SimpleNamespace(state={'ai_bombs':True})),{'walls':False,'bombs':True})


if __name__=='__main__':unittest.main()
