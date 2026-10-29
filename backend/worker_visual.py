from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Local MuseTalk adapter and optional ONNX face restoration; bounded frame batches."""
import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path
from model_registry import MUSE, RESTORER
from worker import ROOT, BIN, ffmpeg, duration, progress, setup_torch

def face_detector():
    import onnxruntime
    if hasattr(onnxruntime, 'preload_dlls'):
        onnxruntime.preload_dlls()
    from insightface.app import FaceAnalysis
    detector = FaceAnalysis(allowed_modules=['detection'],
        root=str(ROOT / 'vendor/LatentSync/checkpoints/auxiliary'),
        providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
    detector.prepare(ctx_id=0, det_size=(512, 512))
    return detector

def choose_face(detector, frame):
    faces = [f for f in detector.get(frame) if f.det_score >= .5]
    if len(faces) != 1:
        raise RuntimeError('画面中需要有一张清晰人脸，请使用单人、正面、无遮挡的人物视频。')
    return faces[0]

def encoder(path, width, height, fps=25):
    return subprocess.Popen([str(FFMPEG), '-y', '-v', 'error', '-nostdin',
        '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', f'{width}x{height}',
        '-framerate', str(fps), '-i', '-', '-an', '-c:v', 'libx264', '-preset', 'fast',
        '-crf', '17', '-pix_fmt', 'yuv420p', str(path)], stdin=subprocess.PIPE,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

def finish_encoder(enc):
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError('视频写入失败，请检查磁盘空间。')

def muse(job, directory, inputs):
    import cv2
    import numpy as np
    torch = setup_torch()
    sys.path.insert(0, str(MUSE))
    from musetalk.models.unet import PositionalEncoding
    from musetalk.utils.audio_processor import AudioProcessor
    from diffusers import AutoencoderKL, UNet2DConditionModel
    from transformers import WhisperModel
    torch.manual_seed(1247)
    dtype, device = torch.float16, 'cuda'
    work = directory / 'musetalk'
    work.mkdir(exist_ok=True)
    voice = directory / 'voice.wav'
    total = duration(voice)
    frames_total = math.ceil(total * 25 - 1e-6)
    progress('lipsync', 38, '准备 MuseTalk 1.5 素材')
    base = work / 'base.mp4'
    ffmpeg('-stream_loop', '-1', '-i', inputs['video'], '-t', f'{frames_total/25:.6f}', '-an',
        '-vf', "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=25,setsar=1",
        '-c:v', 'libx264', '-crf', '17', '-pix_fmt', 'yuv420p', base)
    progress('lipsync', 40, '加载本地 MuseTalk 1.5')
    with torch.inference_mode():
        vae = AutoencoderKL.from_pretrained(str(MUSE / 'models/sd-vae'), torch_dtype=dtype,
            use_safetensors=False, local_files_only=True).to(device).eval()
        vae.enable_slicing()
        config = json.loads((MUSE / 'models/musetalkV15/musetalk.json').read_text())
        unet = UNet2DConditionModel(**{k:v for k,v in config.items() if not k.startswith('_')}).to(dtype=dtype)
        weights = torch.load(MUSE / 'models/musetalkV15/unet.pth', map_location='cpu', weights_only=True)
        unet.load_state_dict(weights)
        del weights
        unet = unet.to(device).eval()
        pe = PositionalEncoding(384).to(device, dtype).eval()
        whisper = WhisperModel.from_pretrained(str(MUSE / 'models/whisper'), torch_dtype=dtype,
            local_files_only=True).to(device).eval()
        processor = AudioProcessor(str(MUSE / 'models/whisper'))
        feat, n = processor.get_audio_feature(str(voice), weight_dtype=dtype)
        audio = processor.get_whisper_chunk(feat, device, dtype, whisper, n, fps=25).cpu()
        if not len(audio):
            raise RuntimeError('配音太短，请输入一段完整文案。')
        while len(audio) < frames_total:
            audio = torch.cat([audio, audio[-1:]])
        del whisper, processor, feat
        torch.cuda.empty_cache()
        detector = face_detector()
        cap = cv2.VideoCapture(str(base))
        width, height = int(cap.get(3)), int(cap.get(4))
        enc = encoder(work / 'silent.mp4', width, height)
        previous_box = None
        try:
            for start in range(0, frames_total, 4):
                frames, crops, boxes, landmarks = [], [], [], []
                for i in range(start, min(start + 4, frames_total)):
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError('人物视频帧数不足，请重新导入视频。')
                    face = choose_face(detector, frame)
                    x1, y1, x2, y2 = face.bbox
                    nose = face.kps[2]
                    box = np.array([x1, max(0, 2*nose[1]-y2), x2, min(height, y2 + (x2-x1)*.06)])
                    if previous_box is not None and np.max(np.abs(box-previous_box)) < 25:
                        box = box*.75 + previous_box*.25
                    previous_box = box
                    a,b,c,d = np.round(box).astype(int)
                    a,b,c,d = max(0,a),max(0,b),min(width,c),min(height,d)
                    if c-a < 32 or d-b < 32:
                        raise RuntimeError('人脸在画面中太小，请换一段更清晰的近景视频。')
                    crops.append(cv2.cvtColor(cv2.resize(frame[b:d,a:c], (256,256)), cv2.COLOR_BGR2RGB))
                    frames.append(frame); boxes.append((a,b,c,d)); landmarks.append(face.kps)
                rgb = torch.from_numpy(np.stack(crops)).permute(0,3,1,2).to(device,dtype)/255
                masked = rgb.clone(); masked[:,:,128:] = 0
                # Deterministic VAE means avoid adding independent sampling noise to each frame.
                lat = vae.encode(torch.cat([masked*2-1,rgb*2-1])).latent_dist.mode()*vae.config.scaling_factor
                latent = torch.cat([lat[:len(frames)], lat[len(frames):]], dim=1)
                conditioning = pe(audio[start:start+len(frames)].to(device,dtype))
                pred = unet(latent, torch.tensor([0], device=device), encoder_hidden_states=conditioning).sample
                decoded = (vae.decode(pred/vae.config.scaling_factor).sample/2+.5).clamp(0,1)
                if not torch.isfinite(decoded).all():
                    raise RuntimeError('MuseTalk 输出异常，请重试或选择另一个模型。')
                recon = (decoded.permute(0,2,3,1).float().cpu().numpy()*255).round().astype(np.uint8)[...,::-1]
                for frame, patch, box, points in zip(frames,recon,boxes,landmarks):
                    a,b,c,d = box
                    original = frame[b:d,a:c].astype(np.float32)
                    patch = cv2.resize(patch, (c-a,d-b), interpolation=cv2.INTER_CUBIC).astype(np.float32)
                    mask = np.zeros((d-b,c-a), np.float32)
                    nose = points[2]; mouth = (points[3]+points[4])/2
                    cx = mouth[0]-a; cy = (nose[1]+d)/2-b
                    cv2.ellipse(mask, (round(cx),round(cy)), (max(1,round((c-a)*.39)),max(1,round((d-nose[1])*.51))), 0,0,360,1.,-1)
                    mask = cv2.GaussianBlur(mask,(0,0),max(3,(c-a)*.025))
                    mask *= np.clip((np.arange(d-b)[:,None]+b-(nose[1]-6))/15,0,1)
                    ring = (mask>.15)&(mask<.65)
                    if ring.sum()>50:
                        patch = np.clip(patch+np.clip(np.median(original[ring]-patch[ring],axis=0),-12,12),0,255)
                    frame[b:d,a:c] = np.clip(patch*mask[:,:,None]+original*(1-mask[:,:,None]),0,255).astype(np.uint8)
                    enc.stdin.write(frame.tobytes())
                progress('lipsync', 43+49*(start+len(frames))/frames_total, f'MuseTalk 正在对口型 · {start+len(frames)} / {frames_total} 帧')
        finally:
            cap.release()
            finish_encoder(enc)
    ffmpeg('-i', work/'silent.mp4', '-i', voice, '-map','0:v:0','-map','1:a:0','-t',f'{total:.6f}',
           '-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',directory/'result.mp4')
    progress('export',94,'MuseTalk 视频完成')

def restore(job, directory, inputs):
    import cv2
    import numpy as np
    setup_torch()
    import onnxruntime as ort
    if hasattr(ort,'preload_dlls'):
        ort.preload_dlls()
    progress('restore',95,'加载本地人脸修复模型')
    session = ort.InferenceSession(str(RESTORER), providers=['CUDAExecutionProvider','CPUExecutionProvider'])
    detector = face_detector()
    raw = directory/'unrestored.mp4'
    if not raw.exists():
        (directory/'result.mp4').replace(raw)
    cap = cv2.VideoCapture(str(raw))
    width,height,count,fps = int(cap.get(3)),int(cap.get(4)),int(cap.get(7)),cap.get(5)
    enc = encoder(directory/'restored-silent.mp4',width,height,fps)
    strength = float(job.get('restoration_strength',.35))
    template = np.array([[192.98138,239.94708],[318.90277,240.1936],[256.63416,314.01935],
                         [201.26117,371.41043],[313.08905,371.15118]],dtype=np.float32)
    mask = np.zeros((512,512),np.float32)
    cv2.ellipse(mask,(256,278),(180,205),0,0,360,1.,-1)
    mask = cv2.GaussianBlur(mask,(0,0),20)*strength
    index=0
    try:
        while True:
            ok, frame = cap.read()
            if not ok: break
            face = choose_face(detector,frame)
            matrix,_ = cv2.estimateAffinePartial2D(face.kps.astype(np.float32),template,method=cv2.LMEDS)
            if matrix is None:
                raise RuntimeError('人脸对齐失败，请换一段正面视频。')
            crop = cv2.warpAffine(frame,matrix,(512,512),borderMode=cv2.BORDER_REFLECT)
            tensor = (cv2.cvtColor(crop,cv2.COLOR_BGR2RGB).astype(np.float32)/127.5-1).transpose(2,0,1)[None]
            restored = session.run(None,{session.get_inputs()[0].name:tensor})[0]
            if not np.isfinite(restored).all():
                raise RuntimeError('人脸修复输出异常，请关闭修复后重试。')
            patch = np.clip((restored[0].transpose(1,2,0)+1)*127.5,0,255).astype(np.uint8)[:,:,::-1]
            inverse = cv2.invertAffineTransform(matrix)
            patch = cv2.warpAffine(patch,inverse,(width,height)).astype(np.float32)
            alpha = cv2.warpAffine(mask,inverse,(width,height))[:,:,None]
            result = np.clip(patch*alpha+frame.astype(np.float32)*(1-alpha),0,255).astype(np.uint8)
            enc.stdin.write(result.tobytes()); index+=1
            if index%10==0 or index==count:
                progress('restore',95+3*index/max(count,1),f'人脸修复 · {index} / {count} 帧')
    finally:
        cap.release(); finish_encoder(enc)
    if index != count or index == 0:
        raise RuntimeError('人脸修复视频帧数不完整，请重试。')
    ffmpeg('-i',directory/'restored-silent.mp4','-i',raw,'-map','0:v:0','-map','1:a:0',
           '-c:v','copy','-c:a','copy','-movflags','+faststart',directory/'result.mp4')
    progress('export',99,'修复完成，检查成片')

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    stage,path = sys.argv[1],Path(sys.argv[2]).resolve()
    job = json.loads(path.read_text(encoding='utf-8'))
    inputs = json.loads((path.parent/'inputs.json').read_text(encoding='utf-8'))
    try:
        {'lipsync':muse,'restore':restore}[stage](job,path.parent,inputs)
    except Exception as exc:
        traceback.print_exc()
        if isinstance(exc,RuntimeError) and any('\u4e00'<=c<='\u9fff' for c in str(exc)):
            print('@@ERROR '+str(exc).replace('\n',' ')[:500],flush=True)
        sys.exit(1)
