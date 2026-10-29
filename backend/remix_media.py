from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Local reference segmentation and final assembly; no network or model billing."""
import json
import math
import re
import subprocess
from pathlib import Path
from fractions import Fraction
from settings import BIN

def ffmpeg(*args, timeout=600):
    result = subprocess.run([str(FFMPEG),'-nostdin','-hide_banner','-y',*map(str,args)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('媒体处理失败，请检查素材格式或本地磁盘空间。')
    return result.stderr

def probe(path):
    result = subprocess.run([str(FFPROBE),'-v','error','-show_format','-show_streams','-of','json',str(path)],
                            capture_output=True,text=True,encoding='utf-8',timeout=45,check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return json.loads(result.stdout)

def analyze(source, folder):
    folder = Path(folder); folder.mkdir(parents=True,exist_ok=True)
    info = probe(source)
    stream = next(s for s in info['streams'] if s['codec_type']=='video')
    duration = float(info['format']['duration'])
    if not .5 <= duration <= 180:
        raise RuntimeError('对标视频请控制在 0.5 秒到 3 分钟之间。')
    evidence = ffmpeg('-i',source,'-an','-vf',"scale=320:-2,select='gt(scene,0.28)',showinfo",'-vsync','vfr','-f','null','-')
    cuts = [0.0]
    for value in re.findall(r'pts_time:([\d.]+)', evidence):
        point = float(value)
        if point-cuts[-1] >= 2 and duration-point >= 2:
            cuts.append(point)
    cuts.append(duration)
    windows = []
    for start,end in zip(cuts,cuts[1:]):
        count = max(1,math.ceil((end-start)/10))
        for i in range(count):
            windows.append((start+(end-start)*i/count,start+(end-start)*(i+1)/count))
    if len(windows)>40:
        raise RuntimeError('镜头过多，请缩短参考视频后再拆解。')
    shots=[]
    for i,(start,end) in enumerate(windows):
        length=end-start
        frame=folder/f'shot-{i:03d}.jpg'
        ffmpeg('-ss',f'{start+min(.3,length/2):.3f}','-i',source,'-frames:v','1','-vf',"scale='min(1280,iw)':-2",'-q:v','2',frame)
        clip=folder/f'clip-{i:03d}.mp4'
        ffmpeg('-ss',f'{start:.3f}','-i',source,'-t',f'{length:.3f}',
               '-map','0:v:0','-an','-vf',"scale='min(960,iw)':-2,fps=25",'-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',clip)
        shots.append({'index':i,'start':round(start,3),'end':round(end,3),'seconds':max(4,min(15,math.ceil(length))),
                      'frame':frame.name,'clip':clip.name,'prompt':'','text':''})
    ffmpeg('-i',source,'-map','0:v:0','-map','0:a:0?','-vf',"scale='min(960,iw)':-2",'-c:v','libx264','-preset','veryfast',
           '-crf','23','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',folder/'preview.mp4')
    audio=any(s['codec_type']=='audio' for s in info['streams'])
    if audio:
        ffmpeg('-i',source,'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',folder/'audio.wav')
    (folder/'media.json').write_text(json.dumps({'duration':duration}),encoding='utf-8')
    return {'duration':round(duration,3),'width':stream['width'],'height':stream['height'],
            'has_audio':audio,'shots':shots,'method':'本地画面切换检测，长镜头按 10 秒内分段'}

def assemble_original(clips,folder,source,shots,resolution,voice=None):
    """Match each source interval before muxing its untouched original soundtrack."""
    if len(clips)!=len(shots) or not clips:raise RuntimeError('镜头数量不一致，无法对齐原片声音。')
    info=probe(source);video=next(s for s in info['streams'] if s['codec_type']=='video')
    duration=float(info['format']['duration'])
    rate=Fraction(video.get('avg_frame_rate','25/1'))
    if not 1<=float(rate)<=120:rate=Fraction(25)
    fps=float(rate)
    short={'480p':480,'720p':720,'1080p':1080}[resolution]
    scale=min(short/min(video['width'],video['height']),1)
    width=max(2,round(video['width']*scale/2)*2);height=max(2,round(video['height']*scale/2)*2)
    normalized=[]
    previous=0
    for i,(clip,shot) in enumerate(zip(clips,shots)):
        # Round cumulative boundaries, not each shot, to prevent accumulated drift.
        boundary=round(shot['end']*fps)
        count=boundary-previous
        if count<1:raise RuntimeError('参考镜头过短，无法对齐原片。')
        previous=boundary
        stream=next(s for s in probe(clip)['streams'] if s['codec_type']=='video')
        length=float(stream.get('duration') or probe(clip)['format']['duration'])
        output=folder/f'normal-{i:03d}.mp4'
        vf=(f'setpts=(PTS-STARTPTS)*{count/fps/length:.10f},'
            f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
            f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={rate},tpad=stop_mode=clone:stop_duration=1')
        ffmpeg('-i',clip,'-map','0:v:0','-an','-vf',vf,'-frames:v',count,
               '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',output)
        normalized.append(output)
    playlist=folder/'concat.txt'
    playlist.write_text('\n'.join("file '"+p.name+"'" for p in normalized),encoding='utf-8')
    joined=folder/'joined.mp4'
    ffmpeg('-f','concat','-safe','0','-i',playlist,'-c','copy',joined)
    audio=next((s for s in info['streams'] if s['codec_type']=='audio'),None)
    args=['-i',joined,'-i',voice or source,'-map','0:v:0','-map','1:a:0?','-c:v','copy']
    if voice:args+=['-c:a','aac','-b:a','192k']
    elif audio:
        args+=['-c:a','copy'] if audio['codec_name'] in {'aac','mp3','alac','ac3','eac3'} else ['-c:a','aac','-b:a','192k']
    result=folder/'result.mp4'
    ffmpeg(*args,'-t',f'{duration:.6f}','-movflags','+faststart',result)
    return result


def assemble(clips, folder, aspect, voice=None, resolution='720p', *, original=None, shots=None):
    folder=Path(folder)
    if original:return assemble_original(clips,folder,original,shots,resolution,voice)
    short={'480p':480,'720p':720,'1080p':1080}[resolution]
    long=round(short*16/9/2)*2
    width,height={'9:16':(short,long),'16:9':(long,short),'1:1':(short,short)}[aspect]
    normalized=[]
    for i,clip in enumerate(clips):
        output=folder/f'normal-{i:03d}.mp4'
        args=['-i',clip]
        has_audio=any(s['codec_type']=='audio' for s in probe(clip)['streams'])
        if not has_audio:
            args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        args+=['-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0',
               '-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=25,setsar=1',
               '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-shortest',output]
        ffmpeg(*args)
        normalized.append(output)
    playlist=folder/'concat.txt'
    playlist.write_text('\n'.join("file '"+p.name+"'" for p in normalized),encoding='utf-8')
    joined=folder/'joined.mp4'
    ffmpeg('-f','concat','-safe','0','-i',playlist,'-c','copy','-movflags','+faststart',joined)
    result=folder/'result.mp4'
    if voice:
        video_length=float(probe(joined)['format']['duration']);audio_length=float(probe(voice)['format']['duration'])
        ffmpeg('-i',joined,'-i',voice,'-map','0:v:0','-map','1:a:0',
               '-vf',f'setpts=(PTS-STARTPTS)*{audio_length/video_length:.8f},fps=25',
               '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',
               '-t',f'{audio_length:.6f}','-movflags','+faststart',result)
    else:
        import shutil
        shutil.copyfile(joined,result)
    return result
