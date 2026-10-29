from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Accurate, persistent clip exports from extraction source videos."""
import copy
import json
import math
import queue
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from settings import BIN


class ClipInput(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


def now():
    return datetime.now().isoformat(timespec='milliseconds')


class ExtractionClips:
    def __init__(self, extraction, start=True):
        self.extraction = extraction
        self.lock = threading.RLock()
        self.thumbnail_lock = threading.Lock()
        self.records = {}
        self.pending = queue.Queue()
        for tid in extraction.records:
            for path in (extraction.root / tid / 'clips').glob('*/clip.json'):
                try:
                    row = json.loads(path.read_text(encoding='utf-8'))
                    if row['task_id'] != tid or row['id'] != path.parent.name or not re.fullmatch('[0-9a-f]{32}', row['id']):
                        continue
                    self.records[(tid, row['id'])] = row
                    if row['status'] in {'queued', 'running'}:
                        self.update(tid, row['id'], status='failed', error='上次导出被中断，请重新截取。')
                except (ValueError, KeyError, OSError):
                    continue
        self.router = self.routes()
        if start:
            threading.Thread(target=self.loop, daemon=True, name='extraction-clips').start()

    def folder(self, tid, cid):
        return self.extraction.root / tid / 'clips' / cid

    def update(self, tid, cid, **changes):
        with self.lock:
            row = {**self.records[(tid, cid)], **changes, 'updated_at': now()}
            target = self.folder(tid, cid) / 'clip.json'
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(target)
            self.records[(tid, cid)] = row
            return copy.deepcopy(row)

    def source(self, tid):
        task = self.extraction.get(tid)
        if not task.get('video_ready'):
            raise HTTPException(409, '原视频尚未准备好，请等待下载完成。')
        meta = json.loads((self.extraction.root / tid / 'media.json').read_text(encoding='utf-8'))
        path = self.extraction.safe_file(tid, meta['source_file'])
        duration = float(meta.get('duration') or task.get('duration') or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise HTTPException(400, '无法读取视频时长，请重新提取视频。')
        return task, path, duration

    def create(self, tid, values):
        task, _, duration = self.source(tid)
        start, end = round(values.start, 3), round(values.end, 3)
        if end > duration + .001 or start >= duration or round(end - start, 3) < .1:
            raise HTTPException(400, '请选择视频范围内的开始和结束时间，片段至少 0.1 秒。')
        end = min(end, duration)
        with self.lock:
            for (task_id, cid), row in self.records.items():
                if task_id == tid and row['start'] == start and row['end'] == end:
                    if row['status'] in {'queued', 'running'} or row['status'] == 'completed' and (self.folder(tid, cid) / 'result.mp4').is_file():
                        return copy.deepcopy(row)
            if sum(r['status'] in {'queued', 'running'} for r in self.records.values()) >= 3:
                raise HTTPException(409, '已有 3 个片段正在导出，请稍后再试。')
            if shutil.disk_usage(self.extraction.root).free < 1024**3:
                raise HTTPException(400, '本地剩余空间不足 1 GB，请先腾出空间。')
            cid = uuid.uuid4().hex
            self.folder(tid, cid).mkdir(parents=True)
            self.records[(tid, cid)] = dict(id=cid, task_id=tid, title=task['title'], start=start, end=end,
                                           duration=round(end-start, 3), created_at=now(), status='queued', error=None)
            row = self.update(tid, cid)
            self.pending.put((tid, cid))
            return row

    def process(self, tid, cid):
        temporary = self.folder(tid, cid) / 'exporting.mp4'
        try:
            _, source, _ = self.source(tid)
            row = self.update(tid, cid, status='running', error=None)
            result = subprocess.run([
                str(FFMPEG), '-nostdin', '-hide_banner', '-v', 'error', '-y',
                '-ss', str(row['start']), '-i', str(source), '-t', str(row['duration']),
                '-map', '0:v:0', '-map', '0:a:0?', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-threads', '4',
                '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(temporary)
            ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError('片段导出失败，请检查原视频是否可播放，或换一个截取范围后重试。')
            temporary.replace(self.folder(tid, cid) / 'result.mp4')
            self.update(tid, cid, status='completed')
        except subprocess.TimeoutExpired:
            self.update(tid, cid, status='failed', error='导出超过 30 分钟，请缩短片段后重试。')
        except Exception as exc:
            message = exc.detail if isinstance(exc, HTTPException) else str(exc)
            self.update(tid, cid, status='failed', error=message[:350])
        finally:
            temporary.unlink(missing_ok=True)

    def loop(self):
        while True:
            tid, cid = self.pending.get()
            try:
                self.process(tid, cid)
            finally:
                self.pending.task_done()

    def timeline(self, tid):
        _, source, duration = self.source(tid)
        directory = self.extraction.root / tid / 'clips'
        target = directory / 'timeline.jpg'
        with self.thumbnail_lock:
            if target.is_file():
                return target
            directory.mkdir(exist_ok=True)
            frames = directory / 'timeline-frames'
            frames.mkdir(exist_ok=True)
            preview = self.extraction.root / tid / 'preview.mp4'
            source = preview if preview.is_file() else source
            try:
                for i in range(8):
                    subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-y',
                        '-ss', str(duration*(i+.25)/8), '-i', str(source), '-frames:v', '1',
                        '-vf', 'scale=160:90:force_original_aspect_ratio=increase,crop=160:90',
                        '-threads', '2', '-q:v', '4', str(frames/f'{i:02d}.jpg')],
                        check=True, capture_output=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                temporary = directory / 'timeline-building.jpg'
                subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-y',
                    '-i', str(frames/'%02d.jpg'), '-vf', 'tile=8x1', '-frames:v', '1', '-q:v', '3', str(temporary)],
                    check=True, capture_output=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                temporary.replace(target)
            except (subprocess.SubprocessError, OSError):
                raise HTTPException(500, '缩略图暂时未能读取，仍可选择时间段导出。') from None
        return target

    def routes(self):
        router = APIRouter()

        @router.get('/{tid}/timeline')
        def timeline(tid: str):
            return FileResponse(self.timeline(tid), media_type='image/jpeg')

        @router.get('/{tid}/clips')
        def listing(tid: str):
            self.extraction.get(tid)
            with self.lock:
                return sorted([copy.deepcopy(r) for (task_id, _), r in self.records.items() if task_id == tid],
                              key=lambda r: r['created_at'], reverse=True)

        @router.post('/{tid}/clips')
        def create(tid: str, values: ClipInput):
            return self.create(tid, values)

        @router.get('/{tid}/clips/{cid}/media')
        def media(tid: str, cid: str, download: bool = False):
            self.extraction.get(tid)
            with self.lock:
                row = self.records.get((tid, cid))
                if not row or row['status'] != 'completed':
                    raise HTTPException(404, '片段尚未导出完成。')
                path = self.folder(tid, cid) / 'result.mp4'
                if not path.is_file():
                    raise HTTPException(404, '片段文件不存在，请重新截取。')
                name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', row['title'])[:60]
                name += f"_片段_{row['start']:g}-{row['end']:g}秒.mp4"
                return FileResponse(path, media_type='video/mp4', filename=name if download else None)

        return router
