"""Select environments supplied by the target game cache."""
import json
from pathlib import Path
import zipfile

from maps import require

DEFAULT_ARENA = 'DefaultEnvironment'
VARIED_ARENA = 'varied'


def environments_in_archive(names, assets):
    known = json.loads((Path(assets) / 'environments.json').read_text(encoding='utf-8'))
    scenes, resources = set(), set()
    for name in names:
        filename = name.rsplit('/', 1)[-1].casefold()
        if not name.startswith('assets/aa/') or not filename.endswith('.bundle'):
            continue
        if '_scenes_all_' in filename:
            scenes.add(filename.split('_scenes_all_', 1)[0])
        if '_assets_all_' in filename:
            resources.add(filename.split('_assets_all_', 1)[0])
    return [DEFAULT_ARENA, *(name for name in sorted(known)
            if name not in (DEFAULT_ARENA, 'TutorialEnvironment', 'MultiplayerEnvironment')
            and name.casefold() in scenes & resources)]


def available_arenas(cache, assets):
    if not cache or not Path(cache).is_file():
        return [DEFAULT_ARENA]
    with zipfile.ZipFile(cache) as archive:
        return environments_in_archive(archive.namelist(), assets)


def choose_arena(mode, available, audio_sha):
    if mode == VARIED_ARENA:
        alternatives = sorted(set(available) - {DEFAULT_ARENA})
        return alternatives[int(audio_sha[:16], 16) % len(alternatives)] if alternatives else DEFAULT_ARENA
    require(mode in available, 'Выбранной арены нет в этом OBB: ' + mode)
    return mode
