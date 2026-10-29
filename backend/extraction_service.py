from settings import terminate_process
from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Persistent download → local ASR → plain-text tasks, independent of avatar assets."""
import copy
import json
import queue
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing import Literal

from extraction_config import readiness, WHISPER_PYTHON, MAX_DURATION
from settings import ROOT, environment

ACTIVE = {'queued', 'running'}

def timestamp():
    return datetime.now().isoformat(timespec='seconds')

def share_link(text):
    for value in re.findall(r'https?://[^\s<>"\[\]]+', text.replace('\\_', '_')):
        value = value.rstrip(').,，。；;')
        url = urlparse(value)
        if url.hostname in {'v.douyin.com', 'douyin.com', 'www.douyin.com', 'www.iesdouyin.com'}:
            try:
                if url.username or url.password or url.port not in (None, 80, 443):
                    continue
            except ValueError:
                continue
            return value
    raise HTTPException(400, '请粘贴抖音视频链接或完整分享文本，也可以上传本地视频。')

def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

class ExtractInput(BaseModel):
    share: str = Field(default='', max_length=8000)
    video_id: str = ''
    language: Literal['zh', 'en', 'auto'] = 'zh'
    keywords: str = Field(default='', max_length=500)

class SaveTranscript(BaseModel):
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200000)

class ExtractionService:
    def __init__(self, directory, gpu_lock, upload_resolver, start=True):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.gpu_lock, self.upload_resolver = gpu_lock, upload_resolver
        self.lock = threading.RLock()
        self.records, self.processes, self.cancelled = {}, {}, set()
        self.pending = queue.Queue()
        for path in self.root.glob('*/task.json'):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if not re.fullmatch('[0-9a-f]{32}', value['id']) or path.parent.name != value['id']:
                    continue
                if value['status'] in ACTIVE:
                    value.update(status='failed', message='上次提取已中断，请重新提取。', error='服务重启中断了提取。')
                    atomic_json(path, value)
                self.records[value['id']] = value
            except (ValueError, KeyError, OSError):
                continue
        from extraction_clips import ExtractionClips
        self.clips = ExtractionClips(self, start=start)
        self.router = self.make_router()
        if start:
            threading.Thread(target=self.loop, daemon=True, name='transcript-queue').start()

    def get(self, tid):
        with self.lock:
            if tid not in self.records:
                raise HTTPException(404, '提取记录不存在。')
            return copy.deepcopy(self.records[tid])

    def update(self, tid, **changes):
        with self.lock:
            self.records[tid].update(changes, updated_at=timestamp())
            atomic_json(self.root / tid / 'task.json', self.records[tid])

    def summary(self, record):
        return {k: v for k, v in record.items() if k not in {'text', 'input_path', 'share'}}

    def public(self, record):
        return {k: v for k, v in record.items() if k not in {'input_path', 'share'}}

    def detail(self, tid):
        return self.public(self.get(tid))

    def create(self, values):
        if bool(values.share.strip()) == bool(values.video_id):
            raise HTTPException(400, '请选择一个抖音链接或一段本地视频。')
        settings = readiness()
        if not settings['ready']:
            raise HTTPException(503, '缺少：' + '、'.join(settings['missing']))
        title, source_url, input_path = '正在读取视频', '', ''
        if values.video_id:
            meta, path = self.upload_resolver(values.video_id)
            if meta['kind'] != 'video':
                raise HTTPException(400, '请选择视频文件。')
            if meta['duration'] > MAX_DURATION:
                raise HTTPException(400, '请使用 30 分钟以内的视频。')
            title, input_path = Path(meta['name']).stem[:200], str(path)
        else:
            source_url = share_link(values.share)
        if shutil.disk_usage(self.root).free < 3 * 1024**3:
            raise HTTPException(400, '剩余空间不足 3GB，请先腾出一些空间。')
        tid = uuid.uuid4().hex
        task = {**values.model_dump(), 'id': tid, 'title': title, 'source_url': source_url,
                'input_path': input_path, 'created_at': timestamp(), 'updated_at': timestamp(),
                'status': 'queued', 'stage': 'queued', 'message': '等待提取', 'progress': 0,
                'revision': 1, 'text': '', 'saved_at': None, 'error': None, 'video_ready': False,
                'model': 'Whisper large-v3-turbo'}
        with self.lock:
            if sum(j['status'] in ACTIVE for j in self.records.values()) >= 5:
                raise HTTPException(409, '已有 5 个提取任务，请等完成后再添加。')
            (self.root / tid).mkdir()
            atomic_json(self.root / tid / 'task.json', task)
            self.records[tid] = task
            self.pending.put(tid)
        return self.public(task)

    def save(self, tid, values):
        with self.lock:
            task = self.get(tid)
            if task['status'] != 'completed':
                raise HTTPException(409, '请等待文案提取完成后保存。')
            if task['revision'] != values.revision:
                raise HTTPException(409, '文案已在其他窗口更新，请重新打开这条记录后再保存。')
            if not values.title.strip() or not values.text.strip():
                raise HTTPException(400, '标题和文案不能为空。')
            self.update(tid, title=values.title.strip(), text=values.text.strip(),
                        saved_at=timestamp(), revision=task['revision'] + 1, message='文案已保存')
            task = self.get(tid)
            self.write_exports(tid, task)
            return self.public(task)

    def write_exports(self, tid, task):
        metadata = json.loads((self.root / tid / 'media.json').read_text(encoding='utf-8'))
        media = self.safe_file(tid, metadata['source_file'])
        for extension, content in [('txt', task['text'] + '\n'),
                                   ('md', '# ' + task['title'] + '\n\n' + task['text'] + '\n')]:
            target = media.with_name(media.stem + '_文案.' + extension)
            temporary = target.with_suffix(target.suffix + '.tmp')
            temporary.write_text(content, encoding='utf-8')
            temporary.replace(target)

    def safe_file(self, tid, relative):
        root = (self.root / tid).resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, '文件不存在。')
        return target

    def cancel(self, tid):
        with self.lock:
            task = self.get(tid)
            if task['status'] not in ACTIVE:
                return self.public(task)
            self.cancelled.add(tid)
            proc = self.processes.get(tid)
            if proc and proc.poll() is None:
                terminate_process(proc)
            self.update(tid, status='cancelled', stage='cancelled', message='提取已停止')
            return self.public(self.get(tid))

    def run_stage(self, tid, stage):
        with self.lock:
            if tid in self.cancelled:
                raise InterruptedError()
            env = environment()
            env.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYTHONUTF8='1')
            proc = subprocess.Popen([str(WHISPER_PYTHON), '-u', str(SOURCE / 'worker_extract.py'), stage,
                                     str(self.root / tid / 'task.json')], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes[tid] = proc
        problem = ''
        try:
            with (self.root / tid / (stage + '.log')).open('a', encoding='utf-8') as logfile:
                for line in proc.stdout:
                    clean = re.sub(r'https?://\S+', '[链接已省略]', line)
                    logfile.write(clean); logfile.flush()
                    if line.startswith('@@EXTRACT '):
                        payload = json.loads(line[len('@@EXTRACT '):])
                        with self.lock:
                            if tid not in self.cancelled:
                                self.update(tid, **{k: v for k, v in payload.items() if k in {'progress', 'message', 'stage', 'device'}})
                    if line.startswith('@@ERROR '):
                        problem = clean[len('@@ERROR '):].strip()[:400]
            code = proc.wait()
            if tid in self.cancelled:
                raise InterruptedError()
            if code:
                raise RuntimeError(problem or '提取未完成，请查看素材或稍后重试。')
        finally:
            with self.lock:
                self.processes.pop(tid, None)
            if proc.poll() is None:
                terminate_process(proc)

    def loop(self):
        while True:
            tid = self.pending.get()
            try:
                with self.lock:
                    if tid in self.cancelled:
                        continue
                    self.update(tid, status='running', stage='download', progress=2, message='准备视频')
                self.run_stage(tid, 'prepare')
                metadata = json.loads((self.root / tid / 'media.json').read_text(encoding='utf-8'))
                with self.lock:
                    if tid in self.cancelled:
                        raise InterruptedError()
                    self.update(tid, title=metadata['title'][:200], duration=metadata['duration'],
                                author=metadata.get('author', ''), video_ready=True, stage='waiting',
                                progress=24, message='等待本机模型空闲')
                acquired = False
                try:
                    while not acquired:
                        if tid in self.cancelled:
                            raise InterruptedError()
                        acquired = self.gpu_lock.acquire(timeout=.5)
                    self.run_stage(tid, 'transcribe')
                finally:
                    if acquired:
                        self.gpu_lock.release()
                with self.lock:
                    if tid in self.cancelled:
                        raise InterruptedError()
                    result = json.loads((self.root / tid / 'transcript.json').read_text(encoding='utf-8'))
                    if not result.get('text', '').strip():
                        raise RuntimeError('没有识别到清晰的人声，请换一段有口播的视频。')
                    self.update(tid, status='completed', stage='completed', progress=100,
                                message='文案已提取并保存', text=result['text'], saved_at=timestamp(),
                                language=result['language'], device=result['device'])
                    self.write_exports(tid, self.get(tid))
            except InterruptedError:
                self.update(tid, status='cancelled', stage='cancelled', message='提取已停止')
            except Exception as exc:
                message = re.sub(r'https?://\S+', '[链接已省略]', str(exc))[:400]
                self.update(tid, status='failed', stage='failed', error=message, message='提取未完成')
            finally:
                self.pending.task_done()

    def make_router(self):
        router = APIRouter(prefix='/api/extractions')
        router.include_router(self.clips.router)

        @router.get('/config')
        def config():
            return readiness()

        @router.get('')
        def listing():
            with self.lock:
                return [self.summary(r) for r in sorted(self.records.values(), key=lambda r: r['created_at'], reverse=True)]

        @router.post('')
        def create(values: ExtractInput):
            return self.create(values)

        @router.get('/{tid}')
        def detail(tid: str):
            return self.detail(tid)

        @router.put('/{tid}')
        def save(tid: str, values: SaveTranscript):
            return self.save(tid, values)

        @router.post('/{tid}/cancel')
        def cancel(tid: str):
            return self.cancel(tid)

        @router.post('/{tid}/retry')
        def retry(tid: str):
            task = self.get(tid)
            if task['status'] in ACTIVE:
                raise HTTPException(409, '这个任务仍在提取中。')
            return self.create(ExtractInput(share=task.get('share', ''), video_id=task.get('video_id', ''),
                                            language=task.get('language', 'zh'), keywords=task.get('keywords', '')))

        @router.get('/{tid}/media')
        def media(tid: str, original: bool = False):
            task = self.get(tid)
            if not task.get('video_ready'):
                raise HTTPException(404, '视频尚未准备好。')
            metadata = json.loads((self.root / tid / 'media.json').read_text(encoding='utf-8'))
            path = self.safe_file(tid, metadata['source_file'] if original else 'preview.mp4')
            return FileResponse(path, media_type='video/mp4', filename=path.name if original else None)

        @router.get('/{tid}/export/{kind}')
        def export(tid: str, kind: Literal['txt', 'md']):
            task = self.get(tid)
            if task['status'] != 'completed':
                raise HTTPException(409, '请先完成提取。')
            content = task['text'] + '\n' if kind == 'txt' else '# ' + task['title'] + '\n\n' + task['text'] + '\n'
            from urllib.parse import quote
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', task['title'])[:80] + '_文案.' + kind
            return Response(content, media_type='text/plain' if kind == 'txt' else 'text/markdown',
                            headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name)})

        return router
