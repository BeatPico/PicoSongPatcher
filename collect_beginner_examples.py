"""Collect up to 15 human Easy/Normal/Hard references for section pacing."""
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import collect_training_examples as source
from collect_additional_examples import GENRES, audio_hashes

DATA = ROOT / 'training_examples_pacing'
LIMIT = 15
ATTEMPTS = 36
NPS = {'Easy': (.55, 2.05), 'Normal': (.8, 3.05), 'Hard': (1.2, 4.8)}


def eligible(row):
    if row.get('automapper') or row.get('declaredAi') not in (None, 'None'):
        return []
    if not 65 <= row['metadata'].get('duration', 0) <= 330:
        return []
    return [d for d in source.published(row)['diffs'] if d['characteristic'] == 'Standard'
            and d['difficulty'] in NPS and NPS[d['difficulty']][0] <= d.get('nps', 0) <= NPS[d['difficulty']][1]
            and not any(d.get(k) for k in ('ne', 'me', 'cinema', 'chroma'))]


def main():
    previous = source.read_json(ROOT / 'training_examples_70/manifest.json')['packages']
    source.ROOT, source.eligible_diffs = DATA, eligible
    for name in ('api', 'archives', 'raw', 'packages'):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    manifest = DATA / 'manifest.json'
    saved = source.read_json(manifest) if manifest.is_file() else {}
    selected = saved.get('packages', [])
    rejected = [item for item in saved.get('skipped', []) if item['reason'] != 'Duplicate recording']
    excluded = {item['id'] for item in previous + selected + rejected}
    hashes = {item[key] for item in previous + selected for key in ('audio_sha256', 'decoded_sha256')}
    titles = {(item['title'].casefold(), item['artist'].casefold()) for item in previous + selected}
    rows = [(genre, row) for genre in GENRES for row in source.read_json(
        ROOT / 'training_examples_40/api' / f'search_genre_all_dates_{genre}.json')['docs']
        if row['id'] not in excluded and any(d['difficulty'] in ('Easy', 'Normal') for d in eligible(row))]
    genres = Counter(item['selection_genre'] for item in selected)
    while len(selected) < LIMIT and len(selected) + len(rejected) < ATTEMPTS:
        candidates = [(g, row) for g, row in rows if row['id'] not in excluded]
        if not candidates:
            break
        genre, row = min(candidates, key=lambda p: (genres[p[0]], -len(eligible(p[1])), -p[1]['stats']['score']))
        identifier = row['id']
        excluded.add(identifier)
        try:
            item = source.download(row)
            if not any(chart['difficulty'] in ('Easy', 'Normal') for chart in item['charts']):
                raise ValueError('No compatible Easy or Normal chart')
            audio = audio_hashes(DATA / item['training_package'])
            identity = (item['title'].casefold(), item['artist'].casefold())
            audio_ids = {audio['audio_sha256'], audio['decoded_sha256']}
            if identity in titles or audio_ids & hashes:
                raise ValueError('Duplicate title or audio hash')
            item.update(audio, selection_genre=genre)
            selected.append(item)
            hashes.update(audio_ids)
            titles.add(identity)
            genres[genre] += 1
            source.log(f"{len(selected)}/{LIMIT} {item['title']} [{genre}]")
        except (ValueError, OSError, KeyError, RuntimeError) as error:
            rejected.append({'id': identifier, 'reason': str(error)})
            source.log(f'Skipped {identifier}: {error}')
        source.save(manifest, {'packages': selected, 'skipped': rejected, 'attempt_limit': ATTEMPTS})
    if len(selected) < LIMIT:
        raise RuntimeError(f'Collected only {len(selected)} references')


if __name__ == '__main__':
    main()
