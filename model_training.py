"""Launch training in the existing isolated Python, with a persistent run log."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def train(engine, project, log=print, epochs=3, prepared_file=None, source_profile='base'):
    engine, project = Path(engine).resolve(), Path(project).resolve()
    prepared = Path(prepared_file).resolve() if prepared_file else project / 'training_examples/preparation_result.json'
    if not prepared.is_file():
        raise ValueError('Нет подготовленных примеров. Сначала запусти prepare_training_examples.py в исходном проекте.')
    preparation = json.loads(prepared.read_text(encoding='utf-8'))
    for key in ('beat_dataset', 'v2_dataset'):
        if not (Path(preparation[key]) / 'manifest.json').is_file():
            raise ValueError('Не найден набор обучающих данных: ' + preparation[key])
    from model_profiles import resolve_profile
    parent=resolve_profile(engine,source_profile)
    run_id = datetime.now(timezone.utc).strftime('finetune-%Y%m%dT%H%M%S%fZ')
    run = engine / 'models/experiments' / run_id
    run.mkdir(parents=True)
    job = {'id': run_id, 'run': str(run), 'source_bundle': str(parent['bundle']),
           'beat_dataset': preparation['beat_dataset'], 'v2_dataset': preparation['v2_dataset'],
           'epochs': int(epochs), 'learning_rate': 1e-5, 'seed': 127,
           'source_profile':source_profile,'prepared_file':str(prepared),'dataset_recordings':preparation.get('packages'),
           'ffmpeg': str(project / 'assets/tools/ffmpeg.exe')}
    job_path = run / 'job.json'
    job_path.write_text(json.dumps(job, indent=2), encoding='utf-8')
    log('Дообучение: ' + run_id + '. Журнал: ' + str(run / 'training.log'))
    process = subprocess.Popen([str(engine / 'runtime/python.exe'), '-X', 'utf8', '-B',
                                str(engine / 'train_model.py'), str(job_path)],
        cwd=engine / 'infernosaber', stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding='utf-8', errors='replace',
        env=dict(os.environ, PYTHONUTF8='1', CUDA_VISIBLE_DEVICES='-1'),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    tail = []
    with (run / 'training.log').open('w', encoding='utf-8') as stream:
        for line in process.stdout:
            stream.write(line); stream.flush()
            tail = (tail + [line.strip()])[-10:]
            if line.startswith('@training '):
                log(json.loads(line[10:])['log'])
    process.stdout.close()
    if process.wait() != 0:
        raise RuntimeError('Дообучение не завершено. Журнал: ' + str(run / 'training.log') + '\n' + '\n'.join(tail[-4:]))
    return json.loads((run / 'result.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    project = Path(__file__).resolve().parent
    result = train(project / 'ai_engine', project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
