from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Saved remix projects, local reference analysis, and explicit HypiHub generation."""
import copy
import hashlib
import json
import math
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from settings import ROOT, environment, JOBS
from extraction_config import WHISPER_PYTHON
from extraction_service import atomic_json, timestamp
from remix_hub import Hub, HubSubmissionUnknown, clean_error, estimate
from remix_media import analyze, assemble, ffmpeg, probe
from remix_audio import selected_groups, replace_dialogue
from remix_screenplay import Screenplay, plan_text, plan_shots

ACTIVE={'analyzing','recognizing','scripting','generating'}

class Draft(BaseModel):
    revision: int = 0
    title: str = Field(default='我的视频复刻',min_length=1,max_length=100)
    reference_id: str = ''
    person_id: str = ''
    product_ids: list[str] = Field(default_factory=list,max_length=6)
    voice_id: str = ''
    edit_mode: Literal['remix','character','imitate'] = 'remix'
    screenplay: Screenplay | None = None
    screenplay_approved: bool = False
    background_id: str = ''
    imitation_scene: str = Field(default='',max_length=1400)
    target_character: str = Field(default='',max_length=300)
    voice_mode: Literal['original','native','clone'] = 'native'
    script: str = Field(default='',max_length=8000)
    script_source: Literal['custom','original'] = 'custom'
    voice_scope: Literal['all','selected','roles'] = 'all'
    voice_segments: list[int] = Field(default_factory=list,max_length=400)
    role_voice_ids: dict[str,str] = Field(default_factory=dict,max_length=12)
    role_names: dict[str,str] = Field(default_factory=dict,max_length=12)
    speaker_overrides: dict[str,str] = Field(default_factory=dict,max_length=400)
    instructions: str = Field(default='',max_length=4000)
    aspect: Literal['9:16','16:9','1:1'] = '9:16'
    image_model: str = Field(default='',max_length=120)
    video_model: str = Field(default='',max_length=120)
    resolution: Literal['480p','720p','1080p'] = '720p'
    make_images: bool = True
    shot_prompts: list[str] = Field(default_factory=list,max_length=40)
    reuse_shots: list[int] = Field(default_factory=list,max_length=40)

class Connection(BaseModel):
    key: str = Field(min_length=1,max_length=1000)

class Approval(BaseModel):
    quote_id: str
    accepted_estimate: float = Field(ge=0,allow_inf_nan=False)

class RepairNotes(BaseModel):
    notes: dict[str,str] = Field(min_length=1,max_length=40)

class StoryboardReuse(BaseModel):
    source_index: int = Field(ge=0,le=39)
    target_index: int = Field(ge=0,le=39)

def local_settings(request):
    # The existing public gateway rewrites Host and strips Origin. Require a
    # same-origin browser request as well, so it cannot spend the local key.
    expected=f'{request.url.scheme}://{request.url.netloc}'
    if request.url.hostname not in {'127.0.0.1','localhost','::1'} or request.headers.get('origin')!=expected:
        raise HTTPException(403,'请在本机工作室连接账号或发起云端生成。')

class RemixService:
    def __init__(self, directory, gpu_lock, voice_resolver, create_audio, get_audio):
        self.root=Path(directory);self.root.mkdir(parents=True,exist_ok=True)
        self.assets=self.root/'assets';self.assets.mkdir(exist_ok=True)
        self.projects=self.root/'projects';self.projects.mkdir(exist_ok=True)
        self.hub=Hub(self.root/'private')
        self.gpu_lock,self.voice_resolver,self.create_audio,self.get_audio=gpu_lock,voice_resolver,create_audio,get_audio
        self.model_settings = None
        self.lock=threading.RLock();self.records={}
        for path in self.projects.glob('*/project.json'):
            try:
                value=json.loads(path.read_text(encoding='utf-8'))
                if path.parent.name!=value['id'] or not re.fullmatch('[0-9a-f]{32}',value['id']):continue
                if value['status'] in ACTIVE:
                    value.update(status='interrupted',message='上次处理已中断，可继续已保存的任务。')
                    atomic_json(path,value)
                if (value.get('audio_render') or {}).get('status') in {'queued','running'}:
                    value['audio_render'].update(status='failed',error='上次配音已中断，请重新生成试听。')
                    atomic_json(path,value)
                self.records[value['id']]=value
            except (ValueError,KeyError,OSError):pass
        self.router=self.routes()

    def asset(self, aid):
        if not re.fullmatch('[0-9a-f]{32}',aid):raise HTTPException(404,'素材不存在。')
        folder=self.assets/aid
        if not (folder/'meta.json').is_file():raise HTTPException(404,'素材不存在，请重新上传。')
        meta=json.loads((folder/'meta.json').read_text(encoding='utf-8'))
        return meta,folder/meta['stored_name']

    def get(self,pid):
        with self.lock:
            if pid not in self.records:raise HTTPException(404,'复刻项目不存在。')
            return copy.deepcopy(self.records[pid])

    def update(self,pid,**changes):
        with self.lock:
            value={**self.records[pid],**changes,'updated_at':timestamp()}
            atomic_json(self.projects/pid/'project.json',value);self.records[pid]=value
            return copy.deepcopy(value)

    def public(self,p):
        result={k:v for k,v in p.items() if k not in {'run','quote','repair_quote'}}
        if p.get('status')=='scripting':
            try:
                progress=json.loads((self.projects/p['id']/'analysis/screenplay-progress.json').read_text(encoding='utf-8'))
                result['message']=progress['message'];result['progress']=40+55*progress['completed']/max(1,progress['total'])
            except (OSError,ValueError,KeyError):pass
        result['assets']={}
        for aid in [p.get('reference_id'),p.get('person_id'),p.get('background_id'),*p.get('product_ids',[]),
                    *[c.get('photo_id') for c in (p.get('screenplay') or {}).get('characters',[])]]:
            if aid:
                try:result['assets'][aid]=self.asset(aid)[0]
                except HTTPException:pass
        run=p.get('run') or {}
        result['can_resume']=bool(run) and p['status'] in {'failed','interrupted','paused'}
        result['video_ready']=bool(run.get('completed'))
        result['run_id']=run.get('id','')
        result['locked']=p['status'] in ACTIVE or bool(run) and p['status'] in {'failed','paused','interrupted','completed','storyboard'}
        result['reuse_source_mode']=self.records.get((p.get('reuse_source') or {}).get('project_id'),{}).get('edit_mode')
        result['storyboard']=[]
        if p.get('edit_mode')=='imitate' and run:
            for i in [-1,*range(len((p.get('screenplay') or {}).get('shots',[])))]:
                filename='master.png' if i==-1 else f'image-{i:03d}.png'
                if (self.projects/p['id']/run['id']/filename).is_file():
                    revision=run.get('image_revisions',{}).get(str(i),'0')
                    result['storyboard'].append({'index':i,'url':f'/api/remixes/{p["id"]}/storyboard/{i}?run={run["id"]}&revision={revision}'})
            shots=(p.get('screenplay') or {}).get('shots',[])
            folder=self.projects/p['id']/run['id']
            videos=sum((folder/f'video-{s["index"]:03d}.mp4').is_file() for s in shots)
            images=sum(b['index']>=0 for b in result['storyboard'])
            stage='complete' if run.get('completed') else 'video' if run.get('storyboard_approved') else 'review' if p['status']=='storyboard' else 'images'
            result['workflow']={'stage':stage,'total':len(shots),'images_done':images,'videos_done':videos,
                'storyboard_approved':bool(run.get('storyboard_approved')),'active_slot':run.get('active_slot',''),
                'pause_requested':bool(run.get('stop')),
                'connection':run.get('connection'),
                'video_estimate':round(sum(x.get('video_credits',0) for x in (run.get('approved_quote') or {}).get('items',[])),2)}
        result['dialogue_segments']=self.dialogue_rows(p)
        result['roles']=self.role_cards(p)
        result['audio_preview']=self.audio_preview(p)
        return result

    def dialogue_rows(self,p):
        if not p.get('analysis'):return []
        path=self.projects/p['id']/'analysis/asr.json'
        try:
            rows=json.loads(path.read_text(encoding='utf-8'))['segments']
            return [{k:r[k] for k in ('id','start','end','text')} for r in rows]
        except (OSError,ValueError,KeyError):return []

    def effective_script(self,p):
        if p.get('edit_mode')=='imitate' and p.get('screenplay'):return plan_text(p)
        return p.get('transcript','').strip() if p.get('script_source')=='original' else p.get('script','').strip()

    def role_cards(self,p):
        cast=p.get('cast') or {};rows=self.dialogue_rows(p)
        assigned={**cast.get('assignments',{}),**p.get('speaker_overrides',{})}
        result=[]
        for role in cast.get('roles',[]):
            members=[r for r in rows if assigned.get(str(r['id']))==role['id']]
            sample=next((r for r in members if r['id']==role['sample_segment']),members[0] if members else None)
            result.append({**role,'name':p.get('role_names',{}).get(role['id'],role['name']),
                'segment_ids':[r['id'] for r in members],'sample':sample,
                'voice_id':p.get('role_voice_ids',{}).get(role['id'],''),
                'seconds':round(sum(r['end']-r['start'] for r in members),2)})
        return result

    def role_voice_rows(self,p):
        by_segment={sid:role['voice_id'] for role in self.role_cards(p) if role['voice_id'] for sid in role['segment_ids']}
        return [(row,by_segment[row['id']]) for row in self.dialogue_rows(p) if row['id'] in by_segment]

    def selected_voice(self,p):
        if p.get('edit_mode')=='imitate':return False
        return p.get('voice_mode')=='clone' and p.get('voice_scope') in {'selected','roles'}

    def replacement_groups(self,p):
        if p.get('voice_scope')!='roles':
            return [{**row,'voice_id':p['voice_id']} for row in selected_groups(self.dialogue_rows(p),p.get('voice_segments',[]))]
        if not p.get('cast'):raise HTTPException(400,'请先识别原视频中的角色。')
        rows=self.role_voice_rows(p);groups=[]
        for voice in dict.fromkeys(voice for _,voice in rows):
            selected=[row['id'] for row,v in rows if v==voice]
            groups.extend({**g,'voice_id':voice} for g in selected_groups(self.dialogue_rows(p),selected))
        return sorted(groups,key=lambda r:r['start'])

    def effective_voice(self,vid,p=None):
        if p and p.get('run'):
            return (p.get('speech_snapshots') or {}).get(vid) or self.voice_resolver(vid)
        voice=self.voice_resolver(vid)
        return self.model_settings.speech(voice) if self.model_settings else voice

    def speech_job_values(self,voice):
        fields={'audio_id','reference_text','reference_language','language','tts_model',
                'emotion_mode','emotion_preset','emotion_strength','emotion_audio_id'}
        return {k:v for k,v in voice.items() if k in fields}

    def audio_signature(self,p,snapshots=None):
        resolve=lambda vid:(snapshots or {}).get(vid) or self.effective_voice(vid,p)
        if p.get('voice_scope')=='roles':
            rows=self.role_voice_rows(p)
            voices=list(dict.fromkeys(voice for _,voice in rows))
            if not voices:raise HTTPException(400,'为需要更换音色的角色选择声音。')
            # Preserve a valid audition when migrating from selected lines to the
            # same complete role. Labels and grouping do not change the sound.
            if len(voices)==1:
                return self.audio_signature({**p,'voice_scope':'selected','voice_id':voices[0],'voice_segments':[r['id'] for r,_ in rows]}, {vid:resolve(vid) for vid in voices})
            content=[p.get('reference_id'),self.effective_script(p),rows,
                     {voice:resolve(voice) for voice in voices}]
            return hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        voice=resolve(p['voice_id'])
        content=[self.effective_script(p),voice,p.get('reference_id'),p.get('script_source','custom'),p.get('voice_scope','all')]
        if self.selected_voice(p):
            content.append([r for r in self.dialogue_rows(p) if r['id'] in p.get('voice_segments',[])])
        return hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    def audio_preview(self,p):
        if p.get('voice_mode')!='clone' or not (p.get('voice_id') or any(p.get('role_voice_ids',{}).values())):return None
        try:
            signature=self.audio_signature(p)
            if self.selected_voice(p):
                render=p.get('audio_render') or {}
                if render.get('signature')!=signature:return None
                ready=render.get('status')=='completed' and (self.projects/p['id']/'audio'/render['id']/'voice.wav').is_file()
                return {k:render.get(k) for k in ('id','status','message','error')} | {'audio_ready':ready,
                    'url':f'/api/remixes/{p["id"]}/voice-audio?v={render["id"]}' if ready else ''}
            if p.get('audio_signature')!=signature or not p.get('audio_job_id'):return None
            job=self.get_audio(p['audio_job_id'])
            ready=job['status']=='completed' and (JOBS/job['id']/'voice.wav').is_file()
            return {k:job.get(k) for k in ('id','status','message','error')} | {'audio_ready':ready,
                'url':f'/api/jobs/{job["id"]}/files/voice.wav' if ready else ''}
        except (HTTPException,KeyError,OSError):return None

    def require_audio(self,p):
        if p['voice_mode']!='clone':return
        audio=self.audio_preview(p)
        if not audio or not audio['audio_ready']:
            raise HTTPException(400,'当前声音或台词尚未准备好，请点击「生成配音并查看费用」。')

    def audio_path(self,p):
        self.require_audio(p)
        if self.selected_voice(p):return self.projects/p['id']/'audio'/p['audio_render']['id']/'voice.wav'
        return JOBS/p['audio_job_id']/'voice.wav'

    def save(self,values,pid=None):
        with self.lock:
            old=self.get(pid) if pid else {}
            if old.get('status') in ACTIVE or old.get('status') in {'failed','paused','interrupted','completed','storyboard'} and old.get('run'):
                raise HTTPException(409,'请先完成当前任务，或点击「复用为新项目」后修改，已完成的视频会保留。')
            if old and values.revision!=old['revision']:raise HTTPException(409,'项目已在其他窗口更新，请重新打开。')
            data=values.model_dump(exclude={'revision'})
            if self.model_settings:data=self.model_settings.visuals(data)
            if data['edit_mode']=='imitate':
                data.update(make_images=True,voice_scope='all',voice_segments=[],role_voice_ids={})
                if len(data['product_ids'])>2:raise HTTPException(400,'模仿创作最多使用 2 张产品参考图。')
                for c in (data.get('screenplay') or {}).get('characters',[]):
                    if c.get('photo_id') and self.asset(c['photo_id'])[0]['role']!='person':raise HTTPException(400,'请为角色上传人物照片。')
                if data['background_id'] and self.asset(data['background_id'])[0]['role']!='background':raise HTTPException(400,'请上传门店背景照片。')
            changed_ref=old.get('reference_id')!=data['reference_id']
            if changed_ref:
                data.update(role_voice_ids={},role_names={},speaker_overrides={},voice_segments=[])
            else:
                roles={r['id'] for r in (old.get('cast') or {}).get('roles',[])}
                row_ids={str(r['id']) for r in self.dialogue_rows(old)}
                if any(r not in roles for field in ('role_voice_ids','role_names') for r in data[field]):raise HTTPException(400,'角色已变化，请重新打开项目。')
                if any(s not in row_ids or r not in roles for s,r in data['speaker_overrides'].items()):raise HTTPException(400,'台词归属无效，请重新打开项目。')
                if any(not name.strip() or len(name)>60 for name in data['role_names'].values()):raise HTTPException(400,'角色名称请填写 1–60 个字。')
                for voice in set(data['role_voice_ids'].values()):
                    if voice:self.voice_resolver(voice)
            if data['edit_mode']=='character':
                data.update(voice_mode='original',product_ids=[],voice_id='',script='')
            if data['voice_scope'] in {'selected','roles'} and data['voice_mode']=='clone':
                if data['script_source']!='original':raise HTTPException(400,'按角色更换声音时，请选择「沿用原文」。')
                rows=self.dialogue_rows(old)
                if any(i not in {r['id'] for r in rows} for i in data['voice_segments']):raise HTTPException(400,'台词片段已变化，请重新勾选。')
                data['voice_segments']=sorted(set(data['voice_segments']))
            if not data['title'].strip():raise HTTPException(400,'请填写项目名称。')
            for aid,kind in [(data['reference_id'],'reference'),(data['person_id'],'person'),*[(x,'product') for x in data['product_ids']]]:
                if aid and self.asset(aid)[0]['role']!=kind:raise HTTPException(400,'请选择对应类型的素材。')
            if data['voice_id']:self.voice_resolver(data['voice_id'])
            if any(len(p)>2000 for p in data['shot_prompts']):raise HTTPException(400,'单个镜头描述最多 2000 字。')
            if data['reuse_shots']:
                source=self.reuse_source(old)
                if not source:raise HTTPException(400,'请先复用一个已完成的视频项目，再选择保留的镜头。')
                keys=['reference_id','person_id','product_ids','edit_mode','voice_mode','script','script_source','voice_scope','voice_segments','voice_id','role_voice_ids','speaker_overrides','aspect','resolution','make_images']
                defaults=Draft().model_dump()
                if any(data.get(k)!=source.get(k,defaults[k]) for k in keys):raise HTTPException(400,'复用镜头时请保留原素材、声音与生成设置；可修改需重做镜头的描述。')
                if any(i<0 or i>=len(source['analysis']['shots']) for i in data['reuse_shots']):raise HTTPException(400,'请选择有效的复用镜头。')
                data['reuse_shots']=sorted(set(data['reuse_shots']))
            pid=pid or uuid.uuid4().hex;(self.projects/pid).mkdir(exist_ok=True)
            value={**old,**data,'id':pid,'revision':old.get('revision',0)+1,'created_at':old.get('created_at',timestamp()),
                   'updated_at':timestamp(),'status':'draft','message':'项目已保存','error':None,'quote':None}
            if changed_ref:value.update(analysis=None,shot_prompts=[],transcript='',voice_segments=[],cast=None,cast_error=None)
            if changed_ref:value.update(screenplay=None,screenplay_approved=False)
            sound_keys=['reference_id','voice_mode','voice_id','script','script_source','voice_scope','voice_segments','role_voice_ids','speaker_overrides']
            if data['edit_mode']=='imitate':sound_keys+=['screenplay']
            if any(value.get(k)!=old.get(k) for k in sound_keys):
                same_audio=False
                try:same_audio=value['voice_mode']==old.get('voice_mode')=='clone' and self.audio_signature(value)==self.audio_signature(old)
                except (HTTPException,KeyError,OSError):pass
                if not same_audio:value.update(audio_job_id=None,audio_signature=None,audio_render=None)
            # A changed brief starts a new generation; existing output files stay on disk.
            if any(data.get(k)!=old.get(k) for k in data):value['run']=None
            self.records[pid]=value
            atomic_json(self.projects/pid/'project.json',value)
            return self.public(value)

    def duplicate(self,pid):
        with self.lock:
            old=self.get(pid)
            if old['status'] in ACTIVE:raise HTTPException(409,'请等待当前处理完成后复用。')
            data={k:old[k] for k in Draft.model_fields if k in old}
            data.update(revision=0,title=(old['title'][:90]+' · 副本'),reuse_shots=[])
            selections={k:data.get(k,{} if k!='voice_segments' else []) for k in ('voice_segments','role_voice_ids','role_names','speaker_overrides')}
            data.update(voice_segments=[],role_voice_ids={},role_names={},speaker_overrides={})
            new=self.save(Draft(**data));nid=new['id']
            if old.get('analysis'):
                shutil.copytree(self.projects/pid/'analysis',self.projects/nid/'analysis')
                self.update(nid,analysis=old['analysis'],transcript=old.get('transcript',''),shot_prompts=old['shot_prompts'],cast=old.get('cast'),**selections,status='ready')
            if old.get('edit_mode')=='imitate':
                self.update(nid,screenplay=old.get('screenplay'),screenplay_approved=False)
            if (old.get('run') or {}).get('completed'):
                self.update(nid,reuse_source={'project_id':pid,'run_id':old['run']['id']})
            return self.public(self.get(nid))

    def reuse_source(self,p):
        reference=p.get('reuse_source') or {}
        if not reference:return None
        source=self.get(reference['project_id'])
        run=source.get('run') or {}
        if run.get('completed') and run.get('id')==reference.get('run_id'):return source
        raise HTTPException(409,'原项目成片不可用，无法复用镜头。')

    def launch_analysis(self,pid):
        with self.lock:
            p=self.get(pid)
            if p['status'] in ACTIVE:raise HTTPException(409,'此项目正在处理。')
            if not p['reference_id']:raise HTTPException(400,'请先上传对标视频。')
            needs_text=p['voice_mode']!='original' and not p.get('transcript')
            if p.get('analysis') and not needs_text:return self.public(p)
            self.update(pid,status='analyzing',message='正在提取参考镜头',progress=5,error=None)
            threading.Thread(target=self.analysis_worker,args=(pid,),daemon=True).start()
            return self.public(self.get(pid))

    def analysis_worker(self,pid):
        try:
            p=self.get(pid);folder=self.projects/pid/'analysis'
            evidence=p.get('analysis') or analyze(self.asset(p['reference_id'])[1],folder)
            transcript='';warning=''
            if evidence['has_audio'] and p['voice_mode']!='original':
                task=folder/'task.json';atomic_json(task,{'language':'auto','keywords':''})
                self.update(pid,message='镜头已拆分，等待本地声音识别',progress=60)
                with self.gpu_lock:
                    result=subprocess.run([str(WHISPER_PYTHON),'-u',str(SOURCE / 'worker_extract.py'),'transcribe',str(task)],
                                          cwd=ROOT,env=environment(),capture_output=True,text=True,encoding='utf-8',errors='replace',
                                          timeout=900,creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode==0:
                    transcript=json.loads((folder/'transcript.json').read_text(encoding='utf-8'))['text']
                    rows=json.loads((folder/'asr.json').read_text(encoding='utf-8'))['segments']
                    for shot in evidence['shots']:
                        shot['text']=''.join(r['text'] for r in rows if shot['start']<=(r['start']+r['end'])/2<shot['end'])
                else:warning='未能识别清晰人声；镜头拆解已保留，可以手动填写新文案。'
            self.update(pid,analysis=evidence,transcript=transcript,status='ready',progress=100,
                        message='参考镜头已拆解',warning=warning,revision=p['revision']+1)
            if transcript and p.get('voice_scope')=='roles' and p['voice_mode']=='clone':
                self.update(pid,status='recognizing',message='正在识别原片角色',progress=90)
                self.roles_worker(pid)
        except Exception as e:self.update(pid,status='failed',error=clean_error(e),message='参考拆解未完成')

    def launch_roles(self,pid):
        with self.lock:
            p=self.get(pid)
            if self.public(p)['locked']:raise HTTPException(409,'请先完成当前处理，或复用为新项目。')
            if p.get('cast'):return self.public(p)
            if not p.get('transcript') or not self.dialogue_rows(p):raise HTTPException(400,'请先准备原视频台词。')
            self.update(pid,status='recognizing',message='正在识别原片角色',progress=80,error=None,cast_error=None)
            threading.Thread(target=self.roles_worker,args=(pid,),daemon=True).start()
            return self.public(self.get(pid))

    def roles_worker(self,pid):
        try:
            p=self.get(pid);folder=self.projects/pid/'analysis'
            result=subprocess.run([str(venv_python('.speechvenv')),'-u',str(SOURCE / 'worker_roles.py'),str(folder)],
                cwd=ROOT,env=environment(),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:raise RuntimeError(result.stderr[-500:] or '角色识别未完成，请重试。')
            cast=json.loads((folder/'speakers.json').read_text(encoding='utf-8'))
            self.update(pid,cast=cast,cast_error=None,status='ready',progress=100,message='原片角色已识别',revision=p['revision']+1)
        except Exception as e:
            self.update(pid,status='ready',cast_error=clean_error(e),message='角色识别未完成，可重试')

    def voice_job(self,pid,cloud_confirmed=False,model_config_revision=None):
        with self.lock, (self.model_settings.lock if self.model_settings else nullcontext()):
            if self.model_settings:self.model_settings.check_revision(model_config_revision)
            p=self.get(pid)
            if p['status'] in ACTIVE:raise HTTPException(409,'请等待当前处理完成。')
            if p['voice_mode']!='clone':raise HTTPException(400,'请先选择使用克隆声音。')
            if p.get('voice_scope')!='roles' and not p['voice_id']:raise HTTPException(400,'请选择一个已克隆声音。')
            script=self.effective_script(p)
            if not script:raise HTTPException(400,'请先提取参考原文，或填写新的台词。')
            signature=self.audio_signature(p)
            from cloud_speech import MODELS as CLOUD_MODELS, cloud
            ids=set(p.get('role_voice_ids',{}).values()) if p.get('voice_scope')=='roles' else {p.get('voice_id')}
            snapshots={vid:self.effective_voice(vid) for vid in filter(None,ids)}
            for vid in filter(None,ids):
                profile=snapshots[vid]
                if profile.get('tts_model') in CLOUD_MODELS:
                    if not cloud_confirmed:raise HTTPException(400,'该角色使用云端声音，请确认本次配音费用后生成。')
                    if not cloud.binding(profile['tts_model'],profile['audio_id']):raise HTTPException(400,'请先到语音克隆页启用该角色的云端声音。')
            if self.selected_voice(p):
                groups=self.replacement_groups(p)
                if not groups:raise HTTPException(400,'请为需要更换音色的角色选择声音，其他角色会保留原声。')
                current=self.audio_preview(p)
                if current and current['status'] not in {'failed','cancelled'} and (current['status']!='completed' or current['audio_ready']):return current
                render={'id':uuid.uuid4().hex,'signature':signature,'status':'queued','message':'准备角色配音','error':None,'jobs':[]}
                self.update(pid,audio_render=render,quote=None,speech_snapshots=snapshots)
                threading.Thread(target=self.dialogue_worker,args=(pid,render['id'],groups,cloud_confirmed,snapshots),daemon=True).start()
                return self.audio_preview(self.get(pid))
            if p.get('audio_signature')==signature and p.get('audio_job_id'):
                try:job=self.get_audio(p['audio_job_id'])
                except HTTPException:job={'status':'failed'}
                if job['status'] not in {'failed','cancelled'} and (job['status']!='completed' or (JOBS/p['audio_job_id']/'voice.wav').is_file()):return job
            job=self.create_audio({**self.speech_job_values(snapshots[p['voice_id']]),'voice_id':p['voice_id'],'text':script,'mode':'audio','cloud_confirmed':cloud_confirmed})
            self.update(pid,audio_job_id=job['id'],audio_signature=signature,quote=None,speech_snapshots=snapshots)
            return job

    def dialogue_worker(self,pid,rid,groups,cloud_confirmed=False,snapshots=None):
        def publish(**changes):
            with self.lock:
                current=self.get(pid);render=current.get('audio_render') or {}
                if render.get('id')!=rid:return False
                self.update(pid,audio_render={**render,**changes});return True
        try:
            p=self.get(pid);folder=self.projects/pid/'audio'/rid;folder.mkdir(parents=True,exist_ok=True)
            entries=[];jobs=[]
            for i,row in enumerate(groups):
                if not publish(status='running',message=f'正在克隆第 {i+1} / {len(groups)} 段台词'):return
                vid=row.get('voice_id',p.get('voice_id',''))
                job=self.create_audio({**self.speech_job_values((snapshots or {}).get(vid) or self.effective_voice(vid)),
                    'voice_id':vid,'text':row['text'],'mode':'audio','cloud_confirmed':cloud_confirmed})
                jobs.append({'segment_ids':row['ids'],'job_id':job['id']});publish(jobs=jobs)
                deadline=time.monotonic()+1800
                while job['status'] not in {'completed','failed','cancelled'}:
                    if time.monotonic()>deadline:raise RuntimeError('本地配音等待超时，请检查任务后重试。')
                    time.sleep(1)
                    if (self.get(pid).get('audio_render') or {}).get('id')!=rid:return
                    job=self.get_audio(job['id'])
                if job['status']!='completed':raise RuntimeError(job.get('error') or '本地配音未完成，请重试。')
                entries.append((row,JOBS/job['id']/'voice.wav'))
            if self.audio_signature(self.get(pid),snapshots)!=p['audio_render']['signature']:
                raise RuntimeError('声音设置已经变化，请重新生成配音。')
            if not publish(message='对齐原片台词，保留未选片段的原声'):return
            replace_dialogue(self.asset(p['reference_id'])[1],entries,folder)
            publish(status='completed',message='角色配音已完成，其他片段保留原声')
        except Exception as e:publish(status='failed',error=clean_error(e),message='配音尚未完成，请重试')

    def quote(self,pid):
        p=self.get(pid)
        if p.get('edit_mode')=='imitate':
            from remix_imitation import quote
            return quote(self,pid)
        if self.public(p)['locked']:raise HTTPException(409,'请继续当前任务，或复用为新项目后生成。')
        if self.model_settings:
            configured=self.model_settings.visuals(p)
            if any(configured[k]!=p[k] for k in ('image_model','video_model')):
                self.update(pid,image_model=configured['image_model'],video_model=configured['video_model'],revision=p['revision']+1,quote=None)
                p=self.get(pid)
        if not p.get('analysis'):raise HTTPException(400,'请先拆解对标视频。')
        if p.get('edit_mode')=='character':
            if not p.get('person_id'):raise HTTPException(400,'请上传要换成的人物照片。')
            if not p.get('target_character','').strip():raise HTTPException(400,'请说明原片里要替换谁，例如：戴帽子的猴子。')
        if p.get('reuse_shots'):
            source=self.reuse_source(p)
            if not source:raise HTTPException(400,'原项目不可用，请重新打开。')
            for i in p['reuse_shots']:
                if not (self.projects/source['id']/source['run']['id']/f'video-{i:03d}.mp4').is_file():raise HTTPException(400,'原镜头文件缺失，无法复用，请先恢复文件。')
        if not self.hub.configured():raise HTTPException(400,'请先连接 HypiHub，再获取当前账号的生成报价。')
        self.require_audio(p)
        cards={c['id']:c for c in self.hub.models(refresh=True)}
        image=cards.get(p['image_model'],{});video=cards.get(p['video_model'],{})
        if 'videos' not in video.get('endpoints',[]):raise HTTPException(400,'请选择账号可用的视频模型。')
        # Reference video + identity references are documented for these model families.
        if p['video_model'].split('/')[-1] not in {'seedance-2','seedance-2-mini','seedance-2-fast','seedance-2.5'}:
            raise HTTPException(400,'当前复刻流程请选择支持参考视频的 Seedance 模型。')
        if p['make_images'] and 'image_edits' not in image.get('endpoints',[]):raise HTTPException(400,'请选择支持图片编辑的生图模型。')
        items=[]
        if len(p.get('reuse_shots',[]))==len(p['analysis']['shots']):raise HTTPException(400,'至少选择一个需要重做的镜头，或直接下载原成片。')
        for s in p['analysis']['shots']:
            if s['index'] in p.get('reuse_shots',[]):
                items.append({'shot':s['index']+1,'seconds':s['seconds'],'image_credits':0,'video_credits':0,'reused':True});continue
            image_cost=estimate(image,resolution='1k') if p['make_images'] else 0
            video_cost=estimate(video,seconds=s['seconds'],resolution=p['resolution'],reference_seconds=s['end']-s['start'],audio=p['voice_mode']=='native')
            items.append({'shot':s['index']+1,'seconds':s['seconds'],'image_credits':image_cost,'video_credits':video_cost})
        q={'id':uuid.uuid4().hex,'revision':p['revision'],'created':time.time(),'items':items,
           'model_config_revision':(self.model_settings.read() or {}).get('revision') if self.model_settings else None,
           'total':round(sum(i['image_credits']+i['video_credits'] for i in items),4),
           'note':'按当前模型价目估算，实际以 HypiHub 结算为准。参考片段及替换素材将上传至 HypiHub；已提交的云端镜头不能撤销。',
           'source_seconds':p['analysis'].get('duration'), 'voice_mode':p['voice_mode'],
           'voice_scope':p.get('voice_scope','all'),
           'edit_mode':p.get('edit_mode','remix')}
        self.update(pid,quote=q)
        return q

    def start(self,pid,approval):
        with self.lock:
            p=self.get(pid);q=p.get('quote')
            if q and self.model_settings:self.model_settings.check_revision(q.get('model_config_revision'))
            if q and self.model_settings and any(self.model_settings.visuals(p)[k]!=p[k] for k in ('image_model','video_model')):
                raise HTTPException(409,'生成模型已变化，请重新查看预计积分。')
            if p['status'] in ACTIVE:raise HTTPException(409,'该项目已经在处理。')
            if not q or q['id']!=approval.quote_id or q['revision']!=p['revision'] or time.time()-q['created']>600:
                raise HTTPException(409,'报价已过期或项目已修改，请重新查看预计积分。')
            if not math.isclose(q['total'],approval.accepted_estimate,abs_tol=.00001):raise HTTPException(400,'请确认当前报价。')
            self.require_audio(p)
            try:self.hub.ensure_uploader()
            except RuntimeError as e:raise HTTPException(400,str(e)) from None
            run={'id':uuid.uuid4().hex,'approved_quote':q,'slots':{},'stop':False,'completed':False}
            (self.projects/pid/run['id']).mkdir()
            self.update(pid,run=run,status='generating',message='准备生成素材',progress=1,error=None,quote=None)
            threading.Thread(target=self.generate_worker,args=(pid,),daemon=True).start()
            return self.public(self.get(pid))

    def remote_job(self,pid,slot,path,body,target):
        p=self.get(pid);run=p['run'];receipt=run['slots'].get(slot,{})
        if target.is_file():return
        def mark_connection(attempt,delay):
            with self.lock:
                current=self.get(pid)['run']
                if current.get('stop'):raise RuntimeError('已暂停任务')
                current['connection']={'state':'retrying','attempt':attempt,'delay':delay}
                self.update(pid,run=current,message=f'连接波动，{delay} 秒后重连 · 已完成内容已保存')
        with self.lock:
            current=self.get(pid)['run'];current['active_slot']=slot;current['connection']=None
            phase,index=slot.split('-')[:2];index=int(index)
            message='正在准备统一定妆图' if phase=='master' else f'第 {index+1} / {len(plan_shots(p))} 个镜头 · '+('准备分镜图' if phase=='image' else '准备视频生成')
            self.update(pid,run=current,message=message)
        if not receipt.get('job_id'):
            request_id=receipt.get('request_id') or 'studio-'+run['id']+'-'+slot
            digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if receipt.get('payload_hash') and receipt['payload_hash']!=digest:
                raise RuntimeError('本次请求内容已变化，请复用为新项目，避免覆盖原请求。')
            receipt={**receipt,'request_id':request_id,'payload_hash':digest,'state':'submitting'}
            with self.lock:
                current=self.get(pid)['run'];current['slots'][slot]=receipt;self.update(pid,run=current)
            try:
                created=self.hub.request('POST',path,body=body,request_id=request_id,on_retry=mark_connection)
                if not isinstance(created,dict) or not isinstance(created.get('id'),str) or not created['id']:
                    raise HubSubmissionUnknown('视频服务未返回有效任务编号；已保留原请求，请勿新建重复任务。')
            except HubSubmissionUnknown:
                with self.lock:
                    current=self.get(pid)['run'];current['slots'][slot]['state']='submission_unknown'
                    current['connection']={'state':'submission_unknown'};self.update(pid,run=current)
                raise
            receipt={**receipt,'job_id':created['id'],'state':'accepted'}
            with self.lock:
                current=self.get(pid)['run'];current['slots'][slot]=receipt;current['connection']=None;self.update(pid,run=current)
        deadline=time.monotonic()+3600
        while time.monotonic()<deadline:
            job=self.hub.request('GET','/jobs/'+receipt['job_id'],on_retry=mark_connection)
            with self.lock:
                current_run=self.get(pid)['run']
                if current_run.get('connection'):
                    current_run['connection']=None;self.update(pid,run=current_run)
            kind,index=slot.split('-')[:2];index=int(index);count=len(plan_shots(p))
            repairing=kind=='repair'
            if repairing:kind='image'
            try:percent=max(0,min(100,float(job.get('progress') or 0)))/100
            except (TypeError,ValueError):percent=0
            has_images=p['make_images']
            stage=percent*.35 if kind=='image' else (.35+.65*percent if has_images else percent)
            message=f'第 {index+1} / {count} 个镜头 · '+('生成替换图' if kind=='image' else '生成动态视频')
            progress=round(5+85*(index+stage)/count,1)
            if p.get('edit_mode')=='imitate':
                message='建立统一角色与门店定妆图' if kind=='master' else f'第 {index+1} / {count} 个镜头 · '+('生成分镜图' if kind=='image' else '生成新视频')
                progress=round(5+5*percent if kind=='master' else 10+30*(index+percent)/count if kind=='image' else 40+50*(index+percent)/count,1)
                if repairing:message=f'正在修正镜头 {index+1} 的分镜图'
            current=self.get(pid)
            if current.get('message')!=message or current.get('progress')!=progress:
                self.update(pid,message=message,progress=progress)
            if job['status']=='succeeded':
                self.hub.download(receipt['job_id'],target);return
            if job['status'] in {'failed','queue_expired'}:raise RuntimeError('云端镜头生成失败：'+clean_error(job.get('error','请检查模型参数。')))
            time.sleep(5)
        raise RuntimeError('云端仍未返回结果，任务编号已保存，可稍后继续查询。')

    def narration(self,p):
        """Partition new copy once, near punctuation, so shots don't repeat the whole script."""
        text=self.effective_script(p);shots=p['analysis']['shots'];total=sum(s['seconds'] for s in shots)
        boundaries=[m.end() for m in re.finditer(r'[，。！？；,.!?;\s]',text)]
        rows=[];start=0;elapsed=0
        for shot in shots[:-1]:
            elapsed+=shot['seconds'];ideal=round(len(text)*elapsed/total)
            nearby=[b for b in boundaries if start<b<len(text) and abs(b-ideal)<=max(5,len(text)//len(shots)//3)]
            end=min(nearby,key=lambda b:abs(b-ideal)) if nearby else max(start,ideal)
            rows.append(text[start:end]);start=end
        return [*rows,text[start:]]

    def prompt(self,p,shot):
        details=p['shot_prompts'][shot['index']] if len(p['shot_prompts'])>shot['index'] else ''
        if p.get('edit_mode')=='character' or self.selected_voice(p) and p.get('person_id') and p.get('target_character') and not p['product_ids']:
            return ('执行局部角色替换，严格沿用参考视频。唯一替换目标：'+p['target_character']+'。'
                    '将这个目标角色换成上传人物参考中的真人身份、五官、发型和人体特征，保持目标人物跨镜头一致。'
                    '人物照片只提供身份参考，绝不采用照片里的服装或背景。原角色衣服的款式、颜色、材质及穿戴配件必须保留，所有镜头穿着一致。'
                    '保留原角色在该镜头中的位置、身体动作、姿态、视线、表情和开口节奏。'
                    '其他人物和动物绝不能换脸或改变身份；背景、物品、构图、镜头运动、光线、字幕和镜头顺序尽量保持原样。'
                    '如果该镜头没有出现指定目标，保持原镜头，不要凭空添加人物。'
                    '不要新增情节、对白、旁白、音乐或音效；保持原文和开口节奏，成片由本地配入已准备好的声音。'
                    f'动作对应原片 {shot["start"]:.3f} 到 {shot["end"]:.3f} 秒，保留这一段完整动作及节奏。'
                    '\n补充要求：'+p['instructions']+'\n本镜头要求：'+details)
        narration=self.narration(p)[shot['index']]
        return ('参考视频只用于学习这个镜头的动作、景别、构图和镜头节奏，生成新的素材。'
                '人物参考用于目标人物身份和外观；产品参考用于目标产品的外形、颜色、商标和包装。'
                '只替换提供了参考素材的对象，未提供的对象保持原场景语义。保持目标人物与产品跨镜头一致。'+
                ('唯一替换角色：'+p['target_character']+'。其他人物身份、外观不变。' if p.get('target_character') else '')+
                '画面中不要复制原视频字幕、水印和界面控件。\n创作要求：'+p['instructions']+'\n本镜头要求：'+details+
                '\n本镜头对应的文案：'+narration+
                ('\n保留参考片段中的开口与动作节奏，成片使用原视频的声音，不要生成新声音。' if p['voice_mode']=='original' else
                 '\n生成可用于后期旁白的画面，不要生成对白。' if p['voice_mode']=='clone' else
                 '\n只说本镜头对应的文案，不重复其他镜头的内容；文案为空时只生成环境声音。'))

    def render_aspect(self,p):
        if p['voice_mode']=='original' or self.selected_voice(p):
            a=p['analysis'];ratio=a['width']/a['height']
            return min({'9:16':9/16,'16:9':16/9,'1:1':1},key=lambda key:abs(math.log(ratio/({'9:16':9/16,'16:9':16/9,'1:1':1}[key]))))
        return p['aspect']

    def generate_worker(self,pid):
        if self.get(pid).get('edit_mode')=='imitate':
            from remix_imitation import generate
            return generate(self,pid)
        try:
            p=self.get(pid);run=p['run'];folder=self.projects/pid/run['id'];analysis=self.projects/pid/'analysis'
            self.require_audio(p)
            aspect=self.render_aspect(p)
            refs=[]
            for aid in [p.get('person_id'),*p.get('product_ids',[])]:
                if aid:
                    meta,path=self.asset(aid)
                    refs.append((path.parent/'poster.jpg',meta['role']=='person'))
            uploaded=[]
            for i,(path,person) in enumerate(refs):
                cached=folder/f'ref-{i}.json'
                if not cached.exists():atomic_json(cached,{'url':self.hub.upload(path,person)})
                uploaded.append(json.loads(cached.read_text(encoding='utf-8'))['url'])
            clips=[]
            source=self.reuse_source(p) if p.get('reuse_shots') else None
            for shot in p['analysis']['shots']:
                if self.get(pid)['run'].get('stop'):
                    self.update(pid,status='paused',message='已暂停后续镜头，已提交的镜头已保存');return
                i=shot['index'];self.update(pid,message=f'正在生成第 {i+1} / {len(p["analysis"]["shots"])} 个镜头',progress=5+85*i/len(p['analysis']['shots']))
                if i in p.get('reuse_shots',[]):
                    target=folder/f'video-{i:03d}.mp4'
                    previous=self.projects/source['id']/source['run']['id']/target.name
                    if not previous.is_file():raise RuntimeError(f'原项目第 {i+1} 个镜头文件缺失，请恢复原文件后继续。')
                    if not target.is_file():shutil.copyfile(previous,target)
                    clips.append(target);continue
                frame=analysis/shot['frame'];image_refs=list(uploaded)
                if p['make_images']:
                    import base64
                    reference='data:image/jpeg;base64,'+base64.b64encode(frame.read_bytes()).decode()
                    target=folder/f'image-{i:03d}.png'
                    roles='、'.join(['第一张图：镜头构图']+(['第二张图：目标人物身份'] if p.get('person_id') else [])+[f'第 {j+2+bool(p.get("person_id"))} 张图：目标产品参考 {j+1}' for j in range(len(p['product_ids']))])
                    self.remote_job(pid,f'image-{i}','/images/edits',{'model':p['image_model'],'prompt':self.prompt(p,shot)+'\n图片用途：'+roles,
                         'n':1,'resolution':'1k','output_format':'png','aspect_ratio':aspect,'reference_images':[reference,*uploaded]},target)
                    cached=folder/f'image-{i}.json'
                    if not cached.exists():atomic_json(cached,{'url':self.hub.upload(target)})
                    image_refs=[json.loads(cached.read_text(encoding='utf-8'))['url'],*uploaded]
                cached=folder/f'clip-{i}.json'
                if not cached.exists():atomic_json(cached,{'url':self.hub.upload(analysis/shot['clip'])})
                reference_video=json.loads(cached.read_text(encoding='utf-8'))['url']
                target=folder/f'video-{i:03d}.mp4'
                image_roles=(['新分镜构图与外观'] if p['make_images'] else [])+(['目标人物身份'] if p.get('person_id') else [])+[f'目标产品参考 {j+1}' for j in range(len(p['product_ids']))]
                body={'model':p['video_model'],'prompt':self.prompt(p,shot)+'\n参考图片按顺序分别用于：'+'；'.join(image_roles),'seconds':shot['seconds'],
                      'resolution':p['resolution'],'aspect_ratio':aspect,'reference_videos':[reference_video],
                      'generate_audio':p['voice_mode']=='native'}
                if image_refs:body['reference_image_urls']=image_refs
                self.remote_job(pid,f'video-{i}','/videos',body,target);clips.append(target)
            self.update(pid,message='合并镜头与声音，导出视频',progress=95)
            voice=self.audio_path(p) if p['voice_mode']=='clone' else None
            if p['voice_mode']=='original' or self.selected_voice(p):
                assemble(clips,folder,aspect,voice=voice,resolution=p['resolution'],
                         original=self.asset(p['reference_id'])[1],shots=p['analysis']['shots'])
            else:assemble(clips,folder,aspect,voice,p['resolution'])
            with self.lock:
                run=self.get(pid)['run'];run['completed']=True
                self.update(pid,run=run,status='completed',message='复刻视频已保存',progress=100)
        except Exception as e:self.update(pid,status='failed',error=clean_error(e),message='生成暂未完成，已完成的镜头会保留')

    def routes(self):
        router=APIRouter(prefix='/api/remixes')
        @router.get('/config')
        def config():
            return {'configured':self.hub.configured(),'models':self.hub.models(),'provider':'HypiHub','max_duration':180,
                    'uploader':self.hub.upload_status()}
        @router.post('/connect')
        def connect(values:Connection,request:Request):
            local_settings(request)
            try:return {'configured':True,'models':self.hub.connect(values.key)}
            except Exception as e:raise HTTPException(400,clean_error(e)) from None
        @router.post('/models/refresh')
        def models(request:Request):
            local_settings(request)
            try:return {'models':self.hub.models(refresh=True)}
            except Exception as e:raise HTTPException(400,clean_error(e)) from None
        @router.post('/assets')
        async def upload(role:Literal['reference','person','product','background']=Form(...),file:UploadFile=File(...)):
            if shutil.disk_usage(self.root).free<3*1024**3:raise HTTPException(400,'本地剩余空间不足 3 GB，请先腾出空间。')
            ext=Path(file.filename or '').suffix.lower();images={'.jpg','.jpeg','.png','.webp'};videos={'.mp4','.mov','.webm','.mkv'}
            accepted=videos if role=='reference' else images if role in {'product','background'} else images|videos
            if ext not in accepted:raise HTTPException(400,'请上传 JPG / PNG / WebP 图片或 MP4 / MOV 视频。')
            aid=uuid.uuid4().hex;folder=self.assets/aid;folder.mkdir();path=folder/('source'+ext);size=0
            try:
                with path.open('wb') as out:
                    while chunk:=await file.read(1024*1024):
                        size+=len(chunk)
                        if size>(20 if ext in images else 500)*1024**2:raise HTTPException(413,'图片最大 20 MB，视频最大 500 MB。')
                        out.write(chunk)
                info=probe(path);stream=next(s for s in info['streams'] if s['codec_type']=='video')
                duration=float(info.get('format',{}).get('duration',0))
                if ext in videos and not .5<=duration<=180:raise HTTPException(400,'视频请控制在 0.5 秒到 3 分钟。')
                if stream['width']*stream['height']>50000000:raise HTTPException(400,'图片尺寸过大，请先缩小。')
                ffmpeg('-i',path,'-frames:v','1','-vf','scale=1024:-2','-q:v','2',folder/'poster.jpg')
                meta={'id':aid,'role':role,'name':Path(file.filename).name,'kind':'image' if ext in images else 'video',
                      'stored_name':path.name,'duration':duration,'bytes':size,'created_at':timestamp()}
                atomic_json(folder/'meta.json',meta);return meta
            except HTTPException:raise
            except Exception:raise HTTPException(400,'无法读取此素材，请更换常见格式后重试。') from None
            finally:await file.close()
        @router.get('/assets/{aid}/{kind}')
        def content(aid:str,kind:Literal['content','poster']):
            meta,path=self.asset(aid)
            return FileResponse(path if kind=='content' else path.parent/'poster.jpg')
        @router.get('')
        def listing():
            with self.lock:return [self.public(p) for p in sorted(self.records.values(),key=lambda p:p['updated_at'],reverse=True)]
        @router.post('')
        def create(values:Draft):return self.save(values)
        @router.get('/{pid}')
        def detail(pid:str):return self.public(self.get(pid))
        @router.put('/{pid}')
        def save(pid:str,values:Draft):return self.save(values,pid)
        @router.post('/{pid}/analyze')
        def analysis(pid:str):return self.launch_analysis(pid)
        @router.post('/{pid}/screenplay')
        def screenplay(pid:str):
            from remix_imitation import launch_screenplay
            return launch_screenplay(self,pid)
        @router.post('/{pid}/storyboard/approve')
        def approve_storyboard(pid:str,request:Request):
            local_settings(request)
            from remix_imitation import approve_storyboard
            return approve_storyboard(self,pid)
        @router.get('/{pid}/storyboard/{index}')
        def storyboard(pid:str,index:int):
            p=self.get(pid)
            if p.get('edit_mode')!='imitate' or not p.get('run') or not -1<=index<len(p['screenplay']['shots']):raise HTTPException(404,'分镜尚未生成。')
            filename='master.png' if index==-1 else f'image-{index:03d}.png'
            path=self.projects/pid/p['run']['id']/filename
            if not path.is_file():raise HTTPException(404,'分镜尚未生成。')
            return FileResponse(path,media_type='image/png')
        @router.post('/{pid}/storyboard/repair-quote')
        def storyboard_repair_quote(pid:str,values:RepairNotes,request:Request):
            local_settings(request)
            from remix_storyboard import repair_quote
            return repair_quote(self,pid,values.notes)
        @router.post('/{pid}/storyboard/repair')
        def storyboard_repair(pid:str,values:Approval,request:Request):
            local_settings(request)
            from remix_storyboard import start_repair
            return start_repair(self,pid,values)
        @router.post('/{pid}/storyboard/reuse')
        def storyboard_reuse(pid:str,values:StoryboardReuse,request:Request):
            local_settings(request)
            from remix_storyboard import reuse_image
            return reuse_image(self,pid,values.source_index,values.target_index)
        @router.post('/{pid}/roles')
        def roles(pid:str):return self.launch_roles(pid)
        @router.post('/{pid}/duplicate')
        def duplicate(pid:str):return self.duplicate(pid)
        @router.post('/{pid}/voice')
        def voice(pid:str,request:Request,values:dict|None=None):
            confirmed=(values or {}).get('cloud_confirmed') is True
            if confirmed:local_settings(request)
            return self.voice_job(pid,confirmed,(values or {}).get('model_config_revision'))
        @router.get('/{pid}/voice-audio')
        def voice_audio(pid:str):
            p=self.get(pid)
            if not self.selected_voice(p):raise HTTPException(404,'配音不存在。')
            return FileResponse(self.audio_path(p),media_type='audio/wav')
        @router.post('/{pid}/quote')
        def quote(pid:str,request:Request):
            local_settings(request)
            try:return self.quote(pid)
            except HTTPException:raise
            except Exception as e:raise HTTPException(400,clean_error(e)) from None
        @router.post('/{pid}/generate')
        def generate(pid:str,values:Approval,request:Request):local_settings(request);return self.start(pid,values)
        @router.post('/{pid}/pause')
        def pause(pid:str):
            with self.lock:
                p=self.get(pid)
                if p['status']!='generating':raise HTTPException(409,'该项目当前没有在生成。')
                run=p['run'];run['stop']=True;return self.public(self.update(pid,run=run,message='当前云端镜头完成后暂停'))
        @router.post('/{pid}/resume')
        def resume(pid:str,request:Request):
            local_settings(request)
            with self.lock:
                p=self.get(pid)
                if not self.public(p)['can_resume']:raise HTTPException(409,'此项目无需继续。')
                try:self.hub.ensure_uploader()
                except RuntimeError as e:raise HTTPException(400,str(e)) from None
                run=p['run'];run['stop']=False
                self.update(pid,run=run,status='generating',message='继续已保存的云端任务',error=None)
                threading.Thread(target=self.generate_worker,args=(pid,),daemon=True).start()
                return self.public(self.get(pid))
        @router.get('/{pid}/analysis/{name}')
        def analysis_file(pid:str,name:str):
            p=self.get(pid)
            if not p.get('analysis') or not re.fullmatch(r'(shot-\d{3}\.jpg|preview\.mp4)',name):raise HTTPException(404,'预览尚未准备好。')
            return FileResponse(self.projects/pid/'analysis'/name)
        @router.get('/{pid}/result')
        def result(pid:str,download:bool=False):
            p=self.get(pid)
            if not (p.get('run') or {}).get('completed'):raise HTTPException(404,'视频尚未生成。')
            return FileResponse(self.projects/pid/p['run']['id']/'result.mp4',media_type='video/mp4',filename='复刻视频.mp4' if download else None)
        return router
