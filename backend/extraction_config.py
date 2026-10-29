from settings import ROOT, venv_python
"""Trusted machine configuration; never supplied by a browser request."""
import os
from pathlib import Path

WHISPER_PYTHON = Path(os.environ.get('STUDIO_WHISPER_PYTHON', str(venv_python('.whispervenv'))))
WHISPER_MODEL = Path(os.environ.get('STUDIO_WHISPER_MODEL', str(ROOT/'models/faster-whisper-large-v3-turbo')))
DOWNLOADER = Path(os.environ.get('STUDIO_VIDEO_DOWNLOADER', str(Path.home() / '.codex/skills/video-link-downloader/scripts/douyin_archive.py')))
MAX_DURATION = 1800

def readiness():
    missing = []
    for path, label in [(WHISPER_PYTHON, '本地 Whisper 运行环境'), (DOWNLOADER, 'video-link-downloader 技能'),
                        (WHISPER_MODEL / 'model.bin', 'Whisper large-v3-turbo 权重'),
                        (WHISPER_MODEL / 'tokenizer.json', 'Whisper 分词文件')]:
        if not path.is_file():
            missing.append(label)
    return {'ready': not missing, 'missing': missing, 'model': 'Whisper large-v3-turbo', 'max_duration': MAX_DURATION}
