"""Create a separate local 1.4 installation without changing the running 1.3.1."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent
TARGET=ROOT/'dist_v4/Pico Song Patcher 1.4'
OLD=ROOT/'dist_v3/Pico Song Patcher 1.2'


def copy_engine():
    source=ROOT/'ai_engine';target=TARGET/'ai_engine';target.mkdir(exist_ok=True)
    if not (target/'runtime/python.exe').is_file():shutil.copytree(source/'runtime',target/'runtime')
    framework=source/'infernosaber';destination=target/'infernosaber';destination.mkdir(exist_ok=True)
    tracked=subprocess.run(['git','-c','safe.directory='+str(framework).replace('\\','/'),'-C',str(framework),'ls-files','-z'],check=True,stdout=subprocess.PIPE).stdout.decode('utf-8').split('\0')
    for name in tracked:
        if not name:continue
        file=(framework/name).resolve();require_local=file.is_relative_to(framework.resolve())
        if not require_local:raise ValueError('External source path')
        if not file.is_file():continue
        out=destination/name;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,out)
    for file in source.iterdir():
        if file.is_file() and file.suffix in ('.py','.json','.txt','.lock') and not file.name.startswith('test_'):
            shutil.copy2(file,target/file.name)
    models=target/'models';models.mkdir(exist_ok=True)
    if not (models/'v2_expert/bundle.json').is_file():shutil.copytree(source/'models/v2_expert',models/'v2_expert')
    experiments=models/'experiments';experiments.mkdir(exist_ok=True)
    for profile in (source/'models/experiments').glob('*/profile.json'):
        record=json.loads(profile.read_text(encoding='utf-8'))
        if record.get('status')!='evaluated':continue
        output=experiments/profile.parent.name
        if not output.exists():shutil.copytree(profile.parent,output)
        report=Path(record.get('report','evaluation'))
        record['report']=report.name if report.is_absolute() else str(report)
        (output/'profile.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')


def copy_library():
    source=OLD/'data';target=TARGET/'data'
    if (target/'library.json').exists():return
    state=json.loads((source/'library.json').read_text(encoding='utf-8'))
    for entry in state['songs']:
        if not entry.get('folder'):continue
        folder=(source/entry['folder']).resolve()
        if not folder.is_relative_to(source.resolve()):raise ValueError('Library folder is outside the source library')
        relative=folder.relative_to(source.resolve());output=target/relative
        if not output.exists():shutil.copytree(folder,output)
    latest=OLD/'output/PicoSongs_2026-11-02_19-49-58_746656'
    cache=latest/'Android/obb/com.beatgames.beatsaber/main.1716.com.beatgames.beatsaber.obb'
    if cache.is_file():state.update(source_obb=str(cache),cache_obb=str(cache),last_output=str(latest))
    target.mkdir(parents=True,exist_ok=True)
    (target/'library.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    (TARGET/'library_snapshot.json').write_text(json.dumps({'source':str(source),'songs':len(state['songs']),
        'source_library_sha256':hashlib.sha256((source/'library.json').read_bytes()).hexdigest(),
        'created_at':datetime.now(timezone.utc).isoformat()},indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--activate-model');args=parser.parse_args()
    TARGET.mkdir(parents=True,exist_ok=True);copy_engine();copy_library()
    (TARGET/'training_project.txt').write_text(str(ROOT),encoding='utf-8')
    if args.activate_model:
        profile=TARGET/'ai_engine/models/experiments'/args.activate_model/'profile.json'
        if not profile.is_file():raise ValueError('Requested model was not copied')
        path=TARGET/'data/library.json';state=json.loads(path.read_text(encoding='utf-8'));state['ai_model']=args.activate_model
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Separate installation ready: '+str(TARGET),flush=True)


if __name__=='__main__':main()
