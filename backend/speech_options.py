"""Explicit model capabilities and fixed expression presets shared by API and workers."""
EMOTION_DEFAULTS = {'emotion_mode': 'reference', 'emotion_preset': 'happy',
                    'emotion_strength': 0.5, 'emotion_audio_id': ''}
LABELS = {'happy': '开心', 'angry': '生气', 'sad': '悲伤', 'afraid': '紧张 / 害怕',
          'disgusted': '厌恶', 'melancholic': '低落', 'surprised': '惊讶', 'calm': '平静',
          'chat': '自然聊天', 'gentle': '温柔', 'serious': '认真讲解'}
MODES = {'reference': '跟随原声', 'preset': '情感预设', 'auto': '按文案自动', 'audio': '参考情绪录音'}
INDEX_PRESETS = ['happy', 'angry', 'sad', 'afraid', 'disgusted', 'melancholic', 'surprised', 'calm']
PRESETS = {'gpt_sovits': [], 'indextts2': INDEX_PRESETS,
           'voxcpm2': ['chat', 'gentle', 'serious', 'happy', 'sad', 'angry', 'surprised', 'calm'],
           'cosyvoice3': ['happy', 'sad', 'angry']}
for key in ('minimax_hd', 'minimax_turbo'):
    PRESETS[key] = ['happy', 'sad', 'angry', 'afraid', 'disgusted', 'surprised', 'calm']
for key in ('eleven_v4', 'eleven_v4_turbo'):
    PRESETS[key] = ['chat', 'gentle', 'serious', 'happy', 'sad', 'angry', 'surprised', 'calm']
VOX_STYLES = {'chat': 'natural, relaxed conversational tone', 'gentle': 'gentle and warm tone',
              'serious': 'serious, clear explanatory tone', 'happy': 'cheerful, happy tone',
              'sad': 'sad and sorrowful tone', 'angry': 'angry tone',
              'surprised': 'surprised tone', 'calm': 'calm, composed tone'}
COSY_STYLES = {'happy': '请非常开心地说一句话。', 'sad': '请非常伤心地说一句话。',
               'angry': '请非常生气地说一句话。'}

def capabilities(model):
    modes = ['reference'] if model == 'gpt_sovits' else ['reference', 'preset']
    if model == 'indextts2':
        modes += ['auto', 'audio']
    return {'modes': [{'id': k, 'name': '自然表达' if k == 'reference' and model.startswith(('minimax_', 'eleven_')) else MODES[k]} for k in modes],
            'presets': [{'id': k, 'name': LABELS[k]} for k in PRESETS[model]],
            'strength': model == 'indextts2'}

def emotion_error(values):
    model = values.tts_model
    caps = capabilities(model)
    if values.emotion_mode not in [m['id'] for m in caps['modes']]:
        return '当前模型不支持这种情感控制方式，请重新选择。'
    if values.emotion_mode == 'preset' and values.emotion_preset not in PRESETS[model]:
        return '当前模型不支持这个情感预设，请重新选择。'
    if values.emotion_mode == 'audio' and not values.emotion_audio_id:
        return '请先上传或选择一段情绪参考录音。'
    return ''

def index_emotion_kwargs(job, directory):
    mode = job.get('emotion_mode', 'reference')
    strength = job.get('emotion_strength', .5)
    if mode == 'auto':
        return {'use_emo_text': True, 'emo_alpha': strength}
    if mode == 'audio':
        return {'emo_audio_prompt': str(directory / 'emotion.wav'), 'emo_alpha': strength}
    if mode == 'preset':
        return {'emo_vector': [strength if k == job['emotion_preset'] else 0.0 for k in INDEX_PRESETS]}
    return {}

def split_speech(text, limit=140):
    """Bound autoregressive inference memory while keeping complete sentences when possible."""
    import re
    parts = re.findall(r'[^。！？!?\n]+[。！？!?\n]*|[。！？!?\n]+', text.strip())
    result, current = [], ''
    for part in parts:
        if len(current) + len(part) > limit and current:
            result.append(current.strip()); current = ''
        while len(part) > limit:
            cut = max(part.rfind('，', 0, limit), part.rfind(',', 0, limit), part.rfind(' ', 0, limit))
            cut = cut + 1 if cut > limit // 3 else limit
            result.append(part[:cut].strip()); part = part[cut:]
        current += part
    if current.strip(): result.append(current.strip())
    return [part for part in result if part]
