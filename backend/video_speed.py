from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Retime complete rendered videos: motion and audio change speed together."""
import json
import math
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from settings import BIN

def adjust_video_speed(directory, factor, include_unrestored=False):
    factor = float(factor)
    if not math.isfinite(factor) or not .5 <= factor <= 2:
        raise ValueError('视频速度需在 0.5～2.0 倍之间。')
    directory = Path(directory)
    names = ['result.mp4'] + (['unrestored.mp4'] if include_unrestored else [])
    for name in names:
        video = directory / name
        original = video.with_stem(video.stem + '-normal')
        if not video.is_file():
            raise RuntimeError('尚未生成完整视频，无法调整播放速度。')
        if not original.is_file():
            if math.isclose(factor, 1, abs_tol=1e-8):
                continue
            staged = original.with_suffix('.tmp.mp4')
            shutil.copyfile(video, staged)
            staged.replace(original)
        temporary = video.with_suffix('.speed.tmp.mp4')
        try:
            if math.isclose(factor, 1, abs_tol=1e-8):
                shutil.copyfile(original, temporary)
            else:
                info = subprocess.run([str(FFPROBE), '-v', 'error', '-show_streams', '-of', 'json', str(original)],
                                      capture_output=True, text=True, timeout=60, check=True,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                streams = json.loads(info.stdout)['streams']
                video_stream = next(s for s in streams if s['codec_type'] == 'video')
                if not any(s['codec_type'] == 'audio' for s in streams):
                    raise RuntimeError('成片缺少音轨，请重新生成视频。')
                try:
                    rate = Fraction(video_stream.get('avg_frame_rate') or '25/1')
                except (ValueError, ZeroDivisionError):
                    rate = Fraction(25)
                if not 1 <= rate <= 120:
                    rate = Fraction(25)
                result = subprocess.run([str(FFMPEG), '-nostdin', '-hide_banner', '-v', 'error', '-y',
                                         '-i', str(original), '-map', '0:v:0', '-map', '0:a:0',
                                         '-vf', f'setpts=(PTS-STARTPTS)/{factor:.6f},fps={rate.numerator}/{rate.denominator}',
                                         '-af', f'atempo={factor:.6f},asetpts=PTS-STARTPTS',
                                         '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                                         '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-shortest', str(temporary)],
                                        capture_output=True, timeout=7200, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode or not temporary.is_file() or temporary.stat().st_size < 100:
                    raise RuntimeError('整体视频调速失败，请重新生成。')
            temporary.replace(video)
        finally:
            temporary.unlink(missing_ok=True)
