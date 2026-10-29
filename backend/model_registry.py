from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Trusted local engine paths. No browser-supplied commands or imports."""
from pathlib import Path
from settings import ROOT, PYTHON
from speech_options import capabilities

INDEX = ROOT / 'vendor/IndexTTS'
MUSE = ROOT / 'vendor/MuseTalk'
RESTORER = ROOT / 'models/GNv1.4.onnx'
TTS_DEFAULT = 'gpt_sovits'
LIP_DEFAULT = 'latentsync_15'

def catalog():
    from cloud_speech import MODELS, cloud
    specs = {
        'tts': [
            ('gpt_sovits', 'GPT-SoVITS v2ProPlus', '需要准确的参考录音原文', [ROOT / 'models/runtime-ready.json', PYTHON]),
            ('indextts2', 'IndexTTS 2', '中文可优先试用，无需参考录音原文',
             [venv_python('.indexvenv'), INDEX / 'indextts/infer_v2.py', INDEX / 'checkpoints/gpt.pth', INDEX / 'checkpoints/s2mel.pth']),
            ('voxcpm2', 'VoxCPM2', '48 kHz 克隆声音 · 支持情感预设 · 无需参考原文',
             [venv_python('.speechvenv'), ROOT / 'models/VoxCPM2/ready.json', ROOT / 'models/VoxCPM2/model.safetensors', ROOT / 'models/VoxCPM2/audiovae.pth']),
            ('cosyvoice3', 'CosyVoice 3 · 0.5B', '中文克隆 · 开心 / 悲伤 / 生气 · 跟随原声时自动识别原文',
             [venv_python('.speechvenv'), *[ROOT / 'models/CosyVoice3' / name for name in
                ('ready.json', 'cosyvoice3.yaml', 'llm.pt', 'flow.pt', 'hift.pt', 'campplus.onnx', 'speech_tokenizer_v3.onnx', 'CosyVoice-BlankEN/model.safetensors')],
                *[ROOT / 'models/wetext' / lang / 'tn' / name for lang in ('zh','en') for name in ('tagger.fst','verbalizer.fst')]]),
        ],
        'lipsync': [
            ('latentsync_15', 'LatentSync 1.5', '扩散式口型生成 · 256 人脸', [ROOT / 'vendor/LatentSync/checkpoints/latentsync_unet.pt']),
            ('musetalk_15', 'MuseTalk 1.5', '本地模型 · 较快 · 256 人脸', [MUSE / 'models/musetalkV15/unet.pth', MUSE / 'models/sd-vae/diffusion_pytorch_model.bin', MUSE / 'models/whisper/pytorch_model.bin']),
        ],
        'restoration': [
            ('none', '关闭修复', '保留对口型模型的原始输出', []),
            ('gn_v14', 'GN v1.4 · 人脸修复', '本地 512 人脸修复；可调整强度', [RESTORER]),
        ],
    }
    result = {kind: [dict(id=key, name=name, description=desc, available=all(p.is_file() for p in files),
                        **({'emotion': capabilities(key)} if kind == 'tts' else {}),
                        reason='' if all(p.is_file() for p in files) else '此引擎需要单独安装；也可在桌面菜单连接已有工作室')
                   for key, name, desc, files in items] for kind, items in specs.items()}
    connected = {p['id']: p['configured'] for p in cloud.status()}
    for key, (provider, _, name) in MODELS.items():
        result['tts'].append(dict(id=key, name=name, cloud=True, provider=provider,
            available=connected[provider], reason='请先在下方连接对应语音服务',
            description='云端克隆 · 支持情感预设 · 按服务商用量付费', emotion=capabilities(key)))
    return result

def model_error(kind, key):
    item = next((m for m in catalog()[kind] if m['id'] == key), None)
    if item is None:
        return '未知的模型选项，请刷新页面后重新选择。'
    return '' if item['available'] else item['name'] + '：' + item['reason']

def worker_command(stage, job):
    from cloud_speech import MODELS
    if stage == 'tts' and job.get('tts_model') in MODELS:
        return PYTHON, SOURCE / 'worker_cloud_speech.py'
    if stage == 'tts' and job.get('tts_model') in ('voxcpm2', 'cosyvoice3'):
        return venv_python('.speechvenv'), SOURCE / 'worker_speech.py'
    if stage == 'tts' and job.get('tts_model', TTS_DEFAULT) == 'indextts2':
        return venv_python('.indexvenv'), SOURCE / 'worker_index.py'
    if stage == 'restore' or stage == 'lipsync' and job.get('lipsync_model', LIP_DEFAULT) == 'musetalk_15':
        return PYTHON, SOURCE / 'worker_visual.py'
    return PYTHON, SOURCE / 'worker.py'
