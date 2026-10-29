"""Run the user's IndexTTS 2 in its own bundled Python and release it afterward."""
import os
import sys
import json
import traceback
from pathlib import Path
from model_registry import INDEX
from worker import progress, environment
from speech_options import index_emotion_kwargs

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    path = Path(sys.argv[2]).resolve()
    job = json.loads(path.read_text(encoding='utf-8'))
    os.environ.update(environment())
    os.environ.update(HF_HOME=str(INDEX / 'checkpoints'), TRANSFORMERS_CACHE=str(INDEX / 'tf_download'),
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      TOKENIZERS_PARALLELISM='false', TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1')
    os.chdir(INDEX)
    sys.path.insert(0, str(INDEX))
    try:
        import torch
        torch.set_num_threads(8)
        torch.manual_seed(42)
        from indextts.infer_v2 import IndexTTS2
        progress('tts', 18, '加载本地 IndexTTS 2')
        engine = IndexTTS2(cfg_path=str(INDEX / 'checkpoints/config.yaml'), model_dir=str(INDEX / 'checkpoints'),
                           is_fp16=True, device='cuda', use_cuda_kernel=False)
        progress('tts', 23, 'IndexTTS 2 正在生成配音')
        engine.infer(spk_audio_prompt=str(path.parent / 'reference.wav'), text=job['text'],
                     output_path=str(path.parent / 'voice.wav'), use_random=False,
                     max_text_tokens_per_sentence=120, verbose=False,
                     **index_emotion_kwargs(job, path.parent))
        if not (path.parent / 'voice.wav').is_file():
            raise RuntimeError('IndexTTS 2 未生成音频，请查看任务日志。')
        progress('tts', 34, 'IndexTTS 2 配音完成')
    except Exception:
        traceback.print_exc()
        sys.exit(1)
