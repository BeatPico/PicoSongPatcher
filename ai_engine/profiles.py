"""Model selection shared by the UI and the isolated AI worker."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def selection(engine):
    path = Path(engine) / 'model_selection.json'
    return _json(path) if path.is_file() else {}


def selected_id(engine, current=None):
    settings = selection(engine)
    current = settings.get('aliases', {}).get(current, current)
    return current or settings.get('default', 'base')


def restore_shared_weights(engine, bundle):
    root = (Path(engine) / 'models').resolve()
    manifest = root / 'shared_weights.json'
    if not manifest.is_file():
        return
    prefix = bundle.relative_to(root).as_posix() + '/'
    for name, record in _json(manifest).items():
        if not name.startswith(prefix):
            continue
        target = (root / name).resolve()
        source = (root / record['source']).resolve()
        if not target.is_relative_to(bundle) or not source.is_relative_to(root):
            raise ValueError('Некорректный путь к общим весам модели.')
        if target.is_file():
            continue
        with source.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != record['sha256']:
            raise ValueError('Контрольная сумма общих весов модели не совпала.')
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.weights_', dir=target.parent) as folder:
            pending = Path(folder) / target.name
            try:
                pending.hardlink_to(source)
            except OSError:
                shutil.copyfile(source, pending)
            try:
                pending.replace(target)
            except PermissionError:
                if not target.is_file():
                    raise


def profiles(engine):
    engine = Path(engine).resolve()
    base = engine / 'models/v2_expert'
    result = []
    hidden = set(selection(engine).get('hidden', []))
    if 'base' not in hidden and (base / 'bundle.json').is_file():
        result.append({'id': 'base', 'label': 'Исходная модель', 'bundle': base})
    for path in sorted((engine / 'models/experiments').glob('*/profile.json'), reverse=True):
        try:
            record = _json(path)
            if record.get('status') != 'evaluated' or record.get('id') != path.parent.name or record['id'] in hidden:
                continue
            bundle = path.parent / 'bundle'
            if (bundle / 'bundle.json').is_file():
                report=Path(record.get('report','evaluation'))
                if not report.is_absolute():report=path.parent/report
                result.append({**record, 'bundle': bundle, 'report': str(report)})
        except (ValueError, OSError):
            continue
    return result


def resolve_profile(engine, profile_id=None):
    profile_id = selected_id(engine, profile_id)
    for record in profiles(engine):
        if record['id'] == profile_id:
            record = dict(record)
            restore_shared_weights(engine, record['bundle'])
            # The bundle includes the hashes of every checkpoint. A different
            # candidate must never reuse maps cached for a previous model.
            record['revision'] = hashlib.sha256((record['bundle'] / 'bundle.json').read_bytes()).hexdigest()
            return record
    raise ValueError('Выбранная AI-модель отсутствует или её проверка не завершена: ' + str(profile_id))
