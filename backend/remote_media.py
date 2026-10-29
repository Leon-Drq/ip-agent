"""Local browser preview cache. Original uploads and generated downloads are unchanged."""
import asyncio
import hashlib
import re
import subprocess
from pathlib import Path

PREVIEW_RANGE=1024*1024

def bounded_range(value):
    match=re.fullmatch(r'bytes=(\d+)-(\d*)',value or '')
    if not match:return value
    start=int(match[1]);end=min(int(match[2]) if match[2] else start+PREVIEW_RANGE-1,start+PREVIEW_RANGE-1)
    return f'bytes={start}-{end}' if end>=start else value

class PreviewCache:
    def __init__(self,directory,ffmpeg):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.ffmpeg=Path(ffmpeg);self.tasks={};self.slots=asyncio.Semaphore(2)

    async def get(self,source,kind):
        source=Path(source);stat=source.stat()
        key=hashlib.sha256(f'v1:{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{kind}'.encode()).hexdigest()
        output=self.directory/(key+('.mp4' if kind=='video' else '.mp3'))
        if output.exists():return output
        if key not in self.tasks:
            self.tasks[key]=asyncio.create_task(self.convert(source,kind,output))
            def completed(task):
                self.tasks.pop(key,None)
                if not task.cancelled():task.exception()
            self.tasks[key].add_done_callback(completed)
        return await asyncio.shield(self.tasks[key])

    async def convert(self,source,kind,output):
        async with self.slots:
            temporary=output.with_name(output.stem+'.partial'+output.suffix)
            cmd=[str(self.ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-y','-threads','2','-i',str(source)]
            if kind=='video':
                cmd+=['-map','0:v:0','-map','0:a:0?','-vf',"scale='min(768,iw)':'min(768,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=24",'-c:v','libx264','-threads','2','-preset','veryfast','-crf','28','-maxrate','700k','-bufsize','1400k','-g','48','-pix_fmt','yuv420p','-c:a','aac','-b:a','64k','-ac','1','-movflags','+faststart']
            else:cmd+=['-map','0:a:0','-vn','-c:a','libmp3lame','-b:a','96k','-ar','44100','-ac','1']
            cmd+=[str(temporary)]
            try:
                result=await asyncio.to_thread(subprocess.run,cmd,capture_output=True,timeout=180,creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode or not temporary.exists():raise RuntimeError('无法准备网页预览，请检查素材格式。')
                temporary.replace(output);return output
            finally:temporary.unlink(missing_ok=True)
