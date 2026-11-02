"""Model selection shared by the UI and the isolated AI worker."""
import hashlib
import json
from pathlib import Path


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def profiles(engine):
    engine = Path(engine).resolve()
    base = engine / 'models/v2_expert'
    result = []
    if (base / 'bundle.json').is_file():
        result.append({'id': 'base', 'label': 'Исходная модель', 'bundle': base})
    for path in sorted((engine / 'models/experiments').glob('*/profile.json'), reverse=True):
        try:
            record = _json(path)
            if record.get('status') != 'evaluated' or record.get('id') != path.parent.name:
                continue
            bundle = path.parent / 'bundle'
            if (bundle / 'bundle.json').is_file():
                report=Path(record.get('report','evaluation'))
                if not report.is_absolute():report=path.parent/report
                result.append({**record, 'bundle': bundle, 'report': str(report)})
        except (ValueError, OSError):
            continue
    return result


def resolve_profile(engine, profile_id='base'):
    for record in profiles(engine):
        if record['id'] == profile_id:
            record = dict(record)
            # The bundle includes the hashes of every checkpoint. A different
            # candidate must never reuse maps cached for a previous model.
            record['revision'] = hashlib.sha256((record['bundle'] / 'bundle.json').read_bytes()).hexdigest()
            return record
    raise ValueError('Выбранная AI-модель отсутствует или её проверка не завершена: ' + str(profile_id))
