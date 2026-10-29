from settings import FFMPEG, FFPROBE, SOURCE, venv_python
"""Imitate a screenplay with fixed casting and setting; never submit source frames/video."""
import json
import shutil
import subprocess
import threading
import time
import uuid
from fastapi import HTTPException
from settings import ROOT, environment
from extraction_config import WHISPER_PYTHON
from extraction_service import atomic_json
from remix_media import analyze, ffmpeg, assemble, probe
from remix_hub import estimate, clean_error
from remix_screenplay import Screenplay, readiness, VISION_PYTHON, master_prompt, shot_prompt

def launch_screenplay(service,pid):
    with service.lock:
        p=service.get(pid)
        if service.public(p)['locked']:raise HTTPException(409,'请先完成当前任务，或复用为新项目。')
        if p.get('edit_mode')!='imitate':raise HTTPException(400,'请先选择模仿创作。')
        if not p.get('reference_id'):raise HTTPException(400,'请先上传原视频。')
        if not readiness()['ready']:raise HTTPException(503,'本地视频理解模型正在准备，请稍后再试。')
        folder=service.projects/pid/'analysis';folder.mkdir(exist_ok=True)
        atomic_json(folder/'screenplay-progress.json',{'message':'正在读取原片画面和台词','completed':0,'total':1})
        service.update(pid,status='scripting',message='正在读取原片画面和台词',progress=5,error=None)
        threading.Thread(target=screenplay_worker,args=(service,pid),daemon=True).start()
        return service.public(service.get(pid))

def screenplay_worker(service,pid):
    try:
        p=service.get(pid);folder=service.projects/pid/'analysis';source=service.asset(p['reference_id'])[1]
        evidence=p.get('analysis') or analyze(source,folder)
        transcript=p.get('transcript','')
        if evidence['has_audio'] and not (folder/'asr.json').is_file():
            task=folder/'task.json';atomic_json(task,{'language':'auto','keywords':''})
            service.update(pid,message='正在本机提取原片台词',progress=15)
            with service.gpu_lock:
                result=subprocess.run([str(WHISPER_PYTHON),'-u',str(SOURCE / 'worker_extract.py'),'transcribe',str(task)],
                        env=environment(),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=900,creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:raise RuntimeError('原片语音转写未完成，请检查音轨后重试。')
        if (folder/'asr.json').is_file():
            rows=json.loads((folder/'asr.json').read_text(encoding='utf-8'))['segments']
            transcript=''.join(r['text'] for r in rows)
            roles={sid:role for role in service.role_cards(p) for sid in role['segment_ids']}
            for s in evidence['shots']:
                s['utterances']=[{'text':r['text'],'source_role_id':roles.get(r.get('id'),{}).get('id',''),
                    'source_name':roles.get(r.get('id'),{}).get('name','')} for r in rows if s['start']<=(r['start']+r['end'])/2<s['end']]
                s['text']='\n'.join((r['source_name']+'：' if r['source_name'] else '')+r['text'] for r in s['utterances'])
        for s in evidence['shots']:
            s['end_frame']=f'end-{s["index"]:03d}.jpg'
            ffmpeg('-ss',f'{max(s["start"],s["end"]-.2):.3f}','-i',source,'-frames:v','1','-vf','scale=640:-2','-q:v','3',folder/s['end_frame'])
        atomic_json(folder/'screenplay-input.json',{'shots':evidence['shots'],
            'source_roles':[{'id':r['id'],'name':r['name']} for r in service.role_cards(p) if r['segment_ids']]})
        service.update(pid,analysis=evidence,transcript=transcript,message='正在理解人物、动作和故事，整理为分镜剧本',progress=40)
        with service.gpu_lock:
            result=subprocess.run([str(VISION_PYTHON),'-u',str(SOURCE / 'worker_screenplay.py'),str(folder)],
                env=environment(),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=2400,creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (folder/'screenplay-worker.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('本地剧本反推未完成，请重试；详细原因已保存到该项目的处理日志。')
        plan=Screenplay(**json.loads((folder/'screenplay.json').read_text(encoding='utf-8'))).model_dump()
        service.update(pid,screenplay=plan,screenplay_approved=False,revision=p['revision']+1,status='ready',progress=100,
                       message='剧本已整理，请设置固定角色和门店，再确认分镜',error=None)
    except Exception as e:service.update(pid,status='failed',message='剧本反推未完成',error=clean_error(e))

def validate(service,p):
    if not p.get('analysis') or not p.get('screenplay'):raise HTTPException(400,'请先反推原视频剧本。')
    plan=Screenplay(**p['screenplay']).model_dump()
    if not p.get('screenplay_approved'):raise HTTPException(400,'请先确认剧本、角色和门店设置。')
    if len(plan['characters'])>4:raise HTTPException(400,'模仿创作最多支持 4 个固定角色，请合并或删除多余角色。')
    if not any(c.get('photo_id') for c in plan['characters']):raise HTTPException(400,'请至少为一个角色上传人物照片。')
    if p.get('reuse_shots'):
        old=service.reuse_source(p)
        if not old or old.get('edit_mode')!='imitate':raise HTTPException(400,'模仿创作只能复用同模式已完成的镜头。')
        if any(old.get(k)!=p.get(k) for k in ['background_id','imitation_scene','instructions']):raise HTTPException(400,'更换场景或全片要求后请重新生成全部镜头。')
        old_plan=Screenplay(**old['screenplay']).model_dump()
        if old_plan['characters']!=plan['characters']:raise HTTPException(400,'更换人物或服装后请重新生成全部镜头。')
        for i in p['reuse_shots']:
            if i>=len(plan['shots']) or i>=len(old_plan['shots']) or plan['shots'][i]!=old_plan['shots'][i]:
                raise HTTPException(400,'要保留的镜头不能修改剧本，请取消该镜头的复用。')
    if p['voice_mode']=='original' and not p['analysis'].get('has_audio'):raise HTTPException(400,'原片没有声音，请改用视频模型生成声音。')
    service.require_audio(p)

def quote(service,pid):
    p=service.get(pid)
    if service.public(p)['locked']:raise HTTPException(409,'当前任务已开始，请复用为新项目后修改。')
    validate(service,p)
    if not service.hub.configured():raise HTTPException(400,'请先在模型配置中连接 HypiHub。')
    if service.model_settings:
        current=service.model_settings.visuals(p)
        if any(current[k]!=p[k] for k in ['image_model','video_model']):
            service.update(pid,image_model=current['image_model'],video_model=current['video_model'],revision=p['revision']+1)
            p=service.get(pid)
    cards={c['id']:c for c in service.hub.models(refresh=True)}
    image=cards.get(p['image_model'],{});video=cards.get(p['video_model'],{})
    if 'image_edits' not in image.get('endpoints',[]):raise HTTPException(400,'当前图片模型不能使用角色参考图，请在模型配置中更换。')
    from model_settings import VIDEO_COMPATIBLE
    if p['video_model'].split('/')[-1] not in VIDEO_COMPATIBLE:raise HTTPException(400,'请在模型配置中选择已适配的视频模型。')
    cost=estimate(image,resolution='1k')
    reused=p.get('reuse_shots',[])
    if len(reused)==len(p['screenplay']['shots']):raise HTTPException(400,'请至少选择一个重做镜头。')
    # A duplicate with reusable shots carries the same master, preserving continuity.
    master_cost=0 if reused else cost
    items=[dict(shot=0,label='统一角色与门店定妆图',seconds=0,image_credits=master_cost,video_credits=0,reused=bool(reused))]
    for s in p['screenplay']['shots']:
        keep=s['index'] in reused
        items.append(dict(shot=s['index']+1,seconds=s['seconds'],image_credits=0 if keep else cost,
            video_credits=0 if keep else estimate(video,seconds=s['seconds'],resolution=p['resolution'],reference_seconds=0,audio=p['voice_mode']=='native'),reused=keep))
    q={'id':uuid.uuid4().hex,'revision':p['revision'],'created':time.time(),'items':items,
       'total':round(sum(i['image_credits']+i['video_credits'] for i in items),4),
       'note':'先生成统一定妆图和分镜，检查后再继续生成视频。本次只上传选定的人物、门店、产品图片与剧本文字，不上传原视频或原片帧。金额为全部阶段的估算，实际以 HypiHub 结算为准。',
       'edit_mode':'imitate','voice_mode':p['voice_mode'],'voice_scope':'all','source_seconds':sum(s['seconds'] for s in p['screenplay']['shots']),
       'model_config_revision':(service.model_settings.read() or {}).get('revision') if service.model_settings else None}
    service.update(pid,quote=q);return q

def approve_storyboard(service,pid):
    with service.lock:
        p=service.get(pid)
        if p.get('edit_mode')!='imitate' or p['status']!='storyboard':raise HTTPException(409,'请等待所有分镜生成后再继续。')
        run=p['run'];run['storyboard_approved']=True;run['stop']=False
        service.update(pid,run=run,status='generating',message='分镜已确认，开始生成动态视频')
        threading.Thread(target=service.generate_worker,args=(pid,),daemon=True).start()
        return service.public(service.get(pid))

def generate(service,pid):
    from remix_storyboard import repair_images
    try:
        p=service.get(pid);run=p['run'];folder=service.projects/pid/run['id'];shots=p['screenplay']['shots']
        def upload(path,name,person=False):
            receipt=folder/(name+'.json')
            if not receipt.is_file():atomic_json(receipt,{'url':service.hub.upload(path,person)})
            return json.loads(receipt.read_text(encoding='utf-8'))['url']
        def paused():
            if service.get(pid)['run'].get('stop'):
                service.update(pid,status='paused',message='已暂停后续生成，已有素材已保存');return True
            return False
        if not repair_images(service,pid,folder,upload,paused):return
        refs=[];roles=[]
        for c in p['screenplay']['characters']:
            if c.get('photo_id'):
                path=service.asset(c['photo_id'])[1].parent/'poster.jpg'
                refs.append(upload(path,'cast-'+c['id'],True));roles.append(c['id']+' '+c['name']+'的脸部身份')
        for aid,label in [(p.get('background_id'),'门店背景'),*[(a,f'产品 {i+1}') for i,a in enumerate(p.get('product_ids',[]))]]:
            if aid:
                path=service.asset(aid)[1].parent/'poster.jpg';refs.append(upload(path,'reference-'+aid));roles.append(label)
        source=service.reuse_source(p) if p.get('reuse_shots') else None
        if source and not (folder/'master.png').exists():shutil.copyfile(service.projects/source['id']/source['run']['id']/'master.png',folder/'master.png')
        if paused():return
        service.update(pid,message='建立整片共用的角色和门店定妆图',progress=5)
        service.remote_job(pid,'master-0','/images/edits',{'model':p['image_model'],'prompt':master_prompt(p,'；'.join(f'图 {i+1}：{label}' for i,label in enumerate(roles))),
            'n':1,'resolution':'1k','output_format':'png','aspect_ratio':p['aspect'],'reference_images':refs},folder/'master.png')
        master=upload(folder/'master.png','master-upload')
        for s in shots:
            if paused():return
            i=s['index'];target=folder/f'image-{i:03d}.png'
            if i in p.get('reuse_shots',[]):
                previous=service.projects[source['id']]/source['run']['id']
                for name in [target.name,f'video-{i:03d}.mp4']:
                    if not (folder/name).is_file():shutil.copyfile(previous/name,folder/name)
                continue
            service.remote_job(pid,f'image-{i}','/images/edits',{'model':p['image_model'],'prompt':shot_prompt(p,s,True)+
                '\n图 1 是全片统一定妆图；后续图片依次是：'+'；'.join(roles),
                'n':1,'resolution':'1k','output_format':'png','aspect_ratio':p['aspect'],'reference_images':[master,*refs]},target)
        if paused():return
        if not service.get(pid)['run'].get('storyboard_approved'):
            service.update(pid,status='storyboard',message='分镜已生成，请检查人物、服装和门店，再继续生成视频',progress=40);return
        clips=[]
        for s in shots:
            if paused():return
            i=s['index'];target=folder/f'video-{i:03d}.mp4'
            frame=upload(folder/f'image-{i:03d}.png',f'frame-{i}')
            body={'model':p['video_model'],'prompt':shot_prompt(p,s)+'\n图 1 是当前分镜，后续依次是：'+'；'.join(roles),
                  'seconds':s['seconds'],'resolution':p['resolution'],'aspect_ratio':p['aspect'],
                  'reference_image_urls':[frame,*refs],'generate_audio':p['voice_mode']=='native'}
            service.remote_job(pid,f'video-{i}','/videos',body,target);clips.append(target)
        service.update(pid,message='合并新的镜头与声音',progress=95)
        voice=service.audio_path(p) if p['voice_mode']=='clone' else None
        if p['voice_mode']=='original':
            voice=folder/'original-audio.wav'
            ffmpeg('-i',service.asset(p['reference_id'])[1],'-vn','-c:a','pcm_s16le',voice)
        assemble(clips,folder,p['aspect'],voice,p['resolution'])
        if not any(s['codec_type']=='video' for s in probe(folder/'result.mp4')['streams']):raise RuntimeError('输出视频检查失败。')
        with service.lock:
            run=service.get(pid)['run'];run['completed']=True
            service.update(pid,run=run,status='completed',message='模仿创作视频已生成',progress=100,error=None)
    except Exception as e:
        if (service.get(pid).get('run') or {}).get('stop'):
            service.update(pid,status='paused',message='任务已暂停，已有素材已保存',error=None)
        else:service.update(pid,status='failed',message='模仿创作暂未完成，已生成的素材已保存',error=clean_error(e))
