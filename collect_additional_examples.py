"""Collect 30 further human BeatSaver recordings without changing the original ten.

Only public metadata/archive GETs are used. Archives and raw maps are retained;
filtered Info views and provenance are separate. This never starts training.
"""
import copy
import hashlib
import json
import subprocess
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import collect_training_examples as original

ROOT = APP / 'training_examples_40'
OLD = APP / 'training_examples'
GENRES = ('hip-hop-rap', 'metal', 'funk-disco', 'folk-acoustic',
          'classical-orchestral', 'drum-and-bass', 'house', 'trance',
          'rock', 'pop', 'k-pop', 'video-game-soundtrack', 'j-pop', 'dance')
MUSIC_TAGS = set(GENRES) | {'alternative', 'ambient', 'anime', 'blues', 'country',
    'dubstep', 'electronic', 'hardcore', 'indie', 'j-rock', 'jazz', 'r-b',
    'reggae', 'soul', 'speedcore', 'swing', 'techno', 'tv-movie-soundtrack', 'vocaloid'}
QUERY = {k: v for k, v in original.QUERY.items() if k != 'from'}
QUERY.update(q='', fullSpread='true')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def audio_hashes(package):
    info_path = next(p for p in Path(package).iterdir() if p.name.lower() == 'info.dat')
    info = original.read_json(info_path)
    audio = original.locate(package, info['_songFilename'])
    process = subprocess.Popen([str(APP / 'assets/tools/ffmpeg.exe'), '-v', 'error', '-nostdin',
        '-i', str(audio), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    decoded = hashlib.sha256()
    size = 0
    for block in iter(lambda: process.stdout.read(1024 * 1024), b''):
        decoded.update(block)
        size += len(block)
    process.stdout.close()
    if process.wait() or not size or size % 4:
        raise ValueError('Audio decoding failed')
    return {'audio_sha256': digest(audio), 'decoded_sha256': decoded.hexdigest(),
            'decoded_sample_count': size // 4, 'audio_filename': audio.name,
            'decoded_hash_policy': 'FFmpeg mono 16000 Hz f32le; identical to portable preparation'}


def main():
    for name in ('api', 'archives', 'raw', 'packages'):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    original.ROOT = ROOT
    baseline_path = OLD / 'manifest.json'
    baseline_digest = digest(baseline_path)
    baseline = original.read_json(baseline_path)
    assert len(baseline['packages']) == 10
    preserved = []
    identities = {}
    for item in baseline['packages']:
        item = copy.deepcopy(item)
        for field in ('archive', 'raw_package', 'training_package'):
            item[field] = str((OLD / item[field]).resolve())
        assert digest(item['archive']) == item['archive_sha256']
        hashes = audio_hashes(item['training_package'])
        item.update(hashes, collection='original_10_preserved')
        api = original.read_json(OLD / 'api' / (item['id'] + '.json'))
        item['beatsaver_tags'] = api.get('tags') or []
        item['genre_evidence'] = {'type': 'BeatSaver uploader tags',
            'genres': sorted(set(api.get('tags') or []) & MUSIC_TAGS),
            'source': 'https://api.beatsaver.com/maps/id/' + item['id']}
        for key in ('audio_sha256', 'decoded_sha256'):
            identities[hashes[key]] = item['id']
        preserved.append(item)
    selected, skipped, duplicates = [], [], []
    resume = ROOT / 'manifest.json'
    if resume.is_file():
        prior = original.read_json(resume)
        skipped = prior.get('skipped', [])
        duplicates = prior.get('duplicate_recordings', [])
        for item in prior.get('packages', [])[10:]:
            assert digest(ROOT / item['archive']) == item['archive_sha256']
            selected.append(item)
            for key in ('audio_sha256', 'decoded_sha256'):
                identities[item[key]] = item['id']
    rows, provenance = {}, defaultdict(list)
    for source in sorted((OLD / 'api').glob('search*.json')):
        for row in original.read_json(source).get('docs', []):
            rows[row['id']] = row
            provenance[row['id']].append({'cache': str(source), 'query': original.QUERY})
    for genre in GENRES:
        query = {**QUERY, 'tags': genre}
        url = 'https://api.beatsaver.com/search/text/0?' + urllib.parse.urlencode(query)
        cache = ROOT / 'api' / ('search_genre_all_dates_' + genre + '.json')
        if cache.is_file():
            data = original.read_json(cache)
        else:
            with original.fetch(url) as response:
                data = json.load(response)
            original.save(cache, data)
            time.sleep(.25)
        for row in data.get('docs', []):
            rows[row['id']] = row
            provenance[row['id']].append({'url': url, 'query': query, 'cache': str(cache)})
    ids = {item['id'] for item in preserved + selected}
    attempted = ids | {item['id'] for item in skipped}
    mapper_counts = Counter(item['mapper'].casefold() for item in selected)
    genre_counts = Counter(item.get('selection_genre') for item in selected)

    def commit():
        original.save(ROOT / 'manifest.json', {
            'schema_version': 1, 'source_search': original.SOURCE_URL,
            'api_query': QUERY, 'genre_queries': list(GENRES),
            'date_policy': 'Original ten preserve original search cutoff. Additional genre searches cover all dates to include supported Info2 maps.',
            'api_schema_source': 'https://api.beatsaver.com/docs/swagger.json',
            'api_schema_cached': str(OLD / 'api_schema.json'),
            'human_policy': 'Omit automapper search parameter (false means AI-only); require automapper=false and declaredAi None on every selected row.',
            'genre_policy': 'Music tags supplied on BeatSaver; untagged styles are unknown, not inferred from artist/title.',
            'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
            'original_manifest': str(baseline_path), 'original_manifest_sha256': baseline_digest,
            'target_packages': 40, 'new_packages': len(selected),
            'packages': preserved + selected, 'skipped': skipped,
            'duplicate_recordings': duplicates, 'trained': False, 'active_model_changed': False})

    def priority(row):
        diffs = original.eligible_diffs(row)
        low = sum(d['difficulty'] in ('Easy', 'Normal', 'Hard') for d in diffs)
        return (-low, -len(diffs), -row['stats']['score'], row['id'])

    queues = {genre: sorted([r for r in rows.values() if genre in (r.get('tags') or [])
        and len(original.eligible_diffs(r)) >= 2], key=priority) for genre in GENRES}
    # First fill two per genre, then widen to at most four from any one bucket.
    for quota in (2, 3, 4, 30):
        for genre in GENRES:
            for row in queues[genre]:
                if len(selected) >= 30 or genre_counts[genre] >= quota:
                    break
                if row['id'] in attempted:
                    continue
                mapper = row['metadata']['levelAuthorName'].casefold().strip()
                if mapper_counts[mapper] >= 3:
                    continue
                attempted.add(row['id'])
                original.log('Checking ' + row['id'] + ' [' + genre + '] ' + row['metadata']['songName'])
                try:
                    # Reuse cached immutable ZIP bytes via a hard link when available.
                    source_zip = OLD / 'archives' / (row['id'] + '.zip')
                    target_zip = ROOT / 'archives' / source_zip.name
                    if source_zip.is_file() and not target_zip.exists():
                        target_zip.hardlink_to(source_zip)
                    item = original.download(row)
                    hashes = audio_hashes(ROOT / item['training_package'])
                    overlaps = sorted({identities[v] for k, v in hashes.items()
                        if k in ('audio_sha256', 'decoded_sha256') and v in identities})
                    if overlaps:
                        duplicates.append({'id': row['id'], 'same_recording_as': overlaps, **hashes})
                        raise ValueError('Duplicate original/selected recording: ' + ', '.join(overlaps))
                    item.update(hashes, collection='additional_30', selection_genre=genre,
                        beatsaver_tags=row.get('tags') or [],
                        genre_evidence={'type': 'BeatSaver uploader tags',
                            'genres': sorted(set(row.get('tags') or []) & MUSIC_TAGS),
                            'source': 'https://api.beatsaver.com/maps/id/' + row['id']},
                        search_provenance=provenance[row['id']])
                    selected.append(item)
                    ids.add(row['id'])
                    mapper_counts[mapper] += 1
                    genre_counts[genre] += 1
                    for key in ('audio_sha256', 'decoded_sha256'):
                        identities[hashes[key]] = row['id']
                    original.log('Accepted ' + row['id'] + ' ' + str(len(selected)) + '/30: ' +
                        ', '.join(c['difficulty'] for c in item['charts']))
                except Exception as error:
                    skipped.append({'id': row['id'], 'title': row['metadata']['songName'],
                                    'reason': type(error).__name__ + ': ' + str(error)})
                    original.log('Skipped ' + row['id'] + ': ' + str(error))
                commit()
                time.sleep(.2)
    commit()
    assert digest(baseline_path) == baseline_digest, 'Original selection changed'
    if len(selected) != 30:
        raise RuntimeError('Only ' + str(len(selected)) + ' compatible new recordings; review skips')
    summary = {'songs_total': 40, 'new_songs': 30, 'new_charts': sum(len(p['charts']) for p in selected),
        'new_difficulties': dict(Counter(c['difficulty'] for p in selected for c in p['charts'])),
        'selection_genres': dict(genre_counts),
        'music_tags': dict(Counter(t for p in selected for t in p['genre_evidence']['genres'])),
        'new_archive_mib': round(sum(p['archive_bytes'] for p in selected) / 1024 ** 2, 1),
        'duplicates_rejected': duplicates, 'original_manifest_unchanged': True}
    original.save(ROOT / 'collection_result.json', summary)
    listing = ['ADDITIONAL HUMAN BEATSAVER TRAINING REFERENCES', '',
        '30 additional recordings; 40 including the original ten.',
        'Genre labels are BeatSaver uploader tags. Archives and raw maps are unchanged.', '']
    for index, item in enumerate(selected, 1):
        listing.extend([f"{index}. {item['title']} - {item['artist']} / mapper: {item['mapper']}",
            item['url'], 'Genres: ' + ', '.join(item['genre_evidence']['genres']),
            'Difficulties: ' + ', '.join(c['difficulty'] for c in item['charts']),
            'ZIP SHA256: ' + item['archive_sha256'], ''])
    (ROOT / 'SELECTION.txt').write_text('\n'.join(listing) + '\n', encoding='utf-8')
    original.log('COLLECTION COMPLETE ' + json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    main()
