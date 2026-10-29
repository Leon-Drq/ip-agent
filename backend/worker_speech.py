"""Local VoxCPM2 / CosyVoice 3 inference, isolated from the existing engines."""
import json
import os
import sys
import traceback
from pathlib import Path
from settings import ROOT, environment
from worker import progress, setup_torch
from speech_options import VOX_STYLES, COSY_STYLES, split_speech


def synthesize(job, directory):
    import numpy as np
    import soundfile as sf
    torch = setup_torch()
    torch.manual_seed(42)
    reference = str(directory / 'reference.wav')
    preset = job.get('emotion_preset', 'happy') if job.get('emotion_mode') == 'preset' else None
    pieces = []
    if job['tts_model'] == 'voxcpm2':
        from voxcpm import VoxCPM
        progress('tts', 16, '加载本地 VoxCPM2')
        engine = VoxCPM.from_pretrained(str(ROOT / 'models/VoxCPM2'), load_denoiser=False,
                                       optimize=False, local_files_only=True, device='cuda')
        sample_rate = engine.tts_model.sample_rate
        chunks = split_speech(job['text'])
        for i, text in enumerate(chunks):
            progress('tts', 22 + 10 * i / len(chunks), f'VoxCPM2 正在配音 · {i + 1}/{len(chunks)}')
            if preset:
                text = '(' + VOX_STYLES[preset] + ')' + text
            wav = engine.generate(text=text, reference_wav_path=reference,
                                  cfg_value=2.0, inference_timesteps=10, normalize=False,
                                  denoise=False, retry_badcase=True, seed=42 + i)
            if pieces:
                pieces.append(np.zeros(int(sample_rate * .12), dtype=np.float32))
            pieces.append(np.asarray(wav, dtype=np.float32).reshape(-1))
    else:
        repo = ROOT / 'vendor/CosyVoice'
        sys.path[:0] = [str(repo), str(repo / 'third_party/Matcha-TTS')]
        import onnxruntime
        onnxruntime.set_default_logger_severity(3)
        # Text normalization must also work offline, including Chinese numbers.
        import wetext.wetext as wetext_module
        normalizer_dir = ROOT / 'models/wetext'
        for lang in ('zh', 'en'):
            for name in ('tagger.fst', 'verbalizer.fst'):
                if not (normalizer_dir / lang / 'tn' / name).is_file():
                    raise RuntimeError('中文 / 英文读数资源尚未安装完成。')
        def local_normalizer(repo_id, *args, **kwargs):
            if repo_id != 'pengzhendong/wetext':
                raise RuntimeError('Unexpected text normalization model')
            return str(normalizer_dir)
        wetext_module.snapshot_download = local_normalizer
        from cosyvoice.cli.cosyvoice import AutoModel
        progress('tts', 16, '加载本地 CosyVoice 3')
        engine = AutoModel(model_dir=str(ROOT / 'models/CosyVoice3'), load_trt=False,
                           load_vllm=False, fp16=True)
        sample_rate = engine.sample_rate
        progress('tts', 23, 'CosyVoice 3 正在生成配音')
        if preset:
            instruction = 'You are a helpful assistant. ' + COSY_STYLES[preset] + '<|endofprompt|>'
            output = engine.inference_instruct2(job['text'], instruction, reference, stream=False)
        else:
            prompt_text = job.get('reference_text', '').strip()
            if not prompt_text:
                raise RuntimeError('CosyVoice 3 跟随原声需要参考录音原文，请重新生成以自动识别。')
            output = engine.inference_zero_shot(job['text'], 'You are a helpful assistant.<|endofprompt|>' + prompt_text,
                                                reference, stream=False)
        for item in output:
            pieces.append(item['tts_speech'].detach().float().cpu().numpy().reshape(-1))
    if not pieces:
        raise RuntimeError('模型没有生成声音，请更换参考录音或缩短文案后重试。')
    audio = np.concatenate(pieces)
    if not np.isfinite(audio).all() or len(audio) < sample_rate / 4:
        raise RuntimeError('生成的音频无效，请重试。')
    sf.write(str(directory / 'voice.wav'), audio, sample_rate, subtype='PCM_16')
    progress('tts', 34, '配音完成')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ.update(environment())
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      TOKENIZERS_PARALLELISM='false', TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1')
    try:
        path = Path(sys.argv[2]).resolve()
        synthesize(json.loads(path.read_text(encoding='utf-8')), path.parent)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
