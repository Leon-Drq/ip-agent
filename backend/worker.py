from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Isolated local inference workers. Each process releases GPU memory on exit."""
import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path
from settings import ROOT, BIN, environment

os.environ.update(environment())
# All checkpoints are downloaded from the official, recorded repositories.
os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'
os.environ['OMP_NUM_THREADS'] = '8'
os.environ['language'] = 'zh_CN'

def progress(stage, value, message, **extra):
    print('\n@@PROGRESS ' + json.dumps(dict(stage=stage, progress=round(value, 1), message=message, **extra), ensure_ascii=False), flush=True)

def ffmpeg(*args):
    result = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-y', '-nostdin', *map(str, args)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('媒体处理失败：' + result.stderr[-1500:])

def duration(path):
    result = subprocess.run([str(FFPROBE), '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(path)],
                            capture_output=True, text=True, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return float(result.stdout)

def setup_torch():
    import torch
    torch.set_num_threads(8)
    if not torch.cuda.is_available():
        raise RuntimeError('未检测到可用的 NVIDIA 显卡，请检查驱动。')
    return torch

def prepare(job, directory, inputs):
    progress('prepare', 4, '提取参考声音，保留前 10 秒')
    ffmpeg('-i', inputs['reference'], '-t', '10', '-vn', '-ac', '1', '-ar', '32000', '-c:a', 'pcm_s16le', directory / 'reference.wav')
    if duration(directory / 'reference.wav') < 3:
        raise RuntimeError('参考声音不足 3 秒，请更换录音。')
    if job.get('emotion_mode') == 'audio':
        ffmpeg('-i', inputs['emotion_reference'], '-t', '10', '-vn', '-ac', '1', '-ar', '32000', '-c:a', 'pcm_s16le', directory / 'emotion.wav')
        if duration(directory / 'emotion.wav') < 3:
            raise RuntimeError('情绪参考声音不足 3 秒，请更换录音。')

def transcribe(job, directory, inputs):
    progress('transcribe', 8, '识别参考声音的原文')
    setup_torch()
    import whisper
    model = whisper.load_model(str(ROOT / 'vendor/LatentSync/checkpoints/whisper/tiny.pt'), device='cuda')
    language = job.get('reference_language', 'auto')
    result = model.transcribe(str(directory / 'reference.wav'), language=None if language == 'auto' else language,
                              fp16=True, verbose=False)
    text = result['text'].strip()
    if not text:
        raise RuntimeError('没有识别到参考录音里的语音，请换一段清晰的录音，或手动填写录音原文。')
    detected = result.get('language', 'zh')
    progress('transcribe', 12, '参考原文已识别', reference_text=text, reference_language='en' if detected == 'en' else 'zh')

def tts(job, directory, inputs):
    progress('tts', 15, '加载你的声音')
    setup_torch()
    repo = ROOT / 'vendor/GPT-SoVITS'
    os.chdir(repo)
    sys.path[:0] = [str(repo), str(repo / 'GPT_SoVITS')]
    from TTS_infer_pack.TTS import TTS, TTS_Config
    import numpy as np
    import soundfile as sf
    config = TTS_Config({'custom': {
        'device': 'cuda', 'is_half': True, 'version': 'v2ProPlus',
        't2s_weights_path': 'GPT_SoVITS/pretrained_models/s1v3.ckpt',
        'vits_weights_path': 'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth',
        'bert_base_path': 'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large',
        'cnhuhbert_base_path': 'GPT_SoVITS/pretrained_models/chinese-hubert-base',
    }})
    engine = TTS(config)
    progress('tts', 22, '按新文案生成配音')
    reference_language = job.get('reference_language', 'auto')
    if reference_language == 'auto':
        reference_language = 'zh' if any('\u4e00' <= c <= '\u9fff' for c in job['reference_text']) else 'en'
    pieces = []
    params = {
        'text': job['text'], 'text_lang': job.get('language', 'zh'),
        'ref_audio_path': str(directory / 'reference.wav'),
        'prompt_text': job['reference_text'], 'prompt_lang': reference_language,
        'text_split_method': 'cut5', 'batch_size': 1, 'split_bucket': True,
        'top_k': 15, 'top_p': 1.0, 'temperature': 1.0, 'speed_factor': 1.0,
        'seed': 42, 'parallel_infer': True, 'use_cuda_graph': False,
        'return_fragment': False, 'fragment_interval': 0.3, 'repetition_penalty': 1.35,
    }
    sample_rate = 32000
    for sample_rate, audio in engine.run(params):
        pieces.append(audio)
    if not pieces or sum(len(p) for p in pieces) < sample_rate * 0.2:
        raise RuntimeError('配音过短，请使用完整的一句话。')
    sf.write(str(directory / 'voice.wav'), np.concatenate(pieces), sample_rate, subtype='PCM_16')
    progress('tts', 35, '配音已生成')

def lipsync(job, directory, inputs):
    torch = setup_torch()
    repo = ROOT / 'vendor/LatentSync'
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    progress('lipsync', 38, '加载 LatentSync 1.5')
    import onnxruntime
    if hasattr(onnxruntime, 'preload_dlls'):
        onnxruntime.preload_dlls()
    from omegaconf import OmegaConf
    from diffusers import AutoencoderKL, DDIMScheduler
    from latentsync.models.unet import UNet3DConditionModel
    from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline
    from latentsync.whisper.audio2feature import Audio2Feature
    from accelerate.utils import set_seed
    from DeepCache import DeepCacheSDHelper
    config = OmegaConf.load(repo / 'configs/unet/stage2.yaml')
    dtype = torch.float16
    vae = AutoencoderKL.from_pretrained(str(ROOT / 'models/sd-vae-ft-mse'), torch_dtype=dtype, local_files_only=True)
    vae.config.scaling_factor = 0.18215
    vae.config.shift_factor = 0
    vae.enable_slicing()
    audio_encoder = Audio2Feature(model_path=str(repo / 'checkpoints/whisper/tiny.pt'), device='cuda',
                                 num_frames=config.data.num_frames, audio_feat_length=config.data.audio_feat_length)
    unet, _ = UNet3DConditionModel.from_pretrained(OmegaConf.to_container(config.model),
                                                str(repo / 'checkpoints/latentsync_unet.pt'), device='cpu')
    pipeline = LipsyncPipeline(vae=vae, audio_encoder=audio_encoder, unet=unet.to(dtype=dtype),
                              scheduler=DDIMScheduler.from_pretrained(str(repo / 'configs'))).to('cuda')
    helper = DeepCacheSDHelper(pipe=pipeline)
    helper.set_params(cache_interval=3, cache_branch_id=0)
    helper.enable()
    set_seed(1247)
    voice = directory / 'voice.wav'
    total = duration(voice)
    work = directory / 'segments'
    work.mkdir(exist_ok=True)
    # Bound video memory usage and preserve the source aspect ratio.
    progress('lipsync', 40, '整理画面，准备对口型')
    base = work / 'base.mp4'
    ffmpeg('-stream_loop', '-1', '-i', inputs['video'], '-t', f'{total + 0.08:.6f}', '-an',
           '-vf', "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=25,setsar=1",
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', base)
    chunk_seconds = 7.68  # 192 frames = 12 complete 16-frame inference batches.
    count = math.ceil(total / chunk_seconds)
    outputs = []
    for index in range(count):
        start = index * chunk_seconds
        length = min(chunk_seconds, total - start)
        # The model accepts a short final batch; pad very short tails to 0.64 seconds.
        infer_length = max(0.64, math.ceil(length * 25 - 1e-6) / 25)
        source = work / f'input_{index:04d}.mp4'
        audio = work / f'audio_{index:04d}.wav'
        output = work / f'output_{index:04d}.mp4'
        ffmpeg('-ss', f'{start:.6f}', '-i', base, '-t', f'{infer_length:.6f}', '-an',
               '-vf', 'tpad=stop_mode=clone:stop_duration=1', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', source)
        ffmpeg('-ss', f'{start:.6f}', '-i', voice, '-t', f'{infer_length:.6f}', '-af', 'apad', '-ac', '1', '-ar', '16000', audio)
        calls = 0
        steps = 30 if job.get('quality') == 'fine' else 20
        expected = max(1, math.ceil(infer_length * 25 / 16)) * steps
        def callback(*args):
            nonlocal calls
            calls += 1
            if calls % 5 == 0 or calls == expected:
                fraction = (start + min(1, calls / expected) * length) / total
                progress('lipsync', 40 + 55 * fraction, f'正在对口型 · 第 {index + 1} / {count} 段')
        pipeline(video_path=str(source), audio_path=str(audio), video_out_path=str(output),
                 num_frames=16, num_inference_steps=steps, guidance_scale=1.5,
                 weight_dtype=dtype, width=256, height=256,
                 mask_image_path=str(repo / 'latentsync/utils/mask.png'),
                 temp_dir=str(work / f'temp_{index:04d}'), callback=callback)
        if not output.exists():
            raise RuntimeError('对口型完成后未能写出视频，请查看本地日志。')
        # Whisper can produce 1–2 extra frame features. Trim each chunk before joining,
        # otherwise the small excess accumulates into visible audio drift on long scripts.
        aligned = work / f'aligned_{index:04d}.mp4'
        frame_count = round(chunk_seconds * 25) if index < count - 1 else math.ceil(length * 25 - 1e-6)
        ffmpeg('-i', output, '-an', '-vf', 'tpad=stop_mode=clone:stop_duration=1',
               '-frames:v', frame_count, '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', aligned)
        outputs.append(aligned)
    progress('export', 97, '合并画面与声音')
    playlist = work / 'concat.txt'
    playlist.write_text('\n'.join("file '" + p.as_posix() + "'" for p in outputs), encoding='utf-8')
    ffmpeg('-f', 'concat', '-safe', '0', '-i', playlist, '-i', voice, '-map', '0:v:0', '-map', '1:a:0',
           '-t', f'{total:.6f}', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', directory / 'result.mp4')
    progress('export', 99, '检查成片')

def video_speed(job, directory, inputs):
    from video_speed import adjust_video_speed
    factor = job.get('video_speed', 1.0)
    progress('speed', 99, f'调整整段视频速度 · {factor:g} 倍')
    adjust_video_speed(directory, factor, include_unrestored=job.get('restoration_model', 'none') != 'none')
    progress('speed', 99.5, '视频画面与声音已同步调速')

if __name__ == '__main__':
    stage, job_path = sys.argv[1:3]
    path = Path(job_path).resolve()
    job = json.loads(path.read_text(encoding='utf-8'))
    inputs = json.loads((path.parent / 'inputs.json').read_text(encoding='utf-8'))
    try:
        {'prepare': prepare, 'transcribe': transcribe, 'tts': tts, 'speed': video_speed, 'lipsync': lipsync}[stage](job, path.parent, inputs)
    except Exception as exc:
        traceback.print_exc()
        # Only controlled application errors are directly presented in the interface.
        if isinstance(exc, RuntimeError) and any('\u4e00' <= c <= '\u9fff' for c in str(exc)):
            print('@@ERROR ' + str(exc).replace('\n', ' ')[:500], flush=True)
        sys.exit(1)
