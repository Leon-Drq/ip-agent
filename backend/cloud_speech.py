from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Official cloud speech APIs, with encrypted credentials and reusable voice bindings."""
import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path
import httpx
from settings import DATA, BIN
from remix_hub import protect

MODELS = {
    'minimax_hd': ('minimax', 'speech-2.8-hd', 'MiniMax Speech 2.8 · HD'),
    'minimax_turbo': ('minimax', 'speech-2.8-turbo', 'MiniMax Speech 2.8 · Turbo'),
    'eleven_v4': ('elevenlabs', 'eleven_v4', 'ElevenLabs · Eleven v4'),
    'eleven_v4_turbo': ('elevenlabs', 'eleven_v4_turbo', 'ElevenLabs · v4 Turbo'),
}
PROVIDERS = {
    'minimax': {'name': 'MiniMax', 'regions': {'cn': 'https://api.minimax.cn', 'global': 'https://api.minimax.io'},
                'pricing': 'https://platform.minimax.cn/docs/pricing/overview',
                'note': '按字符计费；首次使用克隆音色可能另收克隆费，以账号账单为准。'},
    'elevenlabs': {'name': 'ElevenLabs', 'regions': {'global': 'https://api.elevenlabs.io'},
                   'pricing': 'https://elevenlabs.io/pricing/api',
                   'note': '按账号套餐与字符用量计费；声音克隆需要账号具备相应权限。'},
}
MINI_EMOTIONS = {'happy': 'happy', 'sad': 'sad', 'angry': 'angry', 'afraid': 'fearful',
                 'disgusted': 'disgusted', 'surprised': 'surprised', 'calm': 'neutral'}
ELEVEN_TAGS = {'happy': 'happy', 'sad': 'sad', 'angry': 'angry', 'surprised': 'surprised',
               'gentle': 'warmly', 'calm': 'calm', 'serious': 'serious', 'chat': 'conversational'}

class RejectedRequest(ValueError):
    """The provider explicitly rejected a request; a later user retry is safe."""
    pass

def atomic(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

class CloudSpeech:
    def __init__(self, directory=None):
        self.directory = Path(directory or DATA / 'cloud-speech')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def config(self, provider):
        if provider not in PROVIDERS:
            raise ValueError('未知的云端语音服务。')
        path = self.directory / (provider + '.dpapi')
        if not path.is_file():
            return {}
        try:
            return json.loads(protect(path.read_bytes(), True))
        except Exception:
            raise ValueError('无法读取本机保存的密钥，请重新连接该语音服务。') from None

    def status(self):
        result = []
        for provider, spec in PROVIDERS.items():
            cfg = self.config(provider)
            region = cfg.get('region', 'cn' if provider == 'minimax' else 'global')
            note = ('HD ¥3.50 / 万字符，Turbo ¥2.00 / 万字符；汉字按 2 字符计。克隆音色首次合成 ¥9.90 / 个。' if region == 'cn' else
                    'HD $100 / 百万字符，Turbo $60 / 百万字符；克隆音色首次合成 $1.50 / 个。') if provider == 'minimax' else 'v4 $0.08 / 千字符，v4 Turbo $0.04 / 千字符；克隆权限及额度取决于账号套餐。'
            result.append(dict(id=provider, name=spec['name'], configured=bool(cfg.get('key')),
                               region=cfg.get('region', 'cn' if provider == 'minimax' else 'global'),
                               pricing='https://platform.minimax.io/docs/pricing/overview' if provider == 'minimax' and region == 'global' else spec['pricing'],
                               note=note + '（2026-10-27 公开价，实际以账号结算为准。）'))
        return result

    def request(self, provider, method, path, *, config=None, binary=False, **kwargs):
        cfg = config if config is not None else self.config(provider)
        if not cfg.get('key'):
            raise ValueError('请先在「云端语音设置」中连接 ' + PROVIDERS[provider]['name'] + '。')
        base = PROVIDERS[provider]['regions'][cfg['region']]
        headers = {'Authorization': 'Bearer ' + cfg['key']} if provider == 'minimax' else {'xi-api-key': cfg['key']}
        try:
            response = httpx.request(method, base + path, headers=headers, timeout=httpx.Timeout(180, connect=25),
                                     follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise ValueError('云端语音连接中断，未自动重试；请先核对服务商用量后再重试。') from None
        if not response.is_success:
            label = {401: '密钥无效', 403: '账号没有此接口权限', 402: '账号余额或套餐额度不足',
                     429: '请求过于频繁或额度不足'}.get(response.status_code, '请求未完成，请核对账号权限、音色和用量')
            error = RejectedRequest if response.status_code in (400, 401, 402, 403, 404, 422, 429) else ValueError
            raise error(f'{PROVIDERS[provider]["name"]}：{label}（HTTP {response.status_code}）。')
        if binary:
            if len(response.content) < 100 or 'json' in response.headers.get('content-type', ''):
                raise ValueError('云端没有返回有效音频。')
            return response.content
        try:
            data = response.json()
        except ValueError:
            raise ValueError('云端返回格式异常，未自动重试。') from None
        if not isinstance(data, dict):
            raise ValueError('云端返回格式异常。')
        if provider == 'minimax' and data.get('base_resp', {}).get('status_code', 0) != 0:
            code = data['base_resp']['status_code']
            label = {1004: '密钥无效', 1008: '余额不足', 1002: '请求频率超限'}.get(code, '请检查录音、音色状态与账号权限')
            raise RejectedRequest(f'MiniMax：{label}（错误码 {code}）。')
        return data

    def connect(self, provider, key, region):
        if provider not in PROVIDERS or region not in PROVIDERS[provider]['regions']:
            raise ValueError('请选择正确的服务与账号地区。')
        key = key.strip()
        if not key or len(key) > 4096 or any(c.isspace() for c in key):
            raise ValueError('请填写有效的 API Key。')
        cfg = {'key': key, 'region': region}
        self.remote_voices(provider, cfg)  # Read-only verification; never synthesize a paid sample.
        with self.lock:
            path = self.directory / (provider + '.dpapi')
            tmp = path.with_suffix('.tmp')
            tmp.write_bytes(protect(json.dumps(cfg).encode()))
            tmp.replace(path)
        return self.status()

    def remote_voices(self, provider, config=None):
        if provider == 'minimax':
            data = self.request(provider, 'POST', '/v1/get_voice', config=config, json={'voice_type': 'voice_cloning'})
            voices = data.get('voice_cloning', [])  # MiniMax omits empty categories for some accounts.
        else:
            voices = self.request(provider, 'GET', '/v1/voices', config=config).get('voices')
        if not isinstance(voices, list): raise ValueError('未能读取云端声音库，请检查账号权限。')
        return voices

    def identity(self, model, audio_id):
        if model not in MODELS or not re.fullmatch(r'[a-f0-9]{32}', audio_id):
            raise ValueError('请选择有效的云端模型与参考录音。')
        provider = MODELS[model][0]
        cfg = self.config(provider)
        if not cfg.get('key'):
            raise ValueError('请先连接 ' + PROVIDERS[provider]['name'] + '。')
        account = hashlib.sha256((provider + cfg['region'] + cfg['key']).encode()).hexdigest()
        key = hashlib.sha256((account + audio_id).encode()).hexdigest()[:32]
        return provider, cfg, key, account

    def binding(self, model, audio_id):
        provider, cfg, key, account = self.identity(model, audio_id)
        path = self.directory / (key + '.json')
        data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if provider == 'minimax' and not data.get('activated') and time.time() - data.get('created_at', 0) >= 6 * 86400:
            return None
        return data if data.get('status') == 'ready' else None

    def prepare(self, model, audio_id, source, duration):
        provider, cfg, key, account = self.identity(model, audio_id)
        if not (10 <= duration <= 300):
            raise ValueError('云端克隆请使用 10 秒至 5 分钟的清晰单人录音，建议 30–60 秒；本地模型仍可使用短录音。')
        with self.lock:
            path = self.directory / (key + '.json')
            saved = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            if saved.get('status') == 'ready':
                if provider != 'minimax' or saved.get('activated') or time.time() - saved['created_at'] < 6 * 86400:
                    return saved
            stable_name = 'Studio_' + key
            # Recover a completed remote clone after a lost response or app restart.
            voices = self.remote_voices(provider, cfg)
            match = next((v for v in voices if (v.get('voice_id') == stable_name if provider == 'minimax' else v.get('name') == stable_name)), None)
            if match:
                if match.get('requires_verification'):
                    raise ValueError('此声音需先在 ElevenLabs 官网完成验证。')
                result = {**saved, 'voice_id': match['voice_id'], 'status': 'ready', 'account': account,
                          'provider': provider, 'region': cfg['region'], 'created_at': saved.get('created_at', time.time()), 'key': key,
                          'activated': provider == 'minimax' or saved.get('activated', False)}
                atomic(path, result)
                return result
            if saved.get('status') == 'creating':
                raise ValueError('上次云端克隆结果尚未确认，已阻止重复创建。请到服务商声音库核对后再连接。')
            sample = self.directory / (key + '.wav')
            result = subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-i', str(source),
                                     '-vn', '-ar', '32000', '-ac', '1', '-c:a', 'pcm_s16le', str(sample)],
                                    capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not sample.is_file() or sample.stat().st_size > 20 * 1024**2:
                sample.unlink(missing_ok=True)
                raise ValueError('参考录音转换失败或超过云端文件大小限制。')
            saved = {'provider': provider, 'region': cfg['region'], 'account': account, 'key': key,
                     'created_at': time.time(), 'status': 'creating'}
            try:
                with sample.open('rb') as f:
                    if provider == 'minimax':
                        uploaded = self.request(provider, 'POST', '/v1/files/upload', config=cfg,
                                                data={'purpose': 'voice_clone'}, files={'file': ('reference.wav', f, 'audio/wav')})
                        atomic(path, saved)
                        response = self.request(provider, 'POST', '/v1/voice_clone', config=cfg,
                                                json={'file_id': uploaded['file']['file_id'], 'voice_id': stable_name})
                        if response.get('input_sensitive'):
                            raise ValueError('MiniMax 未通过这份录音的审核。')
                        voice_id = stable_name
                    else:
                        atomic(path, saved)
                        response = self.request(provider, 'POST', '/v1/voices/add', config=cfg,
                                                data={'name': stable_name, 'remove_background_noise': 'false'},
                                                files={'files': ('reference.wav', f, 'audio/wav')})
                        voice_id = response['voice_id']
                        if response.get('requires_verification'):
                            raise ValueError('声音已创建，但需要先在 ElevenLabs 官网完成验证。')
                saved.update(status='ready', voice_id=voice_id)
                atomic(path, saved)
                return saved
            except RejectedRequest:
                saved['status'] = 'rejected'; atomic(path, saved)
                raise
            finally:
                sample.unlink(missing_ok=True)

    def synthesize(self, job, directory):
        model = job['tts_model']; provider, api_model, _ = MODELS[model]
        binding = job.get('cloud_voice') or {}
        _, cfg, key, account = self.identity(model, job['audio_id'])
        if binding.get('account') != account or binding.get('provider') != provider or not binding.get('voice_id'):
            raise ValueError('云端账号或声音已变化，请重新选择声音后生成。')
        voice_id = binding['voice_id']
        preset = job.get('emotion_preset') if job.get('emotion_mode') == 'preset' else None
        if provider == 'minimax':
            voice = {'voice_id': voice_id, 'speed': 1, 'vol': 1, 'pitch': 0}
            if preset: voice['emotion'] = MINI_EMOTIONS[preset]
            data = self.request(provider, 'POST', '/v1/t2a_v2', config=cfg, json={
                'model': api_model, 'text': job['text'], 'stream': False, 'output_format': 'hex',
                'language_boost': 'Chinese' if job['language'] == 'zh' else 'English', 'voice_setting': voice,
                'audio_setting': {'sample_rate': 32000, 'bitrate': 128000, 'format': 'mp3', 'channel': 1}})
            try: audio = bytes.fromhex(data['data']['audio'])
            except (KeyError, TypeError, ValueError): raise ValueError('MiniMax 未返回可用音频。') from None
            usage = {'provider': provider, 'model': api_model, 'characters': data.get('extra_info', {}).get('usage_characters'), 'trace_id': data.get('trace_id')}
        else:
            text = (f'[{ELEVEN_TAGS[preset]}] ' if preset else '') + job['text']
            audio = self.request(provider, 'POST', f'/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128', config=cfg,
                                 binary=True, json={'model_id': api_model, 'text': text, 'language_code': job['language']})
            usage = {'provider': provider, 'model': api_model, 'submitted_characters': len(text)}
        if len(audio) < 100: raise ValueError('云端没有返回可用音频。')
        with self.lock:
            path = self.directory / (key + '.json')
            if path.is_file():
                saved = json.loads(path.read_text(encoding='utf-8')); saved['activated'] = True; atomic(path, saved)
        directory = Path(directory)
        (directory / 'cloud-source.mp3').write_bytes(audio)
        atomic(directory / 'cloud-usage.json', usage)
        result = subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-i', str(directory / 'cloud-source.mp3'),
                                 '-ar', '32000', '-ac', '1', '-c:a', 'pcm_s16le', str(directory / 'cloud-voice.tmp.wav')],
                                capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode: raise ValueError('云端配音已返回，但转换失败，原始音频已保存在作品目录。')
        (directory / 'cloud-voice.tmp.wav').replace(directory / 'voice.wav')

cloud = CloudSpeech()
