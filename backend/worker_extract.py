from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Download validated videos and extract plain text with local Whisper large-v3-turbo."""
import gc
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from extraction_config import WHISPER_MODEL, DOWNLOADER, MAX_DURATION
from settings import BIN

def progress(stage, percent, message, **extra):
    print('@@EXTRACT ' + json.dumps(dict(stage=stage, progress=percent, message=message, **extra), ensure_ascii=False), flush=True)

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def downloader():
    spec = importlib.util.spec_from_file_location('studio_video_link_downloader', DOWNLOADER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run_ffmpeg(args, timeout=3600):
    result = subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-xerror', *args],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('视频音画解码或音频提取失败，请尝试其他视频。')

def prepare(task, folder):
    dl = downloader()
    ffmpeg, ffprobe = str(FFMPEG), str(FFPROBE)
    if task.get('input_path'):
        progress('download', 5, '校验本地视频的完整音画')
        source = Path(task['input_path'])
        meta = dl.probe(source, ffprobe)
        if meta['duration'] > MAX_DURATION:
            raise RuntimeError('视频超过 30 分钟，请先截取需要的部分。')
        run_ffmpeg(['-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-f', 'null', '-'])
        target = folder / '本地视频' / (dl.safe_name(task['title']) + source.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as dst, source.open('rb') as src:
            shutil.copyfileobj(src, dst)
        meta.update(title=task['title'], author='本地视频', source_file=target.relative_to(folder).as_posix())
    else:
        import requests
        class PublicSession(requests.Session):
            def send(self, request, **kwargs):
                url = urlparse(request.url)
                host = (url.hostname or '').lower()
                allowed = host in {'sph.miuistore.com', 'apikr.convry.com', 'douyin.com', 'iesdouyin.com'} or any(host.endswith(s) for s in ('.douyin.com', '.iesdouyin.com', '.douyinvod.com'))
                if not allowed or url.scheme not in {'http', 'https'} or url.username or url.password or url.port not in (None, 80, 443):
                    raise RuntimeError('视频服务返回了非预期地址，已停止下载。')
                return super().send(request, **kwargs)
        progress('download', 5, '解析抖音链接，下载并校验视频')
        from types import SimpleNamespace
        args = SimpleNamespace(ffmpeg=ffmpeg, ffprobe=ffprobe, h264=False, max_candidates=3)
        with PublicSession() as session:
            session.headers['User-Agent'] = dl.UA
            result = dl.archive([{'text': task['share']}], folder, session, args)
        records = json.loads((folder / '视频清单.json').read_text(encoding='utf-8'))
        if result or not records or records[0]['status'] != '已归档并校验':
            detail = records[0].get('error', '') if records else ''
            raise RuntimeError('抖音视频下载失败。可稍后重试，或上传本地视频。' + detail)
        meta = records[0]
        target = (folder / meta['file']).resolve()
        if not target.is_relative_to(folder.resolve()):
            raise RuntimeError('下载清单路径无效。')
        meta['source_file'] = target.relative_to(folder).as_posix()
    if meta['duration'] > MAX_DURATION:
        raise RuntimeError('视频超过 30 分钟，请先截取需要的部分。')
    if not meta.get('audio_codec'):
        raise RuntimeError('这段视频没有音轨，无法进行语音转写。')
    progress('audio', 17, '提取清晰人声，准备视频预览')
    run_ffmpeg(['-y', '-i', str(target), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(folder / 'audio.wav')])
    run_ffmpeg(['-y', '-i', str(target), '-map', '0:v:0', '-map', '0:a:0',
                '-vf', "scale='min(960,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(folder / 'preview.mp4')])
    write_json(folder / 'media.json', {k: v for k, v in meta.items() if k != 'candidates'})
    progress('audio', 23, '视频已通过音画校验')

_dll_handles = []
def local_cuda():
    packages = Path(sys.prefix) / 'Lib/site-packages'
    directories = list((packages / 'nvidia').glob('*/bin'))
    directories += list((packages / 'nvidia').glob('*/lib'))
    for directory in directories:
        if directory.is_dir():
            _dll_handles.append(os.add_dll_directory(str(directory)))
    os.environ['PATH'] = os.pathsep.join(map(str, directories)) + os.pathsep + os.environ.get('PATH', '')

def transcribe(task, folder):
    os.environ['HF_HUB_OFFLINE'] = '1'
    local_cuda()
    import ctranslate2
    from faster_whisper import WhisperModel
    from opencc import OpenCC
    simplify = OpenCC('t2s').convert
    meta = json.loads((folder / 'media.json').read_text(encoding='utf-8'))
    device = 'cuda' if ctranslate2.get_cuda_device_count() else 'cpu'
    progress('transcribe', 26, '加载 Whisper large-v3-turbo', device=device)
    model = WhisperModel(str(WHISPER_MODEL), device=device, compute_type='float16' if device == 'cuda' else 'int8',
                         cpu_threads=6, num_workers=1, local_files_only=True)
    segments, info = model.transcribe(str(folder / 'audio.wav'),
                                     language=None if task['language'] == 'auto' else task['language'],
                                     beam_size=5, vad_filter=True, vad_parameters={'min_silence_duration_ms':400},
                                     condition_on_previous_text=False, initial_prompt=task.get('keywords') or None)
    rows = []
    for segment in segments:
        text = simplify(segment.text).strip()
        if text and segment.end > segment.start:
            rows.append({'id': len(rows), 'start': round(segment.start, 3),
                         'end': round(min(segment.end, meta['duration']), 3), 'text': text,
                         'raw_text': segment.text, 'avg_logprob': segment.avg_logprob})
        progress('transcribe', min(96, 28 + 68 * segment.end / meta['duration']),
                 f'语音转写中 · {min(100, round(segment.end / meta["duration"] * 100))}%')
    write_json(folder / 'asr.json', {'model': 'Whisper large-v3-turbo', 'device': device, 'language': info.language,
                                   'duration': meta['duration'], 'segments': rows})
    del model
    gc.collect()
    # Timings stay in the internal ASR record; the user receives plain prose only.
    text = '\n\n'.join(''.join(row['text'] for row in rows[i:i+5]).strip() for i in range(0, len(rows), 5))
    if not text.strip():
        raise RuntimeError('没有识别到清晰的人声，请换一段有口播的视频。')
    write_json(folder / 'transcript.json', {'text': text, 'language': info.language, 'device': device})
    progress('transcribe', 99, '文案已提取，正在保存')

if __name__ == '__main__':
    try:
        stage, path = sys.argv[1], Path(sys.argv[2])
        task = json.loads(path.read_text(encoding='utf-8'))
        {'prepare': prepare, 'transcribe': transcribe}[stage](task, path.parent)
    except Exception as exc:
        message = re.sub(r'https?://\S+', '[链接已省略]', str(exc))[:400]
        print('@@ERROR ' + message, flush=True)
        raise SystemExit(1)
