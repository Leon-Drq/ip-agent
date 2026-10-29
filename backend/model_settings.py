"""One active engine per category; new jobs snapshot choices, existing jobs stay intact."""
import json
import threading
from pathlib import Path
from fastapi import HTTPException
from speech_options import capabilities, EMOTION_DEFAULTS

CATEGORIES = {
    'video': ('视频生成', '复刻视频中的人物、动作与场景', 'seedance-2-mini'),
    'tts': ('语音克隆', '本地与 API 模型共用一个选择，应用于所有角色配音', 'indextts2'),
    'image': ('图片生成', '生成或编辑复刻视频的参考画面', 'gpt-image-2'),
    'lipsync': ('对口型', '让数字人的嘴型跟随配音', 'latentsync_15'),
    'restoration': ('人脸修复', '修复生成后的面部细节，也可以关闭', 'none'),
    'transcription': ('文案转写', '从视频和录音中提取文字', 'whisper_large_v3_turbo'),
    'understanding': ('视频理解', '从原片画面与台词反推模仿创作的剧本', 'qwen3_vl_4b'),
}
VIDEO_COMPATIBLE = {'seedance-2', 'seedance-2-mini', 'seedance-2-fast', 'seedance-2.5'}
DISPLAY_NAMES = {'seedance-2': 'Seedance 2', 'seedance-2-mini': 'Seedance 2 · Mini',
    'seedance-2-fast': 'Seedance 2 · Fast', 'seedance-2.5': 'Seedance 2.5',
    'gpt-image-2': 'GPT Image 2', 'nano-banana-2': 'Nano Banana 2',
    'nano-banana-2-lite': 'Nano Banana 2 · Lite', 'nano-banana-pro': 'Nano Banana Pro',
    'seedream-5-lite': 'Seedream 5 · Lite', 'seedream-5-pro': 'Seedream 5 · Pro'}

class ModelSettings:
    def __init__(self, path, hub, catalog):
        self.path, self.hub, self.catalog = Path(path), hub, catalog
        self.lock = threading.RLock()

    def choices(self):
        from extraction_config import readiness
        rows = self.catalog()
        rows = {kind: [{**m, 'source': 'API' if m.get('cloud') else '本地'} for m in models] for kind, models in rows.items()}
        remote = self.hub.models()
        connected = self.hub.configured()
        for category, endpoint in [('video', 'videos'), ('image', 'image_edits')]:
            choices = []
            for m in remote:
                if endpoint not in m.get('endpoints', []): continue
                compatible = category == 'image' or m['id'].split('/')[-1] in VIDEO_COMPATIBLE
                choices.append({'id': m['id'], 'name': DISPLAY_NAMES.get(m['id'].split('/')[-1]) or m.get('display_name') or m['id'], 'source': 'API', 'provider': 'hypihub',
                    'supported': compatible,
                    'available': connected and compatible,
                    'description': '通过 HypiHub 调用 · 生成前查看预计积分',
                    'reason': '当前复刻流程尚未适配此模型' if not compatible else '' if connected else '请先连接 HypiHub'})
            if not choices:
                key = CATEGORIES[category][2]
                choices = [dict(id=key, name=key, source='API', provider='hypihub', available=False,
                                description='连接 HypiHub 后读取账号可用模型', reason='请先连接 HypiHub')]
            rows[category] = choices
        ready = readiness()
        rows['transcription'] = [dict(id='whisper_large_v3_turbo', name='Whisper large-v3-turbo', source='本地',
                available=ready['ready'], reason='、'.join(ready['missing']), description='本机语音转写 · 支持视频文案提取')]
        from remix_screenplay import readiness as vision_readiness
        vision = vision_readiness()
        rows['understanding'] = [dict(id='qwen3_vl_4b', name=vision['model'], source='本地',
            available=vision['ready'], reason='、'.join(vision['missing']), description='识别角色、场景与动作 · 原片在本机分析')]
        return rows

    def read(self, initialize=False):
        with self.lock:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding='utf-8'))
                missing = {key: value[2] for key, value in CATEGORIES.items() if key not in data['selected']}
                if missing:
                    data = {'revision': data['revision'] + 1, 'selected': {**data['selected'], **missing}}
                    self.persist(data)
                return data
            if not initialize: return None  # Existing embedded callers migrate when configuration is first opened.
            choices = self.choices()
            selected = {}
            for key, (_, _, preferred) in CATEGORIES.items():
                options = choices[key]
                match = next((m for m in options if m['id'].split('/')[-1] == preferred), None)
                selected[key] = (match or next((m for m in options if m['available']), options[0]))['id']
            data = {'revision': 1, 'selected': selected}
            self.persist(data)
            return data

    def persist(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)

    def public(self):
        current = self.read(initialize=True)
        choices = self.choices()
        return {**current, 'categories': [{'id': key, 'name': name, 'description': desc,
                'selected': current['selected'][key], 'models': choices[key]} for key, (name, desc, _) in CATEGORIES.items()]}

    def select(self, category, model, revision):
        with self.lock:
            current = self.read(initialize=True)
            if revision != current['revision']: raise HTTPException(409, '模型配置已在其他窗口更新，请刷新后重新选择。')
            if category not in CATEGORIES: raise HTTPException(400, '未知模型类别。')
            item = next((m for m in self.choices()[category] if m['id'] == model), None)
            if not item: raise HTTPException(400, '该模型不属于这个类别。')
            if not item['available']: raise HTTPException(400, item.get('reason') or '这个模型暂不可用。')
            if current['selected'][category] != model:
                current = {'revision': current['revision'] + 1, 'selected': {**current['selected'], category: model}}
                self.persist(current)
            return self.public()

    def speech(self, voice, settings=None):
        settings = settings or self.read()
        if not settings: return dict(voice)
        result = {**EMOTION_DEFAULTS, **voice, 'tts_model': settings['selected']['tts']}
        caps = capabilities(result['tts_model'])
        if result['emotion_mode'] not in [m['id'] for m in caps['modes']]: result['emotion_mode'] = 'reference'
        if result['emotion_preset'] not in [m['id'] for m in caps['presets']]:
            result['emotion_preset'] = caps['presets'][0]['id'] if caps['presets'] else 'happy'
        return result

    def check_revision(self, revision):
        current = self.read()
        if current and revision is not None and revision != current['revision']:
            raise HTTPException(409, '模型配置已变化，请重新查看当前模型后再生成。')

    def job_values(self, values, explicit=None, video=True):
        current = self.read()
        if not current: return values
        self.check_revision(values.get('model_config_revision'))
        selected = current['selected']
        wanted = {'tts_model': selected['tts']}
        if video: wanted.update(lipsync_model=selected['lipsync'], restoration_model=selected['restoration'])
        for key, model in wanted.items():
            if explicit and key in explicit and explicit[key] != model:
                raise HTTPException(409, '请使用「模型配置」中当前启用的模型，刷新页面后重试。')
        return {**self.speech(values, current), **wanted, 'model_config_revision': current['revision']}

    def visuals(self, project):
        current = self.read()
        if not current: return dict(project)
        return {**project, 'video_model': current['selected']['video'], 'image_model': current['selected']['image']}
