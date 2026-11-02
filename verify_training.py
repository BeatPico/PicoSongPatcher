"""Run model adapter checks in the portable CPU environment."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'ai_engine'
sys.path[:0] = [str(ROOT), str(ENGINE), str(ENGINE / 'infernosaber')]
for key, value in {'CUDA_VISIBLE_DEVICES': '-1', 'TF_CPP_MIN_LOG_LEVEL': '2',
                   'TF_NUM_INTRAOP_THREADS': '2', 'TF_NUM_INTEROP_THREADS': '2',
                   'OMP_NUM_THREADS': '2', 'MPLBACKEND': 'Agg',
                   'NUMBA_CACHE_DIR': str(ENGINE / 'cache/numba')}.items():
    os.environ[key] = value
from ffmpeg_source import install
install(ROOT / 'assets/tools/ffmpeg.exe')
import pytest

if __name__ == '__main__':
    if '--upstream' in sys.argv:
        os.chdir(ENGINE / 'infernosaber')
        args = ['-q', '-m', 'not comparison']
    else:
        os.chdir(ROOT)
        args = ['-q', 'test_model_profiles.py', 'ai_engine/test_finetune_beats.py',
                'ai_engine/test_finetune_stages.py', 'ai_engine/test_evaluate_candidate.py']
    sys.exit(pytest.main(args))
