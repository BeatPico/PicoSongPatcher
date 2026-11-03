import unittest
from types import SimpleNamespace
from neural_autogen import ALGORITHM, needs_regeneration, object_options, current_gameplay_options, GAMEPLAY_REVISION
from ai_engine.gameplay_options import space_beginner_hazards
from ai_engine.full_song import blank, finish
from song_names import clean_song_name


class GenerationObjectsTest(unittest.TestCase):
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
              'ai_gameplay_options':current_gameplay_options(SimpleNamespace(state={}))}
        self.assertFalse(needs_regeneration(song,{'id':'base'},{'walls':False,'bombs':False}))

    def test_each_object_toggle_requires_a_new_map(self):
        profile={'id':'trained','revision':'same-weights'}
        for walls,bombs in ((False,False),(True,False),(False,True),(True,True)):
            selected={'walls':walls,'bombs':bombs}
            song={'auto_algorithm':ALGORITHM,'ai_model':'trained','ai_model_revision':'same-weights','ai_objects':selected,
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
