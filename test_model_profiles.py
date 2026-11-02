import json
from pathlib import Path
import tempfile
import unittest

from model_profiles import profiles, resolve_profile


class ModelProfilesTest(unittest.TestCase):
    def test_unfinished_model_cannot_be_selected_and_weights_change_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'models/v2_expert'
            base.mkdir(parents=True)
            (base / 'bundle.json').write_text('{}')
            candidate = root / 'models/experiments/test-run'
            (candidate / 'bundle').mkdir(parents=True)
            manifest = candidate / 'bundle/bundle.json'
            manifest.write_text('{"files":{"timing":{"sha256":"before"}}}')
            profile = candidate / 'profile.json'
            profile.write_text(json.dumps({'id': 'test-run', 'label': 'test', 'status': 'training'}))
            self.assertEqual([p['id'] for p in profiles(root)], ['base'])
            with self.assertRaises(ValueError):
                resolve_profile(root, 'test-run')
            profile.write_text(json.dumps({'id': 'test-run', 'label': 'test', 'status': 'evaluated'}))
            before = resolve_profile(root, 'test-run')['revision']
            manifest.write_text('{"files":{"timing":{"sha256":"after"}}}')
            self.assertNotEqual(before, resolve_profile(root, 'test-run')['revision'])
            self.assertNotEqual(resolve_profile(root, 'base')['revision'], before)

    def test_profile_directory_and_id_must_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / 'models/experiments/test-run'
            (candidate / 'bundle').mkdir(parents=True)
            (candidate / 'bundle/bundle.json').write_text('{}')
            (candidate / 'profile.json').write_text(json.dumps({'id': '../another', 'status': 'evaluated'}))
            self.assertEqual(profiles(root), [])


if __name__ == '__main__':
    unittest.main()
