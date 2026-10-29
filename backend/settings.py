"""Portable paths. Application code is read-only; all user state stays outside it."""
from pathlib import Path
import os
import sys
import subprocess

SOURCE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('IP_AGENT_WORKSPACE', str(Path.home() / '.ip-agent'))).resolve()
DATA = ROOT / 'data'
UPLOADS = DATA / 'uploads'
JOBS = DATA / 'jobs'
BIN = Path(os.environ.get('IP_AGENT_MEDIA_BIN', str(ROOT / 'bin')))
def venv_python(name):
    return ROOT / name / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
PYTHON = Path(os.environ.get('IP_AGENT_ENGINE_PYTHON', str(venv_python('.venv'))))
FFMPEG = str(BIN / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'))
FFPROBE = str(BIN / ('ffprobe.exe' if os.name == 'nt' else 'ffprobe'))
for p in (UPLOADS, JOBS, ROOT / 'logs', ROOT / 'cache', BIN):
    p.mkdir(parents=True, exist_ok=True)

def environment():
    env = os.environ.copy()
    env.update({
        'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
        'HF_HOME': str(ROOT / 'cache/huggingface'),
        'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_XET': '1',
        'TORCH_HOME': str(ROOT / 'cache/torch'),
        'NLTK_DATA': str(ROOT / 'models/nltk_data'),
        'TEMP': str(ROOT / 'cache'), 'TMP': str(ROOT / 'cache'),
        'MPLCONFIGDIR': str(ROOT / 'cache/matplotlib'),
        'TOKENIZERS_PARALLELISM': 'false', 'TQDM_DISABLE': '1',
        'PATH': str(BIN) + os.pathsep + env.get('PATH', ''),
    })
    return env

def worker_process(python, script, stage, job_path):
    builtin = Path(script).name == 'worker_cloud_speech.py' or stage in ('prepare', 'speed')
    if builtin:
        args = ['--worker', Path(script).stem, stage, str(job_path)]
        return [sys.executable, *args] if getattr(sys, 'frozen', False) else [sys.executable, str(SOURCE/'desktop_backend.py'), *args]
    return [str(python), '-u', str(script), stage, str(job_path)]

def terminate_process(proc):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        proc.terminate()
