"""Create local game templates and acquire FFmpeg without redistributing either."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request
import wave
import zipfile

from maps import require

PINNED_FFMPEG = {
    'url': 'https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-11-02-12-51/ffmpeg-n8.1.3-32-g760d10603e-win64-lgpl-shared-8.1.zip',
    'sha256': '424ee1ebefd41bda90247db6a08d510d11e14a20ca19ecc168a50d4e63eb7fee',
    'size': 80357941,
}


def _open(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'PicoSongPatcher/1.4.1'}), timeout=90)


def ffmpeg_source():
    try:
        with _open('https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest') as response:
            record = json.load(response)
        choices = [a for a in record['assets'] if 'win64-lgpl-shared' in a['name'] and a['name'].endswith('.zip')]
        choices.sort(key=lambda a: ('n8.1' not in a['name'], 'master' in a['name'], a['name']))
        asset = choices[0]
        digest = asset.get('digest', '')
        require(digest.startswith('sha256:') and len(digest) == 71, 'Источник FFmpeg не предоставил SHA256.')
        require(asset['browser_download_url'].startswith('https://github.com/BtbN/FFmpeg-Builds/releases/download/'), 'Неожиданный источник FFmpeg.')
        require(0 < asset['size'] < 250 * 1024 * 1024, 'Неожиданный размер FFmpeg.')
        return {'url': asset['browser_download_url'], 'sha256': digest[7:], 'size': asset['size']}
    except Exception:
        return dict(PINNED_FFMPEG)


def ensure_ffmpeg(assets, local_root, log=print):
    existing = Path(assets) / 'tools/ffmpeg.exe'
    if existing.is_file():
        return existing
    target = Path(local_root) / 'tools'
    ready = target / 'ffmpeg.exe'
    if ready.is_file():
        return ready
    target.mkdir(parents=True, exist_ok=True)
    source = ffmpeg_source()
    log('Первая настройка: загрузка FFmpeg с github.com/BtbN/FFmpeg-Builds (около 80 МБ)…')
    with tempfile.TemporaryDirectory(prefix='ffmpeg_', dir=target.parent) as temp:
        archive = Path(temp) / 'download.zip'
        digest = hashlib.sha256()
        count = 0
        with _open(source['url']) as response, archive.open('wb') as output:
            while block := response.read(1024 * 1024):
                count += len(block)
                require(count <= source['size'], 'Размер загрузки FFmpeg превысил ожидаемый.')
                digest.update(block)
                output.write(block)
        require(count == source['size'] and digest.hexdigest() == source['sha256'], 'Не совпала контрольная сумма FFmpeg. Повтори загрузку.')
        with zipfile.ZipFile(archive) as bundle:
            rows = bundle.infolist()
            require(sum(row.file_size for row in rows) < 600 * 1024 * 1024, 'Неожиданный размер распакованного FFmpeg.')
            files = []
            for row in rows:
                path = Path(row.filename.replace('\\', '/'))
                require(not path.is_absolute() and '..' not in path.parts, 'Небезопасный путь в архиве FFmpeg.')
                if row.is_dir():
                    continue
                if 'bin' in path.parts and path.suffix.lower() in ('.dll', '.exe'):
                    destination = Path(temp) / path.name
                    destination.write_bytes(bundle.read(row))
                    files.append(destination)
                elif path.suffix.lower() in ('.txt', '.md') or path.name.upper().startswith(('LICENSE', 'COPYING')):
                    notices = target / 'notices' / path
                    notices.parent.mkdir(parents=True, exist_ok=True)
                    notices.write_bytes(bundle.read(row))
            require(any(path.name == 'ffmpeg.exe' for path in files), 'В архиве отсутствует ffmpeg.exe.')
            # Publish the executable last, after all dependent DLLs are installed.
            for path in sorted(files, key=lambda value: value.name == 'ffmpeg.exe'):
                shutil.copy2(path, target / path.name)
        (target / 'download.json').write_text(json.dumps(source, indent=2), encoding='utf-8')
    log('FFmpeg установлен локально, SHA256 проверен.')
    return ready


def game_assets(base, assets, local_root, log=print):
    assets, local_root, base = Path(assets), Path(local_root), Path(base)
    if all((assets / name).is_file() for name in ('gameplay_template.bundle', 'original_espresso')):
        return assets
    espresso = base.parent / 'espresso'
    require(espresso.is_file(), 'Положи файл espresso из кэша очков рядом с выбранным OBB. Публичная сборка не содержит игровых файлов.')
    require(espresso.stat().st_size < 256 * 1024 * 1024, 'Неожиданный размер файла espresso.')
    source_hash = hashlib.sha256(espresso.read_bytes()).hexdigest()
    target = local_root / ('game_' + source_hash[:20])
    if (target / 'ready.json').is_file() and all((target / name).is_file() for name in ('gameplay_template.bundle', 'original_espresso')):
        return target
    log('Подготовка игровых шаблонов из вашего файла espresso…')
    import UnityPy
    from engine import bank, replace_resource, text
    local_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='templates_', dir=local_root) as temp:
        work = Path(temp)
        silence = work / 'silence.wav'
        with wave.open(str(silence), 'wb') as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b'\0' * 44100 * 4)
        fsb, _, _ = bank(silence)
        env = UnityPy.load(espresso.read_bytes())
        require(any(o.type.name == 'MonoBehaviour' and '_difficultyBeatmapSets' in o.parse_as_dict() for o in env.objects), 'Файл espresso не содержит совместимых игровых данных.')
        for obj in env.objects:
            value = obj.parse_as_dict()
            if obj.type.name == 'AudioClip':
                value.update(m_Length=1., m_CompressionFormat=0, m_Name='Template')
                value['m_Resource']['m_Size'] = len(fsb)
                obj.patch(value)
            elif obj.type.name == 'TextAsset':
                name = value['m_Name']
                if '.audio' in name:
                    payload = {'version': '4.0.0', 'songSampleCount': 44100, 'songFrequency': 44100, 'bpmData': []}
                elif '.lightshow' in name:
                    payload = {'version': '4.0.0'}
                else:
                    payload = {'version': '4.1.0', 'colorNotes': [], 'bombNotes': [], 'obstacles': []}
                text(obj, payload)
        for name in list(env.file.files):
            if name.endswith('.resource'):
                replace_resource(env.file, name, fsb)
        (work / 'gameplay_template.bundle').write_bytes(env.file.save(packer='original'))
        shutil.copy2(espresso, work / 'original_espresso')
        for name in ('environments.json', 'stock_packs.json'):
            shutil.copy2(assets / name, work / name)
        (work / 'ready.json').write_text(json.dumps({'source_sha256': source_hash}), encoding='utf-8')
        target.mkdir(parents=True, exist_ok=True)
        for name in ('gameplay_template.bundle', 'original_espresso', 'environments.json', 'stock_packs.json', 'ready.json'):
            shutil.copy2(work / name, target / name)
    return target
