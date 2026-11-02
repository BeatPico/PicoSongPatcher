import unittest
from types import SimpleNamespace
from neural_autogen import ALGORITHM, needs_regeneration, object_options


class GenerationObjectsTest(unittest.TestCase):
    def test_old_maps_remain_current_with_objects_disabled(self):
        song={'auto_algorithm':ALGORITHM,'ai_model':'base'}
        self.assertFalse(needs_regeneration(song,{'id':'base'},{'walls':False,'bombs':False}))

    def test_each_object_toggle_requires_a_new_map(self):
        profile={'id':'trained','revision':'same-weights'}
        for walls,bombs in ((False,False),(True,False),(False,True),(True,True)):
            selected={'walls':walls,'bombs':bombs}
            song={'auto_algorithm':ALGORITHM,'ai_model':'trained','ai_model_revision':'same-weights','ai_objects':selected}
            self.assertFalse(needs_regeneration(song,profile,selected))
            for flag in ('walls','bombs'):
                self.assertTrue(needs_regeneration(song,profile,{**selected,flag:not selected[flag]}))

    def test_settings_are_independent_and_default_off(self):
        self.assertEqual(object_options(SimpleNamespace(state={})),{'walls':False,'bombs':False})
        self.assertEqual(object_options(SimpleNamespace(state={'ai_bombs':True})),{'walls':False,'bombs':True})


if __name__=='__main__':unittest.main()
