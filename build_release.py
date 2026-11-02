"""Build a clean public portable bundle, isolated from every local installation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '1.4.1'
NAME = 'PicoSongPatcher-' + VERSION + '-Windows-x64'
WORK = ROOT / 'release_build' / VERSION
TARGET = WORK / 'dist' / NAME
OUTPUT = ROOT / 'release_artifacts' / ('v' + VERSION)
LAB = ROOT.parent / 'beat_saber_lab'
sys.path[:0] = [str(LAB / 'build_packages'), str(LAB / 'python_packages')]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ignored(folder, names):
    excluded = {'__pycache__', 'tests', 'test', '.git', '.pytest_cache', 'Scripts'}
    media = {'.wav', '.mp3', '.ogg', '.flac', '.m4a', '.mp4', '.obb', '.apk', '.pem', '.key', '.pfx'}
    return [name for name in names if name in excluded or (Path(name).suffix.lower() in media and name != 'cacert.pem') or name == 'direct_url.json']


def build_executable():
    import PyInstaller.__main__
    clean = WORK / 'public_assets'
    clean.mkdir(parents=True, exist_ok=True)
    for name in ('stock_packs.json', 'environments.json'):
        shutil.copy2(ROOT / 'assets' / name, clean / name)
    PyInstaller.__main__.run([
        str(ROOT / 'app_v14.py'), '--noconfirm', '--windowed', '--onedir',
        '--name', NAME, '--distpath', str(WORK / 'compiled'), '--workpath', str(WORK / 'pyinstaller'),
        '--specpath', str(WORK), '--contents-directory', '_internal',
        '--paths', str(LAB / 'python_packages'), '--paths', str(ROOT),
        '--collect-all', 'UnityPy', '--collect-all', 'PIL', '--collect-all', 'archspec',
        '--collect-all', 'tpk_ar', '--collect-all', 'numpy',
        '--exclude-module', 'fmod_toolkit', '--exclude-module', 'pyfmodex',
        '--hidden-import', 'native_audio', '--runtime-hook', str(ROOT / 'hooks/portable_audio.py'),
        '--add-data', str(clean) + ';assets',
    ])
    compiled=WORK/'compiled'/NAME
    TARGET.mkdir(parents=True,exist_ok=True)
    shutil.copytree(compiled/'_internal',TARGET/'_internal',dirs_exist_ok=True)
    shutil.copy2(compiled/(NAME+'.exe'),TARGET/(NAME+'.exe'))


def copy_ai():
    source, target = ROOT / 'ai_engine', TARGET / 'ai_engine'
    target.mkdir(exist_ok=True)
    print('Copying isolated AI runtime…', flush=True)
    shutil.copytree(source / 'runtime', target / 'runtime', ignore=ignored)
    # TensorFlow checks these names before importing sklearn; use app-local
    # Microsoft runtime DLLs rather than depending on a system installation.
    shutil.copy2(source / 'runtime/Lib/site-packages/sklearn/.libs/msvcp140.dll', target / 'runtime/msvcp140.dll')
    shutil.copy2(Path(os.environ['SystemRoot']) / 'System32/msvcp140_1.dll', target / 'runtime/msvcp140_1.dll')
    vendor = source / 'infernosaber'
    tracked = subprocess.check_output(['git', '-c', 'safe.directory=' + vendor.as_posix(), '-C', str(vendor), 'ls-files', '-z']).decode().split('\0')
    for name in tracked:
        if not name:
            continue
        path = (vendor / name).resolve()
        if not path.is_relative_to(vendor.resolve()) or not path.is_file() or ignored(path.parent, [path.name]):
            continue
        destination = target / 'infernosaber' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    for path in source.iterdir():
        if path.is_file() and path.suffix in ('.py', '.json', '.txt', '.lock') and not path.name.startswith('test_'):
            shutil.copy2(path, target / path.name)
    print('Copying inference models only…', flush=True)
    shutil.copytree(source / 'models/v2_expert', target / 'models/v2_expert', ignore=ignored)
    for identifier, label, recordings in [
        ('finetune-20261102T113535114682Z', 'Дообученная · 10 песен', 10),
        ('finetune-20261102T174826024561Z', 'Эксперимент · 40 песен', 40),
    ]:
        origin = source / 'models/experiments' / identifier
        destination = target / 'models/experiments' / identifier
        shutil.copytree(origin / 'bundle', destination / 'bundle', ignore=ignored)
        profile = {'id': identifier, 'label': label, 'status': 'evaluated', 'experimental': True,
                   'dataset_recordings': recordings, 'report': 'evaluation'}
        (destination / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
        report = destination / 'evaluation'
        report.mkdir()
        shutil.copy2(ROOT / ('TRAINING_RESULTS_40.md' if recordings == 40 else 'TRAINING_RESULTS.md'), report / 'README.md')


def notices():
    target = TARGET / 'licenses'
    target.mkdir()
    shutil.copy2(ROOT / 'ai_engine/infernosaber/LICENSE', target / 'InfernoSaber-MIT.txt')
    shutil.copy2(Path(sys.executable).parent / 'LICENSE.txt', target / 'Python-3.14.txt')
    for path in (LAB / 'build_packages').glob('pyinstaller-*.dist-info/licenses/COPYING.txt'):
        shutil.copy2(path, target / 'PyInstaller-bootloader.txt')
    (target / 'README.txt').write_text(
        'Third-party license texts are also preserved next to the bundled packages in _internal and ai_engine/runtime/Lib/site-packages/*.dist-info.\n'
        'Python 3.11 license: ai_engine/runtime/LICENSE.txt.\n'
        'InfernoSaber source and upstream model card declare MIT; see InfernoSaber-MIT.txt and ai_engine/models/v2_expert/README.md.\n'
        'FFmpeg is downloaded by the user from BtbN/FFmpeg-Builds on first audio processing; it is not in this archive. Its notices and verified download provenance are stored in data/runtime_assets/tools.\n'
        'No FMOD binary or game resources are distributed. The public executable decodes the patcher PCM16 banks directly.\n', encoding='utf-8')
    (target / 'Microsoft-runtime.txt').write_text('Microsoft Visual C++ Runtime redistributable components are included app-locally for Python and TensorFlow. They retain their Microsoft copyright and license.\nhttps://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files\n', encoding='utf-8')


def finish():
    data = TARGET / 'data'
    data.mkdir(exist_ok=True)
    state = {'base_obb': '', 'songs': [], 'ai_model': 'finetune-20261102T113535114682Z',
             'ai_walls': True, 'ai_bombs': True, 'target_pack': 'ostvol1'}
    (data / 'library.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in ('README_1.4.md', 'TRAINING_RESULTS_40.md', 'THIRD_PARTY.md'):
        shutil.copy2(ROOT / name, TARGET / name)
    shutil.copy2(ROOT/'docs/USER_GUIDE_RU.html',TARGET/'ИНСТРУКЦИЯ.html')
    (TARGET / 'НАЧАТЬ_ЗДЕСЬ.txt').write_text(
        'Pico Song Patcher 1.4.1 — Windows x64, portable\n\n'
        '1. Распакуй ВЕСЬ архив в отдельную доступную для записи папку. Не запускай EXE внутри ZIP. Python устанавливать не нужно.\n'
        '2. Открой ИНСТРУКЦИЯ.html или нажми «Инструкция» в программе: там выгрузка кэша, все кнопки и примеры шагов.\n'
        '   Запусти ' + NAME + '.exe. Папки _internal и ai_engine должны оставаться рядом.\n'
        '3. Выгрузи из очков свой совместимый кэш Beat Saber 1.40.8 / OVRport 3.2.2. Положи OBB и espresso рядом, выбери OBB в программе.\n'
        '4. Добавляй карты или MP3 и собирай новый кэш. Исходные файлы и устройство программа не перезаписывает.\n'
        '5. При первой обработке аудио программа загрузит FFmpeg с официального репозитория BtbN/FFmpeg-Builds (около 80 МБ), проверив SHA256. Для этого нужен интернет. Далее обработка локальная.\n'
        '6. Сохрани резервную копию кэша очков, затем перенеси готовые OBB и espresso вручную.\n\n'
        'В комплекте пустая библиотека, отдельное CPU AI-окружение и три профиля моделей. По умолчанию — модель на 10 песнях. Модель на 40 экспериментальная.\n'
        'Обучающие записи и массивы не включены: для собственного дообучения нужно подготовить эталоны по исходникам проекта.\n'
        'Извлечение аудио из готовых карт поддерживает PCM16-кэш патчера; для других сжатых FSB используй исходный MP3/ZIP. Переиспользование готовых карт декодирования не требует.\n\n'
        'Проект и сообщения об ошибках: https://github.com/BeatPico/PicoSongPatcher\n', encoding='utf-8')
    files = [path for path in TARGET.rglob('*') if path.is_file() and path.name != 'PAYLOAD.json']
    forbidden = [str(path.relative_to(TARGET)) for path in files if path.suffix.lower() in ('.obb', '.apk', '.mp3', '.ogg', '.wav', '.flac') or path.name in ('original_espresso', 'gameplay_template.bundle', 'fmod.dll', 'training_project.txt', 'library_snapshot.json')]
    if forbidden:
        raise ValueError('Private/game payload in public build: ' + str(forbidden))
    print('Hashing clean payload…', flush=True)
    manifest = {'version': VERSION, 'source_commit': subprocess.check_output(['git', '-C', str(ROOT), '-c', 'safe.directory=' + ROOT.as_posix(), 'rev-parse', 'HEAD']).decode().strip(),
                'files': {path.relative_to(TARGET).as_posix(): {'bytes': path.stat().st_size, 'sha256': sha(path)} for path in sorted(files)}}
    (TARGET / 'PAYLOAD.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive = OUTPUT / (NAME + '.zip')
    print('Compressing portable archive…', flush=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as result:
        for path in sorted(TARGET.rglob('*')):
            if path.is_file():
                result.write(path, NAME + '/' + path.relative_to(TARGET).as_posix())
    if archive.stat().st_size >= 2 * 1024**3:
        raise ValueError('GitHub release asset exceeds 2 GiB')
    (OUTPUT / 'SHA256SUMS.txt').write_text(sha(archive) + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': sha(archive)}, indent=2), flush=True)


if __name__ == '__main__':
    if TARGET.exists():
        raise SystemExit('Use a fresh release build directory; an existing payload is never overwritten.')
    build_executable()
    copy_ai()
    notices()
    finish()
