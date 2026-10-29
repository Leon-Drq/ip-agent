"""Fail publication if staged/tracked sources contain private artifacts or credentials."""
import re
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parent.parent
files=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
problems=[]
patterns=[r'gh[pousr]_[A-Za-z0-9]{30,}',r'github_pat_[A-Za-z0-9_]{40,}',
          r'\bsk-[A-Za-z0-9_-]{24,}',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
          r'[CD]:[/\\](?:Users[/\\]Administrator|BaiduNetdiskDownload|口播|vibevideo)[/\\]']
for name in filter(None,files):
    parts=Path(name).parts
    if any(p in ('data','models','vendor','cache','logs','node_modules','.venv','exports') for p in parts):
        problems.append(name+': private/generated directory')
    path=root/name
    if path.suffix.lower() in ('.mp4','.wav','.mp3','.ckpt','.pth','.safetensors','.dpapi','.secret','.exe','.dll'):
        problems.append(name+': binary/user artifact')
    if path.suffix=='.png':continue
    try:text=path.read_text(encoding='utf-8')
    except UnicodeDecodeError:problems.append(name+': unexpected binary');continue
    for pattern in patterns:
        if re.search(pattern,text):problems.append(name+': possible credential or private machine path')
if problems:raise SystemExit('\n'.join(problems))
print(f'Source audit passed: {len(list(filter(None,files)))} tracked files; no user data, model weights or credential patterns')
