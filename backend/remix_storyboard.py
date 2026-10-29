"""Quoted, durable repairs of selected storyboard images before video approval."""
import json
import shutil
import threading
import time
import uuid
from fastapi import HTTPException
from remix_hub import estimate


def reuse_image(service,pid,source_index,target_index):
    with service.lock:
        p=service.get(pid);run=p.get('run') or {};count=len((p.get('screenplay') or {}).get('shots',[]))
        if p.get('edit_mode')!='imitate' or p['status']!='storyboard' or not run or run.get('storyboard_approved'):
            raise HTTPException(409,'仅可在分镜确认阶段复用图片。')
        if not 0<=source_index<count or not 0<=target_index<count or source_index==target_index:
            raise HTTPException(400,'请选择不同的有效镜头。')
        folder=service.projects/pid/run['id'];source=folder/f'image-{source_index:03d}.png';target=folder/f'image-{target_index:03d}.png'
        if not source.is_file() or not target.is_file():raise HTTPException(400,'请等待分镜生成后再复用。')
        tag='reuse-'+uuid.uuid4().hex;archive=folder/'revisions';archive.mkdir(exist_ok=True)
        shutil.copyfile(target,archive/(tag+'-before.png'))
        temporary=folder/(tag+'.tmp');shutil.copyfile(source,temporary);temporary.replace(target)
        receipt=folder/f'frame-{target_index}.json'
        if receipt.exists():receipt.replace(archive/(tag+'-old-upload.json'))
        run.setdefault('image_revisions',{})[str(target_index)]=tag
        run.setdefault('image_reuse',[]).append({'source':source_index,'target':target_index,'revision':tag})
        service.update(pid,run=run,repair_quote=None,message=f'镜头 {target_index+1} 已复用镜头 {source_index+1} 的分镜，无新增生成费用')
        return service.public(service.get(pid))


def repair_quote(service,pid,notes):
    with service.lock:
        p=service.get(pid);run=p.get('run') or {}
        if p.get('edit_mode')!='imitate' or p['status'] not in {'storyboard','paused'} or not run or run.get('storyboard_approved'):
            raise HTTPException(409,'请在分镜确认阶段修正图片，视频开始后不能覆盖分镜。')
        if run.get('repair') and not run['repair'].get('completed'):
            raise HTTPException(409,'上一次修正尚未完成，请先继续原任务。')
        count=len(p['screenplay']['shots']);folder=service.projects/pid/run['id']
        if not notes or len(notes)>count:raise HTTPException(400,'请选择要修正的分镜。')
        normalized={}
        for key,note in notes.items():
            if not str(key).isdigit() or not 0<=int(key)<count or not isinstance(note,str) or not 1<=len(note.strip())<=2000:
                raise HTTPException(400,'分镜编号或修正说明无效。')
            if not (folder/f'image-{int(key):03d}.png').is_file():raise HTTPException(400,'请先等待这张分镜生成。')
            normalized[str(int(key))]=note.strip()
        card=next((c for c in service.hub.models() if c['id']==p['image_model']),None)
        if not card:raise HTTPException(400,'当前图片模型不可用。')
        q={'id':uuid.uuid4().hex,'run_id':run['id'],'created':time.time(),'notes':normalized,
           'total':round(estimate(card,resolution='1k')*len(normalized),4),'model':p['image_model']}
        service.update(pid,repair_quote=q)
        return q


def start_repair(service,pid,approval):
    with service.lock:
        p=service.get(pid);q=p.get('repair_quote') or {};run=p.get('run') or {}
        if p['status'] not in {'storyboard','paused'} or run.get('storyboard_approved'):
            raise HTTPException(409,'当前阶段不能重做分镜。')
        if (not q or q.get('id')!=approval.quote_id or q.get('run_id')!=run.get('id') or
            abs(q['total']-approval.accepted_estimate)>.0001 or time.time()-q['created']>600):
            raise HTTPException(409,'修正报价已过期或不匹配，请重新查看费用。')
        run['repair']={**q,'completed':False};run['stop']=False
        run.setdefault('repair_history',[]).append(q)
        service.update(pid,run=run,repair_quote=None,status='generating',error=None,message='正在修正选定分镜')
        threading.Thread(target=service.generate_worker,args=(pid,),daemon=True).start()
        return service.public(service.get(pid))


def repair_images(service,pid,folder,upload,paused):
    p=service.get(pid);repair=(p.get('run') or {}).get('repair')
    if not repair or repair.get('completed'):return True
    archive=folder/'revisions';archive.mkdir(exist_ok=True)
    for key,note in repair['notes'].items():
        if paused():return False
        i=int(key);tag=f"repair-{i}-{repair['id']}";original=folder/f'image-{i:03d}.png'
        before=archive/(tag+'-before.png');target=archive/(tag+'.png')
        if not before.exists():shutil.copyfile(original,before)
        reference=upload(before,tag+'-source')
        shot=p['screenplay']['shots'][i]
        prompt=('局部修正这张已生成的分镜图片。最重要的修改要求：'+note+
            '\n本镜头必须呈现的当前造型：'+shot.get('look_start','')+
            '\n这是同一张分镜的修正版，保持人物身份、真实年龄、脸型、服装、画面构图、门店与其他物体。'
            '仅改变要求修正的部分；需要佩戴的物品必须实际戴在指定人物身上，不能只留在别人手里。'
            '不要字幕、标签、水印或拼贴。')
        service.remote_job(pid,tag,'/images/edits',{'model':repair['model'],'prompt':prompt,'n':1,
            'resolution':'1k','output_format':'png','aspect_ratio':p['aspect'],'reference_images':[reference]},target)
        temporary=folder/(tag+'.tmp');shutil.copyfile(target,temporary);temporary.replace(original)
        receipt=folder/f'frame-{i}.json'
        if receipt.exists():receipt.replace(archive/(tag+'-old-upload.json'))
        with service.lock:
            run=service.get(pid)['run'];run.setdefault('image_revisions',{})[str(i)]=repair['id'];service.update(pid,run=run)
    with service.lock:
        run=service.get(pid)['run'];run['repair']['completed']=True;service.update(pid,run=run)
    return True
