"""Read original downloaded charts and compare their density by difficulty."""
import bisect
import json
import math
import io
import zipfile
from pathlib import Path
from datetime import datetime, timezone

import soundfile as sf

ROOT = Path(__file__).resolve().parent
DIFFICULTIES = ('Easy', 'Normal', 'Hard', 'Expert', 'ExpertPlus')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def local(root, name):
    path = root
    for part in str(name).replace('\\', '/').split('/'):
        if part in ('', '.', '..'):
            raise ValueError('Invalid relative filename')
        matches = [p for p in path.iterdir() if p.name.casefold() == part.casefold()]
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous filename: ' + str(name))
        path = matches[0]
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('External path')
    return path


def timing(chart, bpm):
    events = chart.get('bpmEvents', [])
    for event in events:
        if not math.isclose(float(event['m']), bpm, abs_tol=0.001):
            raise ValueError('Variable tempo not supported in this comparison')
    version = str(chart.get('_version', chart.get('version', '')))
    if version.startswith('2'):
        notes = [n for n in chart.get('_notes', []) if n.get('_type') in (0, 1)]
        times = [float(n['_time']) * 60 / bpm for n in notes]
        bombs = sum(n.get('_type') == 3 for n in chart.get('_notes', []))
        walls = len(chart.get('_obstacles', []))
    elif version.startswith(('3', '4')):
        notes = chart.get('colorNotes', [])
        times = [float(n['b']) * 60 / bpm for n in notes]
        bombs, walls = len(chart.get('bombNotes', [])), len(chart.get('obstacles', []))
    else:
        raise ValueError('Unknown beatmap version: ' + version)
    return sorted(times), bombs, walls


def density(times, duration):
    if not times or not all(math.isfinite(t) and t >= 0 for t in times):
        raise ValueError('No valid colored notes')
    if times[-1] > duration + 0.5:
        raise ValueError('Notes extend beyond audio')
    maximum = max(bisect.bisect_left(times, start + 5) - i for i, start in enumerate(times))
    active = times[-1] - times[0]
    return {'blocks': len(times), 'nps_full_song': len(times) / duration,
            'nps_active_span': len(times) / active if active else None,
            'peak_5s_nps': maximum / 5, 'first_note': times[0], 'last_note': times[-1]}


def package(folder, provenance=None):
    infos = [p for p in folder.rglob('*') if p.is_file() and p.name.casefold() == 'info.dat']
    if len(infos) != 1:
        raise ValueError('Missing/ambiguous Info.dat')
    info_path = infos[0]
    info, home = read(info_path), info_path.parent
    if '_songName' not in info:
        raise ValueError('Info v4 requires explicit audio timing; skipped')
    bpm = float(info['_beatsPerMinute'])
    if info.get('_songTimeOffset', 0) or info.get('_shuffle', 0):
        raise ValueError('Nonzero audio offset/shuffle')
    audio = local(home, info['_songFilename'])
    duration = sf.info(str(audio)).duration
    charts, skipped = [], []
    for group in info.get('_difficultyBeatmapSets', []):
        if group['_beatmapCharacteristicName'] != 'Standard':
            continue
        for entry in group['_difficultyBeatmaps']:
            try:
                chart_path = local(home, entry['_beatmapFilename'])
                chart = read(chart_path)
                if chart.get('_customData', {}).get('_BPMChanges'):
                    raise ValueError('Custom BPM changes')
                times, bombs, walls = timing(chart, bpm)
                charts.append({'difficulty': entry['_difficulty'], **density(times, duration),
                    'bombs': bombs, 'walls': walls, 'source': str(chart_path)})
            except (ValueError, KeyError, OSError) as error:
                skipped.append({'difficulty': entry.get('_difficulty'), 'reason': str(error)})
    return {'title': info['_songName'], 'artist': info.get('_songAuthorName', ''),
            'mapper': info.get('_levelAuthorName', ''), 'bpm': bpm, 'duration': duration,
            'charts': charts, 'skipped_charts': skipped, 'info': str(info_path),
            'provenance': provenance or {}}


def original_zip(path):
    """Read counts directly from original user downloads, without extraction."""
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:raise ValueError('Invalid archive CRC')
        names={name.casefold():name for name in archive.namelist()}
        infos=[name for name in archive.namelist() if Path(name).name.casefold()=='info.dat']
        if len(infos)!=1:raise ValueError('Ambiguous Info.dat')
        prefix=infos[0][:-len(Path(infos[0]).name)]
        info=json.loads(archive.read(infos[0]).decode('utf-8-sig'))
        def contents(name):return archive.read(names[(prefix+name).casefold()])
        audio=sf.info(io.BytesIO(contents(info['_songFilename'])))
        bpm=float(info['_beatsPerMinute']);charts=[]
        for group in info['_difficultyBeatmapSets']:
            if group['_beatmapCharacteristicName']!='Standard':continue
            for entry in group['_difficultyBeatmaps']:
                times,bombs,walls=timing(json.loads(contents(entry['_beatmapFilename']).decode('utf-8-sig')),bpm)
                charts.append({'difficulty':entry['_difficulty'],**density(times,audio.duration),'bombs':bombs,'walls':walls})
        return {'title':info['_songName'],'artist':info['_songAuthorName'],'mapper':info['_levelAuthorName'],
                'bpm':bpm,'duration':audio.duration,'charts':charts,'original_zip':str(path)}


def main():
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'references': [], 'skipped': [], 'targets': []}
    metadata = {}
    for file in (ROOT / 'training_examples/api').glob('*.json'):
        data = read(file)
        for doc in data.get('docs', [data]):
            if doc.get('id'):
                metadata[doc['id']] = doc
    selection = {p['id']: p for p in read(ROOT / 'training_examples/manifest.json')['packages']}
    for folder in sorted((ROOT / 'training_examples/raw').iterdir()):
        if not folder.is_dir():
            continue
        doc = metadata.get(folder.name, selection.get(folder.name, {}))
        if doc.get('automapper') is not False or doc.get('declaredAi', doc.get('declared_ai', 'None')) not in ('None', None):
            result['skipped'].append({'package': str(folder), 'reason': 'No cached human-map provenance'})
            continue
        try:
            result['references'].append(package(folder, {'id': folder.name,
                'url': 'https://beatsaver.com/maps/' + folder.name, 'automapper': doc['automapper']}))
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            result['skipped'].append({'package': str(folder), 'reason': str(error)})
    original_downloads=[]
    for name in ('55579 (Looks Can Kill - eurnkung).zip','54b7b (Genie Magic - Danny).zip'):
        path=Path.home()/'Downloads'/name
        # The portable runtime may run as a sandbox user; the library's source
        # paths identify the user's download directory independently of HOME.
        if not path.is_file():path=ROOT.parents[2]/'Downloads'/name
        if path.is_file():original_downloads.append(original_zip(path))
    result['original_downloads']=original_downloads
    library_root = ROOT / 'validation_training/user-audio-20261102/data'
    for song in read(library_root / 'library.json')['songs']:
        if song['ai_model'] != 'base':
            continue
        report = read(library_root / song['analysis_file'])
        target = {'title': song['title'], 'duration': song['full_audio_seconds'],
                  'estimated_bpm': report['parts'][0]['estimated_bpm'], 'source': song['source_audio']}
        eligible = [r for r in result['references'] if r['charts']]
        ranked=[{**r,'match_distance':abs(math.log(r['duration']/target['duration']))+abs(math.log(r['bpm']/target['estimated_bpm']))} for r in eligible]
        target['closest_references'] = sorted(ranked, key=lambda r: r['match_distance'])[:6]
        result['targets'].append(target)
    destination = ROOT / 'validation_training/reference-density'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines=['# Плотность исходных авторских карт','',
           'Подсчитаны Standard-карты непосредственно в оригинальных ZIP из Downloads. Патчер не участвовал в подсчёте.',
           'Цветной блок считается за один; одновременная пара — за два. Бомбы, стены и дополнительные сегменты цепочек в число блоков не входят.',
           'Средняя плотность = блоки / полная длительность аудио. Пик — максимальная плотность на окне 5 секунд.','']
    for song in original_downloads:
        lines += [f"## {song['title']} — {song['artist']}",f"Автор карты: {song['mapper']}. Длина: {song['duration']:.2f} с; BPM в Info.dat: {song['bpm']:.0f}.",'',
                  '| Сложность | Блоков | Блоков/с | Пик на 5 с, блоков/с | Бомбы | Стены |','|---|---:|---:|---:|---:|---:|']
        by_name={c['difficulty']:c for c in song['charts']}
        for name in DIFFICULTIES:
            if name in by_name:
                c=by_name[name];lines.append(f"| {name} | {c['blocks']} | {c['nps_full_song']:.2f} | {c['peak_5s_nps']:.1f} | {c['bombs']} | {c['walls']} |")
        lines += ['',f"Исходный ZIP: {song['original_zip']}",'']
    lines += ['## Сопоставление с нашей генерацией','',
              'Sexy and I Know It: 198.03 с. Оценка анализатора 260 BPM; для сопоставления использована половинная интерпретация 130 BPM. Автоматический BPM не является проверенной музыкальной разметкой.',
              'Looks Can Kill: почти та же длина и 130 BPM в авторской карте. Genie Magic: 188.07 с и 128 BPM; дополнительная близкая пара.',
              'Наш Easy до дообучения: 669 блоков (3.38/с). После: 205 (1.04/с). По числу блоков старый Easy ближе к Hard Looks Can Kill, новый — к Easy.',
              'Это сравнение плотности, а не полной сложности: рисунок рук, направления, скорость подлёта и пики нагрузки тоже имеют значение.',
              'Для Базового минимума среди выбранных исходных ZIP не заявляется точное совпадение одновременно по длительности и музыкальному темпу.']
    (destination/'СРАВНЕНИЕ.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'original_downloads':original_downloads,'references':len(result['references'])},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
