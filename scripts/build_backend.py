"""Build on each target OS/architecture, with no model weights or private data."""
import os
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path

root = Path(__file__).resolve().parent.parent
backend = root / 'backend'
args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--name', 'ip-agent-service', '--distpath', str(root/'dist/backend'),
        '--workpath', str(root/'build/python'), '--specpath', str(root/'build'),
        '--paths', str(backend), '--add-data', str(backend/'static') + os.pathsep + 'static',
        '--collect-all', 'uvicorn', '--collect-all', 'cryptography', '--collect-all', 'certifi']
for path in backend.glob('*.py'):
    args += ['--add-data', str(path) + os.pathsep + '.']
# Preserve dependency license texts and distribution metadata in frozen releases.
for distribution in distributions():
    if distribution.metadata.get('Name'):
        args += ['--copy-metadata', distribution.metadata['Name']]
for license_name in ('LICENSE.txt', 'LICENSE'):
    license_file = Path(sys.base_prefix) / license_name
    if license_file.is_file():
        args += ['--add-data', str(license_file) + os.pathsep + 'python-license']
for name in ['torch', 'numpy', 'scipy', 'transformers', 'diffusers', 'onnxruntime',
             'whisper', 'soundfile', 'TTS_infer_pack', 'latentsync', 'DeepCache', 'accelerate', 'omegaconf']:
    args += ['--exclude-module', name]
args.append(str(backend/'desktop_backend.py'))
subprocess.run(args, cwd=root, check=True)
