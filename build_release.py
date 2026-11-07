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
VERSION = '1.6.0'
MODEL_10 = 'finetune-20261102T113535114682Z'
MODEL_40 = 'finetune-20261102T174826024561Z'
NAME = 'PicoSongPatcher-' + VERSION + '-Windows-x64'
PORTABLE_ROOT = 'Pico' + VERSION.replace('.', '')
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
    excluded = {'__pycache__', '.git', '.pytest_cache', 'Scripts'}
    if Path(folder).name == 'tensorflow':
        excluded.add('include')
    if Path(folder).name == 'joblib':
        excluded.add('test')
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
        if (path.is_file() and path.suffix in ('.py', '.json', '.txt', '.lock') and not path.name.startswith('test_')
                and path.name not in {'model_selection.json', 'download_models.py', 'model_metadata.json', 'models_lock.json'}):
            shutil.copy2(path, target / path.name)


def public_model_metadata(bundle, models, shared):
    """Keep inference contracts and public provenance in exported sidecars."""
    for path in bundle.glob('*/report.json'):
        path.unlink()
    for path in bundle.glob('*/manifest.json'):
        value = json.loads(path.read_text(encoding='utf-8'))
        keys = ('kind', 'schema_version', 'stage', 'status', 'checkpoint', 'checkpoint_sha256',
                'representation', 'normalization', 'normalization_sha256', 'compatibility',
                'vocabularies', 'encoder_identity')
        public = {key: value[key] for key in keys if key in value}
        if 'training' in value:
            training_keys = ('architecture', 'status', 'parameters', 'input_shapes', 'bomb_presence',
                             'split_identity', 'stage_split', 'batch_size', 'optimizer', 'clipnorm',
                             'conditioning', 'intensity_noise_std', 'monitor', 'training_settings')
            public['training'] = {key: value['training'][key] for key in training_keys if key in value['training']}
        path.write_text(json.dumps(public, indent=2), encoding='utf-8')
    path = bundle / 'bundle.json'
    original = json.loads(path.read_text(encoding='utf-8'))
    keys = ('kind', 'schema_version', 'status', 'model_stages', 'encoder', 'data_settings',
            'normalization_sha256', 'vocabularies_sha256', 'calibrations', 'pico_timing_targets', 'limitations')
    public = {key: original[key] for key in keys if key in original}
    files = {}
    for name, record in original['files'].items():
        file = bundle / name
        if file.is_file():
            files[name] = {'sha256': sha(file), 'bytes': file.stat().st_size}
        elif (file.relative_to(models).as_posix() in shared):
            files[name] = record
        elif not (name.endswith('/report.json') or name.startswith('lighting/')):
            raise ValueError('Missing required model file: ' + name)
    public['files'] = files
    public['pico_release'] = VERSION
    path.write_text(json.dumps(public, indent=2), encoding='utf-8')


def copy_models(primary, model_engine=None):
    source, target = Path(model_engine or ROOT / 'ai_engine'), TARGET / 'ai_engine'
    models = target / 'models'
    shared, first = {}, {}
    print('Copying the two evaluated models with shared weights…', flush=True)
    for identifier in (MODEL_10, primary):
        origin = source / 'models/experiments' / identifier
        record = json.loads((origin / 'profile.json').read_text(encoding='utf-8'))
        recordings = 10 if identifier == MODEL_10 else record.get('dataset_recordings')
        if record.get('status') != 'evaluated' or not (origin / 'bundle/bundle.json').is_file():
            raise ValueError('The expected evaluated model is required: ' + identifier)
        destination = models / 'experiments' / identifier
        for path in sorted((origin / 'bundle').rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or ignored(path.parent, [path.name]):
                continue
            output = destination / 'bundle' / path.relative_to(origin / 'bundle')
            relative = output.relative_to(models).as_posix()
            if path.suffix.lower() in ('.keras', '.h5', '.pkl'):
                digest = sha(path)
                if digest in first:
                    shared[relative] = {'source': first[digest], 'sha256': digest}
                    continue
                first[digest] = relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
        label = ('Дообученная · 10 песен' if identifier == MODEL_10 else record['label'].replace(' (тест)', ''))
        profile = {'id': identifier, 'label': label, 'status': 'evaluated', 'experimental': True,
                   'report': 'evaluation'}
        if recordings is not None:
            profile['dataset_recordings'] = recordings
        (destination / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
        report = destination / 'evaluation'
        report.mkdir()
        if identifier == primary:
            evaluation = origin / record.get('report', 'evaluation')
            for name in ('report.html', 'report_ru.txt', 'aggregate_summary.json'):
                if (evaluation / name).is_file():
                    shutil.copy2(evaluation / name, report / name)
            if (evaluation / 'dlc.json').is_file():
                results = json.loads((evaluation / 'dlc.json').read_text(encoding='utf-8'))
                summary = {'source_profile': results['source_profile'], 'dlc_splits': results['dlc_splits'],
                           'stages': {stage: {'selected_weights_changed': row['training']['selected_weights_changed'],
                               'best_epoch': row['training']['best_epoch'],
                               **{label: {arm: {key: metrics[key] for key in ('loss', 'examples', 'conditioning')}
                                          for arm, metrics in row[label].items()}
                                  for label in ('dlc_test', 'previous_test')}}
                               for stage, row in results['stages'].items()}}
                (report / 'aggregate_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                (report / 'report_ru.txt').write_text(
                    'Профиль продолжает обучение на примерах DLC. Использованы 19 записей. '
                    '12 записей выделены для обучения, 4 для выбора весов и 3 для контрольной проверки.\n'
                    'Выбраны обновлённые веса плотности. Для тайминга проверка выбрала исходные веса. '
                    'Разреженный прогноз дополняется звуковыми акцентами с ограничениями плотности.\n'
                    'Полные карты проверены на ПК. Удобство движений оценивайте в очках.\n', encoding='utf-8')
        else:
            shutil.copy2(ROOT / 'TRAINING_RESULTS.md', report / 'README.md')
    (models / 'shared_weights.json').write_text(json.dumps(shared, indent=2), encoding='utf-8')
    for identifier in (MODEL_10, primary):
        public_model_metadata(models / 'experiments' / identifier / 'bundle', models, shared)
    (target / 'model_selection.json').write_text(json.dumps({
        'hidden': ['base', MODEL_40], 'default': primary,
        'aliases': {'base': MODEL_10, MODEL_40: primary, 'pacing-85-153': primary, 'pacing-85-dlc-154': primary},
    }, indent=2), encoding='utf-8')


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
        'InfernoSaber source and upstream model card declare MIT; see InfernoSaber-MIT.txt and the README in each model bundle.\n'
        'FFmpeg is downloaded by the user from BtbN/FFmpeg-Builds on first audio processing; it is not in this archive. Its notices and verified download provenance are stored in data/runtime_assets/tools.\n'
        'No FMOD binary or game resources are distributed. The public executable decodes the patcher PCM16 banks directly.\n', encoding='utf-8')
    (target / 'Microsoft-runtime.txt').write_text('Microsoft Visual C++ Runtime redistributable components are included app-locally for Python and TensorFlow. They retain their Microsoft copyright and license.\nhttps://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files\n', encoding='utf-8')


def finish():
    data = TARGET / 'data'
    data.mkdir(exist_ok=True)
    state = {'base_obb': '', 'songs': [],
             'ai_walls': True, 'ai_bombs': True, 'target_pack': 'ostvol1'}
    (data / 'library.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'THIRD_PARTY.md', TARGET / 'THIRD_PARTY.md')
    shutil.copy2(ROOT/'docs/USER_GUIDE_RU.html',TARGET/'ИНСТРУКЦИЯ.html')
    (TARGET / 'НАЧАТЬ_ЗДЕСЬ.txt').write_text(
        f'Pico Song Patcher {VERSION} — Windows x64, portable\n\n'
        '1. Распакуй ВЕСЬ архив в отдельную доступную для записи папку. Не запускай EXE внутри ZIP. Python устанавливать не нужно.\n'
        '2. Открой ИНСТРУКЦИЯ.html или нажми «Инструкция» в программе: там выгрузка кэша, все кнопки и примеры шагов.\n'
        '   Запусти ' + NAME + '.exe. Папки _internal и ai_engine должны оставаться рядом.\n'
        '3. Выгрузи из очков свой совместимый кэш Beat Saber 1.40.8 / OVRport 3.2.2. Положи OBB и espresso рядом, выбери OBB в программе.\n'
        '4. Добавляй карты или MP3 и собирай новый кэш. Исходные файлы и устройство программа не перезаписывает.\n'
        '5. При первой обработке аудио программа загрузит FFmpeg с официального репозитория BtbN/FFmpeg-Builds (около 80 МБ), проверив SHA256. Для этого нужен интернет. Далее обработка локальная.\n'
        '6. Сохрани резервную копию кэша очков, затем перенеси готовые OBB и espresso вручную.\n\n'
        'В комплекте пустая библиотека, CPU AI-окружение и два профиля. По умолчанию выбран профиль DLC и тайминга. Второй профиль обучен на 10 песнях.\n'
        'Из аудио создаются Easy, Normal, Hard, Expert и Expert+. Для старых карт нажмите «Пересоздать AI».\n'
        'В поле «Арена AI» можно выбрать сцену из прочитанного OBB или «Разные для песен». Для существующей AI-карты используй «Пересоздать AI».\n'
        'Обучающие записи и массивы не включены: для собственного дообучения нужно подготовить эталоны по исходникам проекта.\n'
        'Извлечение аудио из готовых карт поддерживает PCM16-кэш патчера; для других сжатых FSB используй исходный MP3/ZIP. Переиспользование готовых карт декодирования не требует.\n\n'
        'Проект и сообщения об ошибках: https://github.com/BeatPico/PicoSongPatcher\n', encoding='utf-8')
    files = [path for path in TARGET.rglob('*') if path.is_file() and path.name != 'PAYLOAD.json']
    for path in files:
        relative=path.relative_to(TARGET)
        if (relative.parts[0]=='data' and relative.as_posix()!='data/library.json'
                or relative.parts[0]=='output' or '__pycache__' in relative.parts
                or relative.parts[:2]==('ai_engine','cache')
                or path.name in ('self_test.json','last_build.json','cache_inventory.json')):
            raise ValueError('A smoke-test or user file remains in the public payload: '+str(relative))
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
                result.write(path, PORTABLE_ROOT + '/' + path.relative_to(TARGET).as_posix())
    if archive.stat().st_size >= 2 * 1024**3:
        raise ValueError('GitHub release asset exceeds 2 GiB')
    (OUTPUT / 'SHA256SUMS.txt').write_text(sha(archive) + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': sha(archive)}, indent=2), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--primary-model', required=True)
    parser.add_argument('--model-engine', type=Path)
    args = parser.parse_args()
    if TARGET.exists():
        raise SystemExit('Use a fresh release build directory; an existing payload is never overwritten.')
    build_executable()
    copy_ai()
    copy_models(args.primary_model, args.model_engine)
    notices()
    finish()
