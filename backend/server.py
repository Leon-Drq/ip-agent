from settings import worker_process, terminate_process
import platform
from settings import FFMPEG, FFPROBE, SOURCE, venv_python
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from settings import ROOT, DATA, UPLOADS, JOBS, PYTHON, BIN, environment
from model_registry import catalog, model_error, worker_command
from speech_options import EMOTION_DEFAULTS, emotion_error
from compute_lock import gpu_lock
from cloud_speech import cloud, MODELS as CLOUD_MODELS

app = FastAPI(title='IP智能体')
lock = threading.RLock()
pending = queue.Queue()
records = {}
processes = {}
cancelled = set()
AVATARS = DATA / 'avatars'
AVATARS.mkdir(parents=True, exist_ok=True)
VOICES = DATA / 'voices'
VOICES.mkdir(parents=True, exist_ok=True)

def now():
    return datetime.now().isoformat(timespec='seconds')

def persist(job):
    path = JOBS / job['id'] / 'job.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def update(jid, **values):
    with lock:
        records[jid].update(values, updated_at=now())
        persist(records[jid])

for saved in JOBS.glob('*/job.json'):
    try:
        job = json.loads(saved.read_text(encoding='utf-8'))
        if job['status'] in ('queued', 'running'):
            job.update(status='failed', error='上次运行已中断，可以重新生成。')
            persist(job)
        records[job['id']] = job
    except (ValueError, KeyError, OSError):
        continue

@app.middleware('http')
async def local_only(request: Request, call_next):
    origin = request.headers.get('origin', '')
    if origin and not re.match(r'^http://(127\.0\.0\.1|localhost)(:\d+)?$', origin):
        return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

def probe(path):
    result = subprocess.run([str(FFPROBE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('无法读取这个文件，请使用常见的 MP4 视频或 WAV / MP3 音频。')
    return json.loads(result.stdout)

def upload_info(uid):
    if not re.fullmatch(r'[0-9a-f]{32}', uid):
        raise HTTPException(404, '素材不存在')
    meta = UPLOADS / uid / 'meta.json'
    if not meta.exists():
        raise HTTPException(404, '素材不存在，请重新上传')
    return json.loads(meta.read_text(encoding='utf-8'))

def upload_path(uid):
    meta = upload_info(uid)
    return UPLOADS / uid / meta['stored_name']

@app.get('/api/health')
def health():
    manifest = ROOT / 'models/manifest.json'
    files = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
    ready = [x for x in files if Path(x['path']).exists() and Path(x['path']).stat().st_size == x['bytes']]
    supplemental = (ROOT / 'models/runtime-ready.json').exists()
    return {'app': 'avatar-studio', 'ready': bool(files) and len(ready) == len(files) and supplemental,
            'downloaded': len(ready), 'total': len(files),
            'models': ['GPT-SoVITS v2ProPlus', 'IndexTTS 2', 'VoxCPM2', 'CosyVoice 3 · 0.5B', 'LatentSync 1.5', 'MuseTalk 1.5', 'GN v1.4'],
            'engines': catalog(), 'model_settings': model_settings.read(), 'gpu': platform.system() + ' · ' + platform.machine(),
            'active_job': next((j['id'] for j in records.values() if j['status'] == 'running'), None)}

@app.get('/api/models')
def models():
    return catalog()

def validate_models(values, video=True, audio=True):
    choices = [('tts', values.tts_model)] if audio else []
    if video:
        choices += [('lipsync', values.lipsync_model), ('restoration', values.restoration_model)]
    for kind, key in choices:
        error = model_error(kind, key)
        if error:
            raise HTTPException(503, error)
    if audio:
        error = emotion_error(values)
        if error:
            raise HTTPException(400, error)
        if values.emotion_mode == 'audio':
            if upload_info(values.emotion_audio_id)['kind'] != 'audio' or not upload_path(values.emotion_audio_id).is_file():
                raise HTTPException(400, '请选择一段可用的情绪参考录音。')

@app.post('/api/uploads')
async def upload(kind: Literal['video', 'audio'] = Form(...), file: UploadFile = File(...)):
    suffix = Path(file.filename or '').suffix.lower()
    accepted = {'.mp4', '.mov', '.mkv', '.webm', '.avi'} if kind == 'video' else {'.wav', '.mp3', '.m4a', '.flac', '.ogg', '.aac', '.mp4'}
    if suffix not in accepted:
        raise HTTPException(400, '不支持这个格式，请使用 MP4 视频或 WAV / MP3 音频。')
    uid = uuid.uuid4().hex
    directory = UPLOADS / uid
    directory.mkdir()
    path = directory / ('source' + suffix)
    size = 0
    try:
        with path.open('wb') as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise HTTPException(413, '单个文件请控制在 1GB 以内。')
                out.write(chunk)
        info = probe(path)
        streams = info.get('streams', [])
        if not any(s.get('codec_type') == kind for s in streams):
            raise ValueError('文件中没有可用的' + ('画面。' if kind == 'video' else '声音。'))
        duration = float(info.get('format', {}).get('duration') or 0)
        if duration <= 0 or (kind == 'audio' and duration < 3):
            raise ValueError('参考声音需要至少 3 秒。' if kind == 'audio' else '视频时长无效。')
        meta = {'id': uid, 'kind': kind, 'name': Path(file.filename).name,
                'stored_name': path.name, 'duration': round(duration, 2), 'bytes': size, 'created_at': now()}
        (directory / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        return meta
    except Exception as exc:
        # Only files in the freshly-created upload directory are removed.
        path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, str(exc))
    finally:
        await file.close()

@app.get('/api/uploads/{uid}/content')
def uploaded_content(uid: str):
    return FileResponse(upload_path(uid))

@app.get('/api/uploads/{uid}')
def uploaded_metadata(uid: str):
    return upload_info(uid)

@app.get('/api/uploads/{uid}/thumbnail')
def thumbnail(uid: str):
    if upload_info(uid)['kind'] != 'video':
        raise HTTPException(400, '该素材不是人物视频')
    path = UPLOADS / uid / 'thumbnail.jpg'
    with lock:
        if not path.exists():
            result = subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-i', str(upload_path(uid)),
                                     '-frames:v', '1', '-vf', 'scale=480:480:force_original_aspect_ratio=decrease',
                                     '-q:v', '3', str(path)], capture_output=True, timeout=30,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not path.exists():
                raise HTTPException(400, '无法预览这段视频，请重新选择素材。')
    return FileResponse(path, media_type='image/jpeg')

class SpeechInput(BaseModel):
    tts_model: Literal['gpt_sovits', 'indextts2', 'voxcpm2', 'cosyvoice3', 'minimax_hd', 'minimax_turbo', 'eleven_v4', 'eleven_v4_turbo'] = 'gpt_sovits'
    emotion_mode: Literal['reference', 'preset', 'auto', 'audio'] = 'reference'
    emotion_preset: Literal['happy', 'angry', 'sad', 'afraid', 'disgusted', 'melancholic', 'surprised', 'calm', 'chat', 'gentle', 'serious'] = 'happy'
    emotion_strength: float = Field(default=.5, ge=0, le=1)
    emotion_audio_id: str = ''

class VoiceInput(SpeechInput):
    name: str = Field(min_length=1, max_length=40)
    audio_id: str
    reference_text: str = Field(default='', max_length=2000)
    reference_language: Literal['auto', 'zh', 'en'] = 'auto'
    language: Literal['zh', 'en'] = 'zh'

class VoiceUpdate(VoiceInput):
    revision: int = Field(ge=1)

class ProfileDelete(BaseModel):
    revision: int = Field(ge=1)

def cloud_local(request):
    if request and (request.url.hostname not in {'localhost', '127.0.0.1', '::1'} or
                    request.headers.get('origin') != f'{request.url.scheme}://{request.url.netloc}'):
        raise HTTPException(403, '云端语音设置和付费生成请在本机工作室操作。')

class CloudConnect(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)
    region: Literal['cn', 'global'] = 'cn'

class CloudPrepare(BaseModel):
    model_config_revision: int | None = None
    tts_model: str
    audio_id: str
    confirmed: bool = False

@app.get('/api/cloud-speech')
def cloud_status():
    return cloud.status()

@app.put('/api/cloud-speech/{provider}')
def cloud_connect(provider: str, values: CloudConnect, request: Request):
    cloud_local(request)
    try: return cloud.connect(provider, values.api_key, values.region)
    except ValueError as exc: raise HTTPException(400, str(exc)) from None

@app.delete('/api/cloud-speech/{provider}')
def cloud_disconnect(provider: str, request: Request):
    cloud_local(request)
    if provider not in {'minimax', 'elevenlabs'}: raise HTTPException(404, '未知服务')
    with cloud.lock: (cloud.directory / (provider + '.dpapi')).unlink(missing_ok=True)
    return cloud.status()

@app.post('/api/cloud-speech/voice/status')
def cloud_voice_status(values: CloudPrepare, request: Request):
    cloud_local(request)
    try:
        binding = cloud.binding(values.tts_model, values.audio_id)
        return {'ready': bool(binding), 'activated': bool(binding and binding.get('activated'))}
    except ValueError as exc: raise HTTPException(400, str(exc)) from None

@app.post('/api/cloud-speech/voice/prepare')
def cloud_prepare_voice(values: CloudPrepare, request: Request):
    cloud_local(request)
    model_settings.check_revision(values.model_config_revision)
    current = model_settings.read()
    if current and values.tts_model != current['selected']['tts']:
        raise HTTPException(409, '语音模型已变化，请使用模型配置中当前启用的模型。')
    if not values.confirmed: raise HTTPException(400, '请确认将参考录音上传给所选语音服务。')
    info = upload_info(values.audio_id)
    if info['kind'] != 'audio': raise HTTPException(400, '请选择参考录音。')
    try:
        cloud.prepare(values.tts_model, values.audio_id, upload_path(values.audio_id), info['duration'])
        return {'ready': True}
    except ValueError as exc: raise HTTPException(400, str(exc)) from None

def read_voice(vid):
    if not re.fullmatch(r'[0-9a-f]{32}', vid):
        raise HTTPException(404, '声音不存在')
    try:
        profile = {'tts_model': 'gpt_sovits', **EMOTION_DEFAULTS, **json.loads((VOICES / vid / 'profile.json').read_text(encoding='utf-8'))}
        if profile.get('deleted_at'):
            raise HTTPException(404, '这个声音已从声音库删除')
        return profile
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, '声音资料不存在')

def persist_voice(profile):
    directory = VOICES / profile['id']
    directory.mkdir(exist_ok=True)
    path = directory / 'profile.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

@app.get('/api/voices')
def list_voices():
    with lock:
        profiles = []
        for path in VOICES.glob('*/profile.json'):
            try:
                profiles.append(read_voice(path.parent.name))
            except HTTPException:
                continue
        return sorted(profiles, key=lambda p: (p['created_at'], p['id']), reverse=True)

@app.get('/api/voices/{vid}')
def get_voice(vid: str):
    with lock:
        return read_voice(vid)

def save_voice(values, vid=None):
    with lock:
        values = type(values)(**model_settings.job_values(values.model_dump(), values.model_dump(exclude_unset=True), video=False))
        previous = read_voice(vid) if vid else None
        if previous and previous['revision'] != values.revision:
            raise HTTPException(409, '声音资料已更新，请关闭窗口后重新编辑。')
        data = values.model_dump(exclude={'revision'})
        validate_models(values, video=False)
        data['name'], data['reference_text'] = data['name'].strip(), data['reference_text'].strip()
        if not data['name']:
            raise HTTPException(400, '请为声音起个名字。')
        if any(p['id'] != vid and p['name'].casefold() == data['name'].casefold() for p in list_voices()):
            raise HTTPException(409, '这个声音名称已使用，请换一个名字。')
        if upload_info(data['audio_id'])['kind'] != 'audio' or not upload_path(data['audio_id']).is_file():
            raise HTTPException(400, '请上传一段可用的参考录音。')
        if values.tts_model in CLOUD_MODELS and not cloud.binding(values.tts_model, data['audio_id']):
            raise HTTPException(400, '请先为这份录音启用所选云端声音。')
        profile = {**data, 'id': vid or uuid.uuid4().hex, 'revision': previous['revision'] + 1 if previous else 1,
                   'created_at': previous['created_at'] if previous else now(), 'updated_at': now()}
        persist_voice(profile)
        return profile

@app.post('/api/voices')
def create_voice(values: VoiceInput):
    return save_voice(values)

@app.put('/api/voices/{vid}')
def edit_voice(vid: str, values: VoiceUpdate):
    return save_voice(values, vid)

class AvatarInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    video_id: str
    audio_id: str = ''
    voice_id: str = ''
    reference_text: str = Field(default='', max_length=2000)
    reference_language: Literal['auto', 'zh', 'en'] = 'auto'
    language: Literal['zh', 'en'] = 'zh'
    quality: Literal['standard', 'fine'] = 'standard'
    lipsync_model: Literal['latentsync_15', 'musetalk_15'] = 'latentsync_15'
    restoration_model: Literal['none', 'gn_v14'] = 'none'
    restoration_strength: float = Field(default=.35, ge=0, le=1)

class AvatarUpdate(AvatarInput):
    revision: int = Field(ge=1)

def read_avatar(aid):
    if not re.fullmatch(r'[0-9a-f]{32}', aid):
        raise HTTPException(404, '数字人不存在')
    path = AVATARS / aid / 'profile.json'
    try:
        profile = {'lipsync_model': 'latentsync_15', 'restoration_model': 'none', 'restoration_strength': .35,
                   **json.loads(path.read_text(encoding='utf-8'))}
        if profile.get('deleted_at'):
            raise HTTPException(404, '这个数字人已从形象库删除')
        return profile
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, '数字人资料不存在')

def persist_avatar(profile):
    directory = AVATARS / profile['id']
    directory.mkdir(exist_ok=True)
    path = directory / 'profile.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

@app.get('/api/avatars')
def list_avatars():
    with lock:
        profiles = []
        for path in AVATARS.glob('*/profile.json'):
            try:
                profiles.append(read_avatar(path.parent.name))
            except HTTPException:
                continue
        return sorted(profiles, key=lambda p: (p['created_at'], p['id']), reverse=True)

@app.get('/api/avatars/{aid}')
def get_avatar(aid: str):
    with lock:
        return read_avatar(aid)

def save_avatar(values, aid=None):
    with lock:
        current = model_settings.read()
        if current:
            data = values.model_dump()
            for category, field in [('lipsync', 'lipsync_model'), ('restoration', 'restoration_model')]:
                data[field] = current['selected'][category]
            values = type(values)(**data)
        validate_models(values, audio=False)
        previous = read_avatar(aid) if aid else None
        if previous and previous['revision'] != values.revision:
            raise HTTPException(409, '这份资料刚刚更新过，请重新选择数字人后再修改。')
        data = values.model_dump(exclude={'revision'})
        data['name'] = data['name'].strip()
        data['reference_text'] = data['reference_text'].strip()
        if not data['name']:
            raise HTTPException(400, '请为数字人起个名字。')
        for profile in list_avatars():
            if profile['id'] != aid and profile['name'].casefold() == data['name'].casefold():
                raise HTTPException(409, '这个名字已经使用，请换一个名字。')
        for kind in ('video', 'audio'):
            uid = data[kind + '_id']
            if kind == 'audio' and not uid:
                continue
            if upload_info(uid)['kind'] != kind or not upload_path(uid).is_file():
                raise HTTPException(400, '请先导入可用的人物视频和参考声音。')
        if data['voice_id']:
            read_voice(data['voice_id'])
        profile = {**data, 'id': aid or uuid.uuid4().hex, 'revision': previous['revision'] + 1 if previous else 1,
                   'created_at': previous['created_at'] if previous else now(), 'updated_at': now()}
        persist_avatar(profile)
        return profile

@app.post('/api/avatars')
def create_avatar(values: AvatarInput):
    return save_avatar(values)

@app.put('/api/avatars/{aid}')
def edit_avatar(aid: str, values: AvatarUpdate):
    return save_avatar(values, aid)

@app.delete('/api/avatars/{aid}')
def delete_avatar(aid: str, values: ProfileDelete):
    with lock:
        profile = read_avatar(aid)
        if profile['revision'] != values.revision:
            raise HTTPException(409, '数字人资料已更新，请关闭窗口后重新确认删除。')
        # Keep media and historical job snapshots; tombstones also prevent legacy migration resurrection.
        profile.update(deleted_at=now(), updated_at=now(), revision=profile['revision'] + 1)
        persist_avatar(profile)
        return {'id': aid, 'deleted': True}

@app.delete('/api/voices/{vid}')
def delete_voice(vid: str, values: ProfileDelete):
    with lock:
        profile = read_voice(vid)
        if profile['revision'] != values.revision:
            raise HTTPException(409, '声音资料已更新，请关闭窗口后重新确认删除。')
        changed = []
        for avatar in list_avatars():
            if avatar.get('voice_id') != vid:
                continue
            avatar.update(voice_id='', updated_at=now(), revision=avatar['revision'] + 1)
            if avatar.get('audio_id') == profile['audio_id']:
                avatar.update(audio_id='', reference_text='', reference_language='auto')
            persist_avatar(avatar)
            changed.append(avatar)
        profile.update(deleted_at=now(), updated_at=now(), revision=profile['revision'] + 1)
        persist_voice(profile)
        return {'id': vid, 'deleted': True, 'avatars': changed}

def remember_reference(job):
    """Cache first transcription only while the source profile is still unchanged."""
    if job.get('voice_id') and job.get('reference_text', '').strip():
        with lock:
            try:
                voice = read_voice(job['voice_id'])
            except HTTPException:
                voice = None
            if (voice and voice['revision'] == job.get('voice_revision') and voice['audio_id'] == job['audio_id']
                    and not voice['reference_text'].strip()):
                voice.update(reference_text=job['reference_text'], reference_language=job['reference_language'],
                             revision=voice['revision'] + 1, updated_at=now())
                persist_voice(voice)
    if not job.get('avatar_id') or not job.get('reference_text', '').strip():
        return
    with lock:
        try:
            profile = read_avatar(job['avatar_id'])
        except HTTPException:
            return
        if (profile['revision'] != job.get('avatar_revision') or profile['audio_id'] != job['audio_id']
                or profile['reference_text'].strip()):
            return
        profile.update(reference_text=job['reference_text'], reference_language=job['reference_language'],
                       revision=profile['revision'] + 1, updated_at=now())
        persist_avatar(profile)

class JobInput(SpeechInput):
    model_config_revision: int | None = None
    cloud_confirmed: bool = False
    text: str = Field(min_length=1, max_length=8000)
    video_speed: float = Field(default=1.0, ge=.5, le=2.0, allow_inf_nan=False)
    video_id: str = ''
    audio_id: str = ''
    avatar_id: str = ''
    voice_id: str = ''
    reference_text: str = Field(default='', max_length=2000)
    language: Literal['zh', 'en'] = 'zh'
    reference_language: Literal['auto', 'zh', 'en'] = 'auto'
    quality: Literal['standard', 'fine'] = 'standard'
    mode: Literal['video', 'audio'] = 'video'
    lipsync_model: Literal['latentsync_15', 'musetalk_15'] = 'latentsync_15'
    restoration_model: Literal['none', 'gn_v14'] = 'none'
    restoration_strength: float = Field(default=.35, ge=0, le=1)

@app.get('/api/jobs')
def list_jobs(trash: bool = False):
    with lock:
        return sorted((dict(j) for j in records.values() if bool(j.get('deleted_at')) == trash),
                      key=lambda j: j['created_at'], reverse=True)

class JobSelection(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=1000)

def change_job_trash(job_ids, deleted):
    """Validate the entire selection before changing any saved job; keep generated media recoverable."""
    with lock:
        ids = list(dict.fromkeys(job_ids))
        for jid in ids:
            if jid not in records:
                raise HTTPException(404, '有作品不存在，请刷新后重试。')
            proc = processes.get(jid)
            if records[jid]['status'] in ('queued', 'running') or (proc and proc.poll() is None):
                raise HTTPException(409, '选中的作品仍在生成或停止中，请先停止任务，稍后再删除。')
        for jid in ids:
            if deleted:
                cancelled.add(jid)  # A stopped job may still have an old entry in the queue.
            if bool(records[jid].get('deleted_at')) != deleted:
                update(jid, deleted_at=now() if deleted else None)
        return {'jobs': [dict(records[jid]) for jid in ids]}

@app.post('/api/jobs/batch-delete')
def delete_jobs(values: JobSelection):
    return change_job_trash(values.job_ids, True)

@app.post('/api/jobs/batch-restore')
def restore_jobs(values: JobSelection):
    return change_job_trash(values.job_ids, False)

@app.delete('/api/jobs/{jid}')
def delete_job(jid: str):
    return change_job_trash([jid], True)

@app.post('/api/jobs/{jid}/restore')
def restore_job(jid: str):
    return change_job_trash([jid], False)

@app.get('/api/jobs/{jid}')
def get_job(jid: str):
    with lock:
        if jid not in records or records[jid].get('deleted_at'):
            raise HTTPException(404, '任务不存在')
        return dict(records[jid])

@app.post('/api/jobs')
def new_job(values: JobInput, request: Request = None):
    avatar = None
    voice = None
    explicit = values.model_dump(exclude_unset=True)
    defaults = {}
    if values.avatar_id:
        avatar = get_avatar(values.avatar_id)
        defaults = {k: avatar[k] for k in ('video_id', 'audio_id', 'reference_text', 'reference_language', 'language', 'quality', 'lipsync_model', 'restoration_model', 'restoration_strength')}
    voice_id = values.voice_id or (avatar.get('voice_id', '') if avatar else '')
    if voice_id:
        voice = get_voice(voice_id)
        defaults.update({k: voice[k] for k in ('audio_id', 'reference_text', 'reference_language', 'language', 'tts_model', *EMOTION_DEFAULTS)})
        defaults['voice_id'] = voice_id
    values = JobInput(**{**defaults, **explicit})
    # HTTP requests follow the active settings. Internal remix jobs already carry
    # an approved snapshot so a mid-run change cannot switch providers.
    if request is not None:
        values = JobInput(**model_settings.job_values(values.model_dump(), explicit, video=values.mode == 'video'))
    if voice and not values.voice_id:
        values.voice_id = voice['id']
    # A job snapshots the chosen image and voice independently, including explicit historic sources.
    if avatar and values.video_id != avatar['video_id']:
        avatar = None
        values.avatar_id = ''
    if voice and values.audio_id != voice['audio_id']:
        voice = None
        values.voice_id = ''
    if not values.text.strip():
        raise HTTPException(400, '先写下你想说的话。')
    if upload_info(values.audio_id)['kind'] != 'audio':
        raise HTTPException(400, '请选择参考声音。')
    if values.mode == 'video' and (not values.video_id or upload_info(values.video_id)['kind'] != 'video'):
        raise HTTPException(400, '请先上传人物视频。')
    if not health()['ready']:
        raise HTTPException(503, '模型正在准备，请等右上角显示“本地模型就绪”后再生成。')
    validate_models(values, video=values.mode == 'video')
    cloud_voice = None
    if values.tts_model in CLOUD_MODELS:
        cloud_local(request)
        if not values.cloud_confirmed:
            raise HTTPException(400, '请确认使用云端模型生成配音，本次会按服务商用量计费。')
        try: cloud_voice = cloud.binding(values.tts_model, values.audio_id)
        except ValueError as exc: raise HTTPException(400, str(exc)) from None
        if not cloud_voice:
            raise HTTPException(400, '这份录音尚未启用所选云端声音，请先在语音克隆页保存或试听。')
    if shutil.disk_usage(ROOT).free < 3 * 1024**3:
        raise HTTPException(400, 'D 盘剩余空间不足 3GB，请先腾出一些空间。')
    jid = uuid.uuid4().hex
    (JOBS / jid).mkdir()
    job = {**values.model_dump(), 'id': jid, 'title': values.text.strip()[:24], 'created_at': now(),
           'updated_at': now(), 'status': 'queued', 'stage': 'queued', 'progress': 0,
           'message': '等待开始', 'error': None, 'audio_ready': False, 'video_ready': False}
    if avatar:
        job.update(avatar_name=avatar['name'], avatar_revision=avatar['revision'])
    if voice:
        job.update(voice_name=voice['name'], voice_revision=voice['revision'])
    if cloud_voice:
        job['cloud_voice'] = cloud_voice
    with lock:
        records[jid] = job
        persist(job)
    pending.put((jid, False))
    return job

class RenderInput(BaseModel):
    model_config_revision: int | None = None
    video_id: str = ''
    video_speed: float | None = Field(default=None, ge=.5, le=2.0, allow_inf_nan=False)
    avatar_id: str = ''
    lipsync_model: Literal['latentsync_15', 'musetalk_15'] | None = None
    restoration_model: Literal['none', 'gn_v14'] | None = None
    restoration_strength: float | None = Field(default=None, ge=0, le=1)
    quality: Literal['standard', 'fine'] | None = None

@app.post('/api/jobs/{jid}/render')
def render_audio_job(jid: str, values: RenderInput):
    job = get_job(jid)
    avatar = get_avatar(values.avatar_id) if values.avatar_id else None
    video_id = values.video_id or (avatar['video_id'] if avatar else job['video_id'])
    if not job['audio_ready'] or job['status'] != 'completed' or not video_id:
        raise HTTPException(400, '请先生成配音，并选择人物视频。')
    if upload_info(video_id)['kind'] != 'video':
        raise HTTPException(400, '请选择人物视频。')
    if job['video_ready']:
        return job
    render_options = {k: v for k, v in values.model_dump().items() if k in
                      ('lipsync_model', 'restoration_model', 'restoration_strength', 'quality', 'video_speed') and v is not None}
    current = model_settings.read()
    if current:
        model_settings.check_revision(values.model_config_revision)
        for category, field in [('lipsync', 'lipsync_model'), ('restoration', 'restoration_model')]:
            selected = current['selected'][category]
            if field in render_options and render_options[field] != selected:
                raise HTTPException(409, '模型配置已变化，请刷新后重新生成。')
            render_options[field] = selected
    validate_models(JobInput(**{**job, **render_options}), audio=False)
    with lock:
        if records[jid].get('deleted_at'):
            raise HTTPException(404, '作品已删除，请先到回收站恢复。')
        if records[jid]['status'] != 'completed':
            raise HTTPException(409, '这个任务已经在处理。')
        cancelled.discard(jid)
        identity = {'avatar_id': avatar['id'], 'avatar_name': avatar['name'], 'avatar_revision': avatar['revision']} if avatar and avatar['video_id'] == video_id else {}
        if not identity and video_id != job['video_id']:
            identity = {'avatar_id': '', 'avatar_name': '', 'avatar_revision': None}
        update(jid, video_id=video_id, mode='video', status='queued', stage='queued', message='等待生成视频', error=None,
               **identity, **render_options)
    pending.put((jid, True))
    return get_job(jid)

@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    job = get_job(jid)
    with lock:
        if job['status'] not in ('running', 'queued'):
            return job
        cancelled.add(jid)
        proc = processes.get(jid)
    if proc and proc.poll() is None:
        terminate_process(proc)
    update(jid, status='cancelled', stage='cancelled', message='已停止')
    return get_job(jid)

@app.get('/api/jobs/{jid}/files/{filename}')
def output_file(jid: str, filename: str, download: bool = False):
    job = get_job(jid)
    available = {'voice.wav': job['audio_ready'], 'result.mp4': job['video_ready'],
                 'unrestored.mp4': job['video_ready'] and job.get('restoration_model', 'none') != 'none'
                 and (JOBS / jid / 'unrestored.mp4').exists()}
    if filename not in available or not available[filename]:
        raise HTTPException(404, '文件尚未生成')
    return FileResponse(JOBS / jid / filename, filename=filename if download else None,
                        media_type='audio/wav' if filename.endswith('.wav') else 'video/mp4')

def call_worker(jid, stage):
    if jid in cancelled:
        raise InterruptedError()
    directory = JOBS / jid
    python, script = worker_command(stage, get_job(jid))
    cmd = worker_process(python, script, stage, directory / 'job.json')
    with (directory / (stage + '.log')).open('a', encoding='utf-8') as logfile:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=environment(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with lock:
            processes[jid] = proc
            if jid in cancelled:
                terminate_process(proc)
        tail = []
        try:
            for line in proc.stdout:
                logfile.write(line)
                logfile.flush()
                tail = (tail + [line])[-15:]
                if '@@PROGRESS ' in line:
                    try:
                        payload = json.loads(line.partition('@@PROGRESS ')[2])
                        if stage == 'lipsync' and get_job(jid).get('restoration_model', 'none') != 'none':
                            payload['progress'] = min(94, payload.get('progress', 40))
                        update(jid, **{k: v for k, v in payload.items() if k in ('stage', 'progress', 'message', 'reference_text', 'reference_language')})
                    except (ValueError, OSError):
                        pass
            code = proc.wait()
            if jid in cancelled:
                raise InterruptedError()
            if code:
                detail = ''.join(tail)
                if 'out of memory' in detail.lower():
                    raise RuntimeError('显存不足。请关闭其他使用显卡的软件，然后重试。')
                if 'Face not detected' in detail:
                    raise RuntimeError('有些画面没有检测到人脸。请使用正面、无遮挡、没有切镜头的人物视频。')
                message = next((x[8:].strip() for x in tail if x.startswith('@@ERROR ')), '')
                raise RuntimeError(message or f'{"配音" if stage == "tts" else "人脸修复" if stage == "restore" else "视频" if stage == "lipsync" else "参考声音识别"}未完成，详细原因已保存在本地任务日志中。')
        finally:
            with lock:
                processes.pop(jid, None)

def job_loop():
    while True:
        jid, reuse_audio = pending.get()
        try:
            with lock:
                if jid in cancelled or records[jid].get('deleted_at'):
                    continue
                update(jid, status='running', started_at=now(), stage='prepare', progress=2, message='准备素材')
            job = get_job(jid)
            with gpu_lock:
                if jid in cancelled:
                    raise InterruptedError()
                directory = JOBS / jid
                # Workers read fixed trusted paths from a separate input file, never user-provided shell strings.
                paths = {'video': str(upload_path(job['video_id'])) if job['video_id'] else '',
                         'reference': str(upload_path(job['audio_id']))}
                if job.get('emotion_mode') == 'audio':
                    paths['emotion_reference'] = str(upload_path(job['emotion_audio_id']))
                (directory / 'inputs.json').write_text(json.dumps(paths), encoding='utf-8')
                if not reuse_audio:
                    call_worker(jid, 'prepare')
                    needs_text = job.get('tts_model', 'gpt_sovits') == 'gpt_sovits' or (job.get('tts_model') == 'cosyvoice3' and job.get('emotion_mode', 'reference') == 'reference')
                    if needs_text and not get_job(jid)['reference_text'].strip():
                        call_worker(jid, 'transcribe')
                        remember_reference(get_job(jid))
                    call_worker(jid, 'tts')
                    if not (directory / 'voice.wav').exists():
                        raise RuntimeError('没有生成可用配音，请重试。')
                    update(jid, audio_ready=True, progress=35)
                if get_job(jid)['mode'] == 'video':
                    call_worker(jid, 'lipsync')
                    if get_job(jid).get('restoration_model', 'none') != 'none':
                        call_worker(jid, 'restore')
                    call_worker(jid, 'speed')
                    output = probe(directory / 'result.mp4')
                    if not any(x.get('codec_type') == 'video' for x in output['streams']):
                        raise RuntimeError('输出视频检查失败。')
                    update(jid, video_ready=True)
                with lock:
                    if jid in cancelled:
                        raise InterruptedError()
                    update(jid, status='completed', stage='completed', progress=100, message='视频已生成' if get_job(jid)['video_ready'] else '配音已生成', completed_at=now())
        except InterruptedError:
            update(jid, status='cancelled', stage='cancelled', message='已停止')
        except Exception as exc:
            update(jid, status='failed', stage='failed', error=str(exc), message='生成未完成')
        finally:
            pending.task_done()

def migrate_legacy_voices():
    """Split old bundled profiles without changing their media or historical job snapshots."""
    with lock:
        for avatar in list_avatars():
            if not avatar.get('audio_id') or avatar.get('voice_id'):
                continue
            vid = avatar['id']
            if not (VOICES / vid / 'profile.json').exists():
                voice = {k: avatar[k] for k in ('audio_id', 'reference_text', 'reference_language', 'language', 'created_at')}
                voice.update(id=vid, name=avatar['name'][:35] + ' 的声音', revision=1, updated_at=now())
                persist_voice(voice)
            try:
                read_voice(vid)
            except HTTPException:
                continue  # An explicitly deleted legacy voice must stay deleted after a restart.
            avatar.update(voice_id=vid, revision=avatar['revision'] + 1, updated_at=now())
            persist_avatar(avatar)

from extraction_service import ExtractionService
extractions = ExtractionService(DATA / 'extractions', gpu_lock, lambda uid: (upload_info(uid), upload_path(uid)))
app.include_router(extractions.router)

from remix_service import RemixService
remixes = RemixService(DATA / 'remixes', gpu_lock, get_voice,
                       lambda values: new_job(JobInput(**values)), get_job)
app.include_router(remixes.router)

from project_library import ProjectLibrary
project_library = ProjectLibrary(records, lock, processes, change_job_trash, remixes)
app.include_router(project_library.router)

from model_settings import ModelSettings
model_settings = ModelSettings(DATA / 'model-settings.json', remixes.hub, catalog)
remixes.model_settings = model_settings

class ModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)

@app.get('/api/model-settings')
def get_model_settings():
    return model_settings.public()

@app.put('/api/model-settings/{category}')
def select_model(category: str, values: ModelSelection, request: Request):
    cloud_local(request)
    return model_settings.select(category, values.model, values.revision)

migrate_legacy_voices()
threading.Thread(target=job_loop, daemon=True, name='generation-queue').start()
app.mount('/static', StaticFiles(directory=SOURCE / 'static'), name='static')

@app.get('/')
def index():
    return FileResponse(SOURCE / 'static/studio.html')

if __name__ == '__main__':
    import uvicorn
    model_settings.read(initialize=True)
    uvicorn.run(app, host='127.0.0.1', port=7865, log_level='info')
