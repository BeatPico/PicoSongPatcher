"""Create A/B review ZIPs from local audio without changing the user's library."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'beat_saber_lab/python_packages')]
from library import Library
from neural_autogen import generate, engine_root
from model_profiles import profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', nargs='+', type=Path)
    parser.add_argument('--profile', help='Candidate ID; defaults to newest evaluated model')
    parser.add_argument('--baseline-profile',default='base',help='Model used for arm A')
    parser.add_argument('--walls',action='store_true')
    parser.add_argument('--bombs',action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    models = [p for p in profiles(engine_root()) if p['id'] != 'base']
    if not models:
        raise ValueError('Нет проверенного кандидата для сравнения')
    candidate = args.profile or models[0]['id']
    if candidate not in [p['id'] for p in models]:
        raise ValueError('Кандидат не найден')
    output = args.output or ROOT / 'validation_training' / datetime.now(timezone.utc).strftime('audio-%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True, exist_ok=False)
    library = Library(output / 'data')
    library.state.update(ai_walls=args.walls,ai_bombs=args.bombs)
    records = []
    for index, source in enumerate(args.audio):
        for arm, model in (('A', args.baseline_profile), ('B', candidate)):
            library.state['ai_model'] = model
            entry, _ = generate(source, library, ROOT / 'assets', difficulties={'Easy': 1.5},
                                log=lambda line: print(line, flush=True))
            folder = library.root / entry['folder']
            package = output / f'{index + 1}-{arm}'
            shutil.copytree(folder, package)
            info = json.loads((package / 'Info.dat').read_text(encoding='utf-8-sig'))
            info['_songName'] = f"{arm} · {entry['title']}"
            info['_songSubName'] = '(baseline)' if arm == 'A' else '(trained)'
            (package / 'Info.dat').write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
            archive = output / f'{index + 1}-{arm}.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
                for path in package.iterdir():
                    if path.is_file():
                        stream.write(path, path.name)
            analysis = json.loads((folder / 'AI_analysis.json').read_text(encoding='utf-8'))
            records.append({'source': str(source.resolve()), 'title': entry['title'], 'arm': arm,
                'model': model, 'revision': entry['ai_model_revision'], 'duration': entry['full_audio_seconds'],
                'notes': analysis['difficulties'], 'archive': str(archive.resolve())})
    (output / 'comparison.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    text = ['A — '+args.baseline_profile+'. B — '+candidate+'.',
            'Easy, запрос 1.5 блока/с; одинаковый seed для одного исходного аудиофайла.',
            'Это примеры для прослушивания/игры, эталонной авторской карты для этих MP3 нет.',
            'ZIP можно добавить в патчер. Текущая библиотека не изменялась.', '']
    text += [f"{r['arm']} · {r['title']}: {r['notes']['Easy']} блоков, {r['duration']:.1f} с" for r in records]
    (output / 'СРАВНЕНИЕ.txt').write_text('\n'.join(text), encoding='utf-8')
    with zipfile.ZipFile(output/'A-B-Easy.zip','w',zipfile.ZIP_STORED) as stream:
        for record in records:
            path=Path(record['archive'])
            stream.write(path,path.name)
        stream.write(output/'СРАВНЕНИЕ.txt','СРАВНЕНИЕ.txt')
    print('Review packages: ' + str(output.resolve()), flush=True)


if __name__ == '__main__':
    main()
