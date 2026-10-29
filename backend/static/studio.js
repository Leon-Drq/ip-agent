'use strict';
const $=id=>document.getElementById(id);
const remoteStudio=!['localhost','127.0.0.1'].includes(location.hostname);
$('remote-logout').hidden=true;
if(remoteStudio)fetch('/_auth/status').then(r=>r.json()).then(settings=>{$('remote-logout').hidden=!settings.password_required;}).catch(()=>{});
if(remoteStudio){
  $('local-caption').textContent='连接本地电脑 · 素材本地保存';
  document.querySelector('#page-avatars .quiet-note').textContent+=' 网页显示流畅预览，生成仍使用原始素材。';
}
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=name=>`<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const state={avatars:[],voices:[],jobs:[],trashJobs:[],projectSelection:new Set(),projectBulk:false,projectMutating:false,ready:false,initialized:false,route:'home',avatarId:'',voiceId:'',selectedJob:null,snapshot:{},submitting:false,filter:'all',modal:null,deletion:null,testVoiceId:null,testJobId:null,testSubmitting:false,testError:''};
let toastTimer,refreshing=false,dataEpoch=0,resultsKey='',testResultKey='',navKey='',homeKey='',avatarsKey='',voicesKey='',projectsKey='',selectorsKey='';
const routes={home:'首页',avatars:'数字人克隆',voices:'语音克隆',extract:'视频文案提取',remix:'爆款视频复刻',create:'数字人视频',projects:'我的作品',models:'模型配置'};
const statusText={queued:'等待生成',running:'正在生成',review:'待确认分镜',completed:'已完成',failed:'未完成',cancelled:'已停止'};
const busy=job=>job&&['queued','running'].includes(job.status);
const mediaUrl=id=>remoteStudio?`/api/remote-media/uploads/${id}`:`/api/uploads/${id}/content`;
const thumb=id=>`/api/uploads/${id}/thumbnail`;
const fileUrl=(job,name,download=false)=>job.source==='remix'?(download?job.download_url:job.result_url):remoteStudio&&!download?`/api/remote-media/jobs/${job.id}/${name}`:`/api/jobs/${job.id}/files/${name}${download?'?download=true':''}`;
document.addEventListener('play',event=>{
  if(event.target.matches('video,audio'))document.querySelectorAll('video,audio').forEach(media=>{if(media!==event.target&&!media.paused)media.pause();});
},true);
const findAvatar=()=>state.avatars.find(p=>p.id===state.avatarId);
const findVoice=()=>state.voices.find(p=>p.id===state.voiceId);
const videoId=()=>state.snapshot.video_id??findAvatar()?.video_id??'';
const audioId=()=>state.snapshot.audio_id??findVoice()?.audio_id??'';
let engines={},engineKey='',activeModelSettings=null;
const activeModel=(kind,fallback='')=>activeModelSettings?.selected[kind]||fallback;
const modelRevision=()=>activeModelSettings?.revision;
const editorEmotion=new SpeechControls('editor-emotion','tts-model',()=>renderEditor());
const assetEmotion=new SpeechControls('asset-emotion','asset-tts-model',()=>syncModal());
const testEmotion=new SpeechControls('test-emotion','voice-test-model',()=>renderVoiceTest());
const modelName=(kind,id)=>engines[kind]?.find(m=>m.id===id)?.name||({'gpt_sovits':'GPT-SoVITS','latentsync_15':'LatentSync 1.5','none':'关闭修复'}[id]||id);
function expressionName(job){
  const mode=job.emotion_mode||'reference';if(mode==='reference')return '';
  const caps=engines.tts?.find(m=>m.id===job.tts_model)?.emotion;
  const label=(mode==='preset'?caps?.presets.find(p=>p.id===job.emotion_preset)?.name:caps?.modes.find(m=>m.id===mode)?.name)||'';
  return label?(' · '+label+(job.tts_model==='indextts2'?' '+Math.round((job.emotion_strength??.5)*100)+'%':'')):'';
}
function populateModels(next){
  const key=JSON.stringify([next,activeModelSettings]);if(key===engineKey)return;engineKey=key;engines=next;
  for(const [id,kind,defaultValue] of [['tts-model','tts','gpt_sovits'],['asset-tts-model','tts','indextts2'],['voice-test-model','tts','gpt_sovits'],['lipsync-model','lipsync','latentsync_15'],['asset-lipsync-model','lipsync','latentsync_15'],['restoration-model','restoration','none'],['asset-restoration-model','restoration','none']]){
    const selected=activeModel(kind,$(id).value||defaultValue);$(id).replaceChildren();
    const groups={};
    for(const m of engines[kind]||[]){if(activeModelSettings&&m.id!==selected)continue;const option=new Option(m.name+(m.available?'':m.cloud?' · 待连接':' · 不可用'),m.id);option.disabled=!m.available&&!m.cloud;
      if(kind==='tts'){const label=m.cloud?'云端模型 · 按用量付费':'本地模型';if(!groups[label]){groups[label]=document.createElement('optgroup');groups[label].label=label;$(id).append(groups[label]);}groups[label].append(option);}else $(id).add(option);}
    $(id).value=selected;displayManagedModel($(id),kind);
  }
  updateModelHints();
}
function displayManagedModel(el,kind){
  if(!activeModelSettings||!el)return;
  el.disabled=true;el.classList.add('managed-model-input');el.setAttribute('aria-hidden','true');el.tabIndex=-1;
  let summary=document.getElementById(el.id+'-summary');
  if(!summary){summary=document.createElement('div');summary.id=el.id+'-summary';summary.className='managed-model-summary';el.after(summary);}
  const name=el.selectedOptions[0]?.textContent||modelName(kind,activeModel(kind));
  summary.innerHTML=`<span>${esc(name)}</span><button type="button" data-open-models data-model-category="${kind}" aria-label="配置${esc(name)}">配置</button>`;
}
function updateModelHints(){
  editorEmotion.refresh();assetEmotion.refresh();testEmotion.refresh();
  const description=(kind,id)=>engines[kind]?.find(m=>m.id===id)?.description||'';
  $('tts-model-hint').textContent=description('tts',$('tts-model').value)+($('tts-model').value==='indextts2'?' · 按文案语言自动发音':'');
  $('language').disabled=$('tts-model').value!=='gpt_sovits'&&!engines.tts?.find(m=>m.id===$('tts-model').value)?.cloud;
  $('lipsync-model-hint').textContent=description('lipsync',$('lipsync-model').value)+($('lipsync-model').value==='musetalk_15'?' · 固定推理，不使用标准/精细档位':'');
  $('quality').disabled=$('lipsync-model').value==='musetalk_15';
  $('asset-model-hint').textContent=description('tts',$('asset-tts-model').value);
  const cloudAsset=!!engines.tts?.find(m=>m.id===$('asset-tts-model').value)?.cloud;
  if(state.modal?.kind==='voice')$('asset-description').textContent=cloudAsset?'上传 10 秒至 5 分钟清晰单人录音，建议 30–60 秒。云端会使用整段录音，保存后可反复调用。':'上传 3–10 秒清晰人声。较长录音使用前 10 秒，保存后可输入新文案试听。';
  $('asset-reference').hidden=cloudAsset;$('asset-reference').previousElementSibling.hidden=cloudAsset;
  $('reference-help').textContent=['indextts2','voxcpm2'].includes($('asset-tts-model').value)?'当前模型无需填写':'留空自动识别，请核对识别结果';
  for(const [select,wrap,range,value] of [['restoration-model','restoration-strength-wrap','restoration-strength','restoration-strength-value'],['asset-restoration-model','asset-strength-wrap','asset-restoration-strength','asset-strength-value']]){
    $(wrap).hidden=$(select).value==='none';$(value).value=Math.round(Number($(range).value)*100)+'%';
  }
}
function applyVoiceModel(){ $('tts-model').value=activeModel('tts',findVoice()?.tts_model||'gpt_sovits');editorEmotion.refresh(findVoice()||{});updateModelHints(); }
function applyAvatarModels(){const p=findAvatar();$('lipsync-model').value=activeModel('lipsync',p?.lipsync_model||'latentsync_15');$('restoration-model').value=activeModel('restoration',p?.restoration_model||'none');$('restoration-strength').value=p?.restoration_strength??.35;updateModelHints();}
const modelOptions=()=>({model_config_revision:modelRevision(),...editorEmotion.read(),video_speed:Number($('video-speed').value),tts_model:$('tts-model').value,lipsync_model:$('lipsync-model').value,restoration_model:$('restoration-model').value,restoration_strength:Number($('restoration-strength').value)});
for(const id of ['tts-model','lipsync-model','restoration-model','asset-tts-model','asset-restoration-model','restoration-strength','asset-restoration-strength'])$(id).addEventListener('input',updateModelHints);
const stamp=date=>new Date(date).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
async function api(path,options={}){
  const response=await fetch(path,options);let body;
  if(response.status===401&&remoteStudio){location.assign('/_auth/login');throw new Error('登录已过期，请重新登录。');}
  try{body=await response.json();}catch{throw new Error('本地服务没有响应，请重新打开工作室。');}
  if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'请检查名称、素材和文案是否填写完整。');
  return body;
}
const send=(path,body,method='POST')=>api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
async function uploadMedia(file,kind){
  if(!remoteStudio){const form=new FormData();form.append('kind',kind);form.append('file',file);return api('/api/uploads',{method:'POST',body:form});}
  const transfer=await send('/api/remote-uploads',{name:file.name,size:file.size,kind});
  try{
    for(let offset=0;offset<file.size;offset+=transfer.chunk_size){
      await api(`/api/remote-uploads/${transfer.id}?offset=${offset}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:file.slice(offset,offset+transfer.chunk_size)});
      $('upload-title').textContent=`正在上传素材… ${Math.min(100,Math.round((offset+transfer.chunk_size)/file.size*100))}%`;
    }
    $('upload-title').textContent='上传完成，正在检查素材…';
    return await send(`/api/remote-uploads/${transfer.id}/complete`,{});
  }catch(error){await api('/api/remote-uploads/'+transfer.id,{method:'DELETE'}).catch(()=>{});throw error;}
}
function remember(){try{localStorage.setItem('avatar-studio.selection',JSON.stringify({avatarId:state.avatarId,voiceId:state.voiceId}));}catch{}}
function route(){
  const name=location.hash.slice(1);state.route=routes[name]?name:'home';
  document.querySelectorAll('.page').forEach(el=>{el.hidden=el.id!=='page-'+state.route;if(el.hidden)el.querySelectorAll('video,audio').forEach(media=>media.pause());});
  document.querySelectorAll('[data-route]').forEach(el=>{el.classList.toggle('active',el.dataset.route===state.route);if(el.dataset.route===state.route)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  $('page-title').textContent=routes[state.route];window.dispatchEvent(new CustomEvent('studio-route',{detail:state.route}));setSidebar(false);$('page-scroll').scrollTo({top:0});
  renderLibraries();renderEditor();
}
function navigate(name){if(location.hash==='#'+name)route();else location.hash=name;}
window.addEventListener('hashchange',route);
function setSidebar(open){
  const sidebar=$('sidebar'),toggle=$('menu-toggle'),wasOpen=sidebar.classList.contains('open');
  open=!!open&&window.matchMedia('(max-width:900px)').matches;
  sidebar.classList.toggle('open',open);toggle.setAttribute('aria-expanded',String(open));
  document.querySelector('main').inert=open;
  if(open){sidebar.setAttribute('role','dialog');sidebar.setAttribute('aria-modal','true');sidebar.setAttribute('aria-label','主导航');$('sidebar-close').focus();}
  else{sidebar.removeAttribute('role');sidebar.removeAttribute('aria-modal');sidebar.removeAttribute('aria-label');if(wasOpen&&window.matchMedia('(max-width:900px)').matches)toggle.focus();}
}
$('menu-toggle').onclick=()=>setSidebar(!$('sidebar').classList.contains('open'));$('scrim').onclick=()=>setSidebar(false);
$('sidebar-close').onclick=()=>setSidebar(false);
$('sidebar').addEventListener('click',event=>{if(event.target.closest('a[href^="#"]'))setSidebar(false);});
window.matchMedia('(max-width:900px)').addEventListener('change',()=>setSidebar(false));
document.addEventListener('keydown',event=>{
  if(!$('sidebar').classList.contains('open'))return;
  if(event.key==='Escape'){event.preventDefault();setSidebar(false);}
  if(event.key==='Tab'){
    const items=[...$('sidebar').querySelectorAll('a[href],button:not(:disabled),input:not(:disabled)')].filter(el=>el.getClientRects().length),first=items[0],last=items[items.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
});
function newCreation(){$('video-speed').value=1;state.selectedJob=null;state.snapshot={};resultsKey='';$('script').value='';countText();renderSelectors();renderEditor();navigate('create');if(!window.matchMedia('(max-width:900px)').matches)$('script').focus();}
for(const id of ['new-video','home-create','projects-create','reset-editor'])$(id).onclick=newCreation;
function countText(){$('char-count').textContent=$('script').value.length+' / 8000';}
$('script').oninput=countText;$('script').onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();generate('video');}};

function renderSelectors(){
  const key=JSON.stringify([state.avatars.map(p=>[p.id,p.name]),state.voices.map(p=>[p.id,p.name]),state.avatarId,state.voiceId,!!state.snapshot.video_id,!!state.snapshot.audio_id]);
  if(key!==selectorsKey){
    selectorsKey=key;
    for(const [id,list,selected,placeholder] of [['editor-avatar',state.avatars,state.avatarId,state.snapshot.video_id?'作品中的历史形象':'请选择数字人'],['editor-voice',state.voices,state.voiceId,state.snapshot.audio_id?'作品中的历史声音':'请选择声音']]){
      $(id).replaceChildren(new Option(placeholder,''));for(const profile of list)$(id).add(new Option(profile.name,profile.id));$(id).value=selected;
    }
  }
  const aid=audioId(),player=$('editor-reference');$('selected-voice-preview').hidden=!aid;
  if(aid&&player.getAttribute('src')!==mediaUrl(aid))player.src=mediaUrl(aid);
  if(!aid&&player.hasAttribute('src')){player.removeAttribute('src');player.load();}
  $('snapshot-note').hidden=!Object.keys(state.snapshot).length;
}
$('editor-avatar').onchange=()=>{state.avatarId=$('editor-avatar').value;delete state.snapshot.video_id;const current=state.jobs.find(j=>j.id===state.selectedJob);if(!(current?.status==='completed'&&current.audio_ready&&!current.video_ready))state.selectedJob=null;if(!state.voiceId&&findAvatar()?.voice_id){state.voiceId=findAvatar().voice_id;applyVoiceModel();}applyAvatarModels();remember();renderSelectors();renderEditor();};
$('editor-voice').onchange=()=>{state.voiceId=$('editor-voice').value;for(const key of ['audio_id','reference_text','reference_language'])delete state.snapshot[key];state.selectedJob=null;applyVoiceModel();remember();renderSelectors();renderEditor();};
function useAvatar(id){state.avatarId=id;state.selectedJob=null;state.snapshot={};const profile=findAvatar();if(profile?.voice_id){state.voiceId=profile.voice_id;applyVoiceModel();}applyAvatarModels();remember();renderSelectors();navigate('create');}
function useVoice(id){state.voiceId=id;state.selectedJob=null;for(const key of ['audio_id','reference_text','reference_language'])delete state.snapshot[key];applyVoiceModel();remember();renderSelectors();navigate('create');}
function latestVoiceJob(id){return state.jobs.find(j=>j.voice_id===id&&j.audio_ready&&j.status==='completed');}
function reconcileSelections(){
  const previous=state.avatarId+':'+state.voiceId;
  if(state.avatarId&&!findAvatar())state.avatarId='';
  if(state.voiceId&&!findVoice())state.voiceId='';
  if(previous!==state.avatarId+':'+state.voiceId)remember();
  if(state.selectedJob&&!state.jobs.some(j=>j.id===state.selectedJob))state.selectedJob=null;
  if(state.testJobId&&!state.jobs.some(j=>j.id===state.testJobId))state.testJobId=latestVoiceJob(state.testVoiceId)?.id||null;
}
function openProjectDelete(ids){
  if(state.projectMutating)return;
  const jobs=ids.map(id=>state.jobs.find(j=>j.id===id));
  if(!jobs.length||jobs.some(j=>!j||busy(j))){toast('请先停止正在生成的任务，再删除作品。');return;}
  state.deletion={kind:'project',ids:jobs.map(j=>j.id),pending:false};
  $('delete-title').textContent=jobs.length===1?'删除这个作品？':`删除这 ${jobs.length} 个作品？`;
  $('delete-name').textContent=jobs.slice(0,3).map(j=>j.title).join('；')+(jobs.length>3?` 等 ${jobs.length} 个作品`:'');
  $('delete-description').textContent='作品将移入回收站，可以随时恢复。数字人、克隆声音和原始素材会保留。';
  $('delete-error').hidden=true;setDeleteBusy(false);$('delete-dialog').showModal();$('cancel-delete').focus();
}
function applyProjectChanges(jobs){
  const ids=new Set(jobs.map(j=>j.id));
  state.jobs=[...state.jobs.filter(j=>!ids.has(j.id)),...jobs.filter(j=>!j.deleted_at)].sort((a,b)=>b.created_at.localeCompare(a.created_at));
  state.trashJobs=[...state.trashJobs.filter(j=>!ids.has(j.id)),...jobs.filter(j=>j.deleted_at)].sort((a,b)=>b.created_at.localeCompare(a.created_at));
  state.projectSelection.clear();reconcileSelections();renderLibraries();renderEditor();renderVoiceTest();
}
async function restoreProjects(ids){
  if(!ids.length||state.projectMutating)return;
  state.projectMutating=true;dataEpoch++;renderLibraries();
  try{
    const result=ids.length===1?await send(`/api/projects/${ids[0]}/restore`,{}):await send('/api/projects/batch-restore',{job_ids:ids});
    applyProjectChanges(result.jobs);toast(`已恢复 ${result.jobs.length} 个作品，可在「全部」中查看。`);
  }catch(error){toast(error.message);}
  finally{dataEpoch++;state.projectMutating=false;renderLibraries();}
}
function openDelete(kind,id){
  const profile=(kind==='voice'?state.voices:state.avatars).find(p=>p.id===id);if(!profile)return;
  state.deletion={kind,id,revision:profile.revision,pending:false};
  $('delete-title').textContent=kind==='voice'?'删除这个声音？':'删除这个数字人？';
  $('delete-name').textContent=profile.name;
  const linked=kind==='voice'?state.avatars.filter(p=>p.voice_id===id).length:0;
  $('delete-description').textContent=(kind==='voice'?'将从声音库移除。':'将从数字人库移除，独立保存的声音会保留。')+'已有作品、正在生成的任务和原始素材会保留。'+(linked?`同时解除 ${linked} 个数字人的默认声音关联。`:'');
  $('delete-error').hidden=true;setDeleteBusy(false);$('delete-dialog').showModal();$('cancel-delete').focus();
}
function setDeleteBusy(value){
  for(const id of ['close-delete','cancel-delete','confirm-delete'])$(id).disabled=value;
  $('confirm-delete').textContent=value?'正在删除…':'确认删除';
  $('delete-dialog').setAttribute('aria-busy',String(value));
}
function closeDelete(){if(state.deletion?.pending)return;$('delete-dialog').close();state.deletion=null;}
async function confirmDelete(){
  const target=state.deletion;if(!target||target.pending)return;
  target.pending=true;setDeleteBusy(true);$('delete-error').hidden=true;dataEpoch++;
  try{
    if(target.kind==='project'){
      state.projectMutating=true;
      const result=target.ids.length===1?await api(`/api/projects/${target.ids[0]}`,{method:'DELETE'}):await send('/api/projects/batch-delete',{job_ids:target.ids});
      applyProjectChanges(result.jobs);target.pending=false;closeDelete();
      toast(`已将 ${result.jobs.length} 个作品移入回收站，可随时恢复。`);$('project-bulk-toggle').focus();return;
    }
    const result=await send(`/api/${target.kind==='voice'?'voices':'avatars'}/${target.id}`,{revision:target.revision},'DELETE');
    dataEpoch++;
    if(target.kind==='voice'){
      state.voices=state.voices.filter(p=>p.id!==target.id);
      for(const changed of result.avatars||[]){const i=state.avatars.findIndex(p=>p.id===changed.id);if(i>=0)state.avatars[i]=changed;}
    }else state.avatars=state.avatars.filter(p=>p.id!==target.id);
    reconcileSelections();renderLibraries();renderEditor();
    target.pending=false;closeDelete();
    toast(target.kind==='voice'?'声音已从声音库删除，已有作品保留。':'数字人已从形象库删除，已有作品保留。');
    $(target.kind==='voice'?'voice-search':'avatar-search').focus();
  }catch(error){$('delete-error').textContent=error.message;$('delete-error').hidden=false;}
  finally{dataEpoch++;target.pending=false;state.projectMutating=false;setDeleteBusy(false);renderLibraries();}
}
$('cancel-delete').onclick=closeDelete;$('close-delete').onclick=closeDelete;$('confirm-delete').onclick=confirmDelete;
$('delete-dialog').addEventListener('cancel',event=>{event.preventDefault();closeDelete();});
function projectCard(job,selectable=false){
  const posterURL=job.poster_url||(job.video_id?thumb(job.video_id):'');
  const poster=posterURL?`<img src="${esc(posterURL)}" class="${job.source==='remix'?'remix-cover':''}" alt="" loading="lazy">`:'';
  const details=job.source==='remix'?esc(job.description):`${esc(modelName('tts',job.tts_model||'gpt_sovits'))}${esc(expressionName(job))}${esc(speedLabel(job))}`;
  const deleted=!!job.deleted_at,selected=selectable&&state.projectSelection.has(job.id);
  const checkbox=selectable&&state.projectBulk?`<label class="project-select"><input type="checkbox" data-select-job="${job.id}" aria-label="选择作品：${esc(job.title)}" ${selected?'checked':''} ${busy(job)||state.projectMutating?'disabled':''}>选择</label>`:'';
  const links=deleted?`<button class="link-button" data-action="restore-job" data-id="${job.id}" ${state.projectMutating?'disabled':''} aria-label="恢复作品：${esc(job.title)}">恢复作品</button>`:`${job.video_ready?`<a href="${fileUrl(job,'result.mp4',true)}">下载视频</a>`:''}${job.audio_ready?`<a href="${fileUrl(job,'voice.wav',true)}">下载配音</a>`:''}<button class="link-button" data-action="open-job" data-id="${job.id}">${busy(job)?'查看进度':'打开作品'}</button><button class="link-button project-delete" data-action="delete-job" data-id="${job.id}" aria-label="删除作品：${esc(job.title)}" title="${busy(job)?'请先停止生成':'移入回收站'}" ${busy(job)||state.projectMutating?'disabled':''}>删除</button>`;
  return `<article class="project-card${selected?' is-selected':''}">${checkbox}<button class="project-cover" data-action="open-job" data-id="${job.id}" aria-label="${deleted?'已删除作品':'打开作品'}：${esc(job.title)}" ${deleted?'disabled':''}>${poster}<span class="cover-icon">${icon(job.mode==='audio'?'mic':busy(job)?'video':'play')}</span><span class="tag">${deleted?'已删除':esc(statusText[job.status]||job.status)}</span></button><div class="project-card-body"><button class="project-name" data-action="open-job" data-id="${job.id}" title="${esc(job.text)}" ${deleted?'disabled':''}>${esc(job.title)}</button><p class="project-meta">${job.mode==='audio'?'配音':'视频'} · ${esc(stamp(job.created_at))}<br>${details}</p><div class="project-links">${links}</div></div></article>`;
}
function visibleProjects(){
  const search=$('project-search').value.trim().toLowerCase();
  const matchesCategory=j=>{
    if(state.filter==='avatar-video')return j.mode==='video'&&j.source!=='remix';
    if(state.filter==='remix-video')return j.mode==='video'&&j.source==='remix';
    if(state.filter==='active')return busy(j)||j.status==='review';
    return ['all','trash'].includes(state.filter)||j.mode===state.filter;
  };
  return (state.filter==='trash'?state.trashJobs:state.jobs).filter(j=>matchesCategory(j)&&(j.text+' '+j.title).toLowerCase().includes(search));
}
function renderProjects(){
  const jobs=visibleProjects(),eligible=jobs.filter(j=>!busy(j)),ids=new Set(eligible.map(j=>j.id));
  for(const id of state.projectSelection)if(!ids.has(id))state.projectSelection.delete(id);
  const count=state.projectSelection.size;
  $('project-trash-count').textContent=state.trashJobs.length;$('project-trash-note').hidden=state.filter!=='trash';
  $('project-bulk-toggle').textContent=state.projectBulk?'完成管理':'批量管理';$('project-bulk-toggle').setAttribute('aria-pressed',String(state.projectBulk));$('project-bulk-toggle').disabled=state.projectMutating;
  $('project-bulk-bar').hidden=!state.projectBulk;$('project-selection-count').textContent=`已选 ${count} 项`;
  $('project-select-all').checked=count>0&&count===eligible.length;$('project-select-all').indeterminate=count>0&&count<eligible.length;$('project-select-all').disabled=state.projectMutating||!eligible.length;
  $('project-delete-selected').hidden=state.filter==='trash';$('project-restore-selected').hidden=state.filter!=='trash';
  for(const id of ['project-delete-selected','project-restore-selected'])$(id).disabled=!count||state.projectMutating;
  const pk=JSON.stringify([jobs.map(j=>[j.id,j.status,j.audio_ready,j.video_ready,j.title,j.deleted_at,j.video_speed]),state.filter,state.projectBulk,[...state.projectSelection],state.projectMutating]);
  if(pk===projectsKey)return;projectsKey=pk;
  $('project-library').innerHTML=jobs.map(j=>projectCard(j,true)).join('')||`<div class="empty-state">${icon('folder')}${state.filter==='trash'?'回收站中没有匹配的作品。':state.filter==='active'?'当前没有正在进行的任务。':'这里还没有匹配的作品。'}${state.filter==='trash'?'':'<br><a href="#create">开始新的创作 →</a>'}</div>`;
}
function renderLibraries(){
  $('avatar-count').textContent=state.avatars.length;$('avatar-total').textContent=state.avatars.length;$('voice-count').textContent=state.voices.length;$('voice-total').textContent=state.voices.length;
  const avatarSearch=$('avatar-search').value.trim().toLowerCase();
  const ak=JSON.stringify([state.avatars.map(p=>[p.id,p.revision]),avatarSearch]);
  if(ak!==avatarsKey){
    avatarsKey=ak;const profiles=state.avatars.filter(p=>p.name.toLowerCase().includes(avatarSearch));
    $('avatar-library').innerHTML=profiles.map(p=>`<article class="avatar-card"><div class="avatar-picture"><video src="${mediaUrl(p.video_id)}" poster="${thumb(p.video_id)}" controls muted preload="none" aria-label="预览形象：${esc(p.name)}"></video></div><div class="avatar-card-body"><h3 title="${esc(p.name)}">${esc(p.name)}</h3><p>可重复使用 · ${esc(stamp(p.created_at))}</p><div class="card-actions"><button class="button dark" data-action="use-avatar" data-id="${p.id}">用于创作</button><button class="button" data-action="edit-avatar" data-id="${p.id}">编辑</button><button class="button danger" data-action="delete-avatar" data-id="${p.id}" aria-label="删除数字人：${esc(p.name)}">删除</button></div></div></article>`).join('')+(avatarSearch&&!profiles.length?'<div class="empty-state">没有找到这个数字人，试试其他名称。</div>':`<button class="add-tile" data-action="add-avatar"><span>${icon('plus')}</span><strong>创建数字人</strong><small>上传一段你的视频</small></button>`);
  }
  const voiceSearch=$('voice-search').value.trim().toLowerCase();
  const vk=JSON.stringify([state.voices.map(p=>[p.id,p.revision,latestVoiceJob(p.id)?.id]),voiceSearch]);
  if(vk!==voicesKey){
    voicesKey=vk;const profiles=state.voices.filter(p=>p.name.toLowerCase().includes(voiceSearch));
    $('voice-library').innerHTML=profiles.length?profiles.map(p=>{const sample=latestVoiceJob(p.id);return `<article class="voice-card"><div class="voice-card-heading"><div class="voice-symbol">${icon('mic')}</div><div><h3 title="${esc(p.name)}">${esc(p.name)}</h3><small>${esc(modelName('tts',p.tts_model||'gpt_sovits'))} · ${p.language==='en'?'英文':'中文'}配音</small></div></div><audio controls preload="none" src="${sample?fileUrl(sample,'voice.wav'):mediaUrl(p.audio_id)}" aria-label="${sample?'克隆声音':'参考录音'}：${esc(p.name)}"></audio><p class="audio-label">${sample?'克隆声音试听 · '+esc(modelName('tts',sample.tts_model||'gpt_sovits'))+' · '+esc(sample.title):'参考录音 · 点击下方试听新文案'}</p><div class="card-actions"><button class="button" data-action="test-voice" data-id="${p.id}">${icon('play')}试听</button><button class="button dark" data-action="use-voice" data-id="${p.id}">用于创作</button><button class="button" data-action="edit-voice" data-id="${p.id}">编辑</button><button class="button danger" data-action="delete-voice" data-id="${p.id}" aria-label="删除声音：${esc(p.name)}">删除</button></div></article>`;}).join(''):`<div class="empty-state">${icon('mic')}${voiceSearch?'没有找到这个声音，试试其他名称。':'还没有克隆声音。上传一段清晰录音，创建你的第一个声音。'}${voiceSearch?'':'<br><button data-action="add-voice">＋ 克隆新声音</button>'}</div>`;
  }
  const jobsKey=JSON.stringify(state.jobs.map(j=>[j.id,j.status,j.audio_ready,j.video_ready,j.title,j.video_speed]));
  if(jobsKey!==navKey){navKey=jobsKey;$('recent-nav').innerHTML=state.jobs.slice(0,5).map(j=>`<button class="recent-item" data-action="open-job" data-id="${j.id}"><strong>${esc(j.title)}</strong><small>${esc(statusText[j.status])} · ${esc(stamp(j.created_at))}</small></button>`).join('')||'<div class="empty-state">你的作品会出现在这里</div>';}
  const hk=JSON.stringify([jobsKey,state.avatars.map(p=>[p.id,p.revision]),state.voices.map(v=>v.name)]);
  if(hk!==homeKey){homeKey=hk;$('home-projects').innerHTML=state.jobs.slice(0,3).map(j=>projectCard(j)).join('')||`<div class="empty-state">${icon('video')}还没有作品，先准备好形象和声音，再开始创作。<br><a href="#create">创作我的第一条视频 →</a></div>`;const first=state.avatars[0];$('home-avatar-art').innerHTML=first?`<img src="${thumb(first.video_id)}" alt="${esc(first.name)}">`:icon('person');$('home-avatar-status').textContent=first?`${state.avatars.length} 个形象`:'待创建';$('home-avatar-status').classList.toggle('ready',!!first);$('home-voice-status').textContent=state.voices.length?`${state.voices.length} 个声音`:'待创建';$('home-voice-status').classList.toggle('ready',!!state.voices.length);$('home-voice-caption').textContent=state.voices[0]?.name||'一段录音，留下你的声音';}
  renderProjects();
}
for(const id of ['avatar-search','voice-search'])$(id).oninput=renderLibraries;
$('project-search').oninput=()=>{state.projectSelection.clear();renderProjects();};
$('project-filters').onclick=event=>{const button=event.target.closest('[data-filter]');if(!button)return;state.filter=button.dataset.filter;state.projectSelection.clear();document.querySelectorAll('[data-filter]').forEach(el=>{el.classList.toggle('active',el===button);el.setAttribute('aria-pressed',String(el===button));});renderProjects();};
$('project-bulk-toggle').onclick=()=>{state.projectBulk=!state.projectBulk;state.projectSelection.clear();renderProjects();};
$('project-select-all').onchange=event=>{state.projectSelection=new Set(event.target.checked?visibleProjects().filter(j=>!busy(j)).map(j=>j.id):[]);renderProjects();};
$('project-library').addEventListener('change',event=>{
  const id=event.target.dataset.selectJob;if(!id)return;
  if(event.target.checked)state.projectSelection.add(id);else state.projectSelection.delete(id);
  renderProjects();document.querySelector(`[data-select-job="${id}"]`)?.focus();
});
$('project-delete-selected').onclick=()=>openProjectDelete([...state.projectSelection]);
$('project-restore-selected').onclick=()=>restoreProjects([...state.projectSelection]);

const formatSpeed=value=>Number(value??1).toFixed(2).replace(/0$/,'');
const speedLabel=job=>job.mode==='video'?' · 视频 '+formatSpeed(job.video_speed??1)+' 倍速':'';
function updateVideoSpeed(){
  const factor=Number($('video-speed').value),label=formatSpeed(factor)+' 倍',job=state.jobs.find(j=>j.id===state.selectedJob);
  $('video-speed-value').value=label;$('video-speed').setAttribute('aria-valuetext',label);
  $('video-speed').disabled=state.submitting||!!busy(job);$('reset-video-speed').disabled=$('video-speed').disabled;
  const different=job?.video_ready&&Math.abs(factor-(job.video_speed??1))>.001;
  $('video-speed-hint').textContent=different?`当前成片为 ${formatSpeed(job.video_speed??1)} 倍速，新生成的视频将使用 ${label}。`:'输出视频的画面和声音同步变速，配音试听保持原速。';
  const renderButton=$('result').querySelector('[data-action="render-job"]');
  if(renderButton)renderButton.innerHTML=icon('video')+(Math.abs(factor-1)>.001?`生成 ${label}速视频`:'用这份配音生成视频');
}
$('video-speed').oninput=updateVideoSpeed;
$('reset-video-speed').onclick=()=>{$('video-speed').value=1;updateVideoSpeed();};
function renderEditor(){
  renderSelectors();editorEmotion.lock(state.submitting);$('generate-video').disabled=state.submitting||editorEmotion.uploading;$('generate-audio').disabled=state.submitting||editorEmotion.uploading;
  const job=state.jobs.find(j=>j.id===state.selectedJob),source=videoId();
  const key=job?[job.id,job.status,job.audio_ready,job.video_ready].join(':'):'preview:'+source;
  if(key!==resultsKey){
    resultsKey=key;$('preview-title').textContent=job?(job.video_ready?'你的新视频':job.status==='completed'?'配音已生成':'生成进度'):'形象预览';
    if(!job){$('result').innerHTML=source?`<video class="preview-video" src="${mediaUrl(source)}" poster="${thumb(source)}" controls muted preload="none"></video><p class="preview-caption">这是原始形象预览。生成后，会根据新配音同步口型。${remoteStudio?'<br>网页显示流畅预览，生成仍使用原始素材。':''}</p>`:`<div class="preview-empty">${icon('person')}<p>选择一个数字人形象</p><a href="#avatars">去创建我的数字人 →</a></div>`;}
    else if(busy(job)){$('result').innerHTML=`<div class="progress-box"><div class="progress-top"><span id="progress-message"></span><span id="progress-percent"></span></div><div class="progress-track" role="progressbar" aria-label="生成进度" aria-valuemin="0" aria-valuemax="100" id="progress-bar"><div class="progress-fill" id="progress-fill"></div></div><div class="progress-stages"><span>准备声音</span><span>生成配音</span>${job.mode==='video'?'<span>同步口型</span><span>导出视频</span>':''}</div><button class="button" data-action="cancel-job" data-id="${job.id}">停止生成</button></div>`;}
    else if(job.status==='completed'){$('result').innerHTML=(job.video_ready?`<video class="preview-video" id="result-video" src="${fileUrl(job,'result.mp4')}" controls preload="none"></video>`:`<audio class="output-audio" controls preload="none" src="${fileUrl(job,'voice.wav')}"></audio>`)+`<div class="result-actions">${job.video_ready?`<a class="button dark" href="${fileUrl(job,'result.mp4',true)}">${icon('download')}下载视频</a>`:`<button class="button dark" data-action="render-job" data-id="${job.id}">${icon('video')}用这份配音生成视频</button>`}<a class="button" href="${fileUrl(job,'voice.wav',true)}">${icon('download')}${job.video_ready&&Math.abs((job.video_speed??1)-1)>.001?'下载原速配音':'下载配音'}</a></div><p class="preview-caption">${esc(modelName('tts',job.tts_model||'gpt_sovits'))}${esc(expressionName(job))}${esc(speedLabel(job))}${job.video_ready?' · '+esc(modelName('lipsync',job.lipsync_model||'latentsync_15')):''}${job.restoration_model&&job.restoration_model!=='none'&&job.video_ready?' · '+esc(modelName('restoration',job.restoration_model))+' '+Math.round((job.restoration_strength??.35)*100)+'%':''}${remoteStudio?'<br>在线播放为流畅预览，下载保留原始质量。':''}<br>已保存到我的作品 · ${esc(stamp(job.created_at))}${job.video_ready&&job.restoration_model&&job.restoration_model!=='none'?` · <a href="${fileUrl(job,'unrestored.mp4',true)}">下载修复前视频</a>`:''}</p>`;}
    else{$('result').innerHTML=`<div class="error-panel"><h3>${job.status==='cancelled'?'生成已停止':'这次生成没有完成'}</h3><p>${esc(job.error||'素材和文案已保留，可以重新生成。')}</p><div class="result-actions"><button class="button" data-action="retry-job" data-id="${job.id}">重新生成</button>${job.audio_ready?`<a class="button" href="${fileUrl(job,'voice.wav',true)}">下载已完成配音</a>`:''}</div></div>`;}
  }
  updateVideoSpeed();
  if(busy(job)&&$('progress-message')){$('progress-message').textContent=job.message;$('progress-percent').textContent=Math.round(job.progress)+'%';$('progress-fill').style.width=job.progress+'%';$('progress-bar').setAttribute('aria-valuenow',job.progress);}
}
function openJob(id){
  const job=state.jobs.find(j=>j.id===id);if(!job)return;
  if(job.source==='remix'){window.dispatchEvent(new CustomEvent('studio-open-remix',{detail:job.project_id}));return;}
  state.selectedJob=id;state.avatarId=state.avatars.some(p=>p.id===job.avatar_id)?job.avatar_id:'';state.voiceId=state.voices.some(p=>p.id===job.voice_id)?job.voice_id:'';
  state.snapshot={video_id:job.video_id,audio_id:job.audio_id,reference_text:job.reference_text,reference_language:job.reference_language};
  $('video-speed').value=job.video_speed??1;
  $('script').value=job.text;$('language').value=job.language;$('quality').value=job.quality;countText();remember();navigate('create');
  $('tts-model').value=activeModel('tts',job.tts_model||'gpt_sovits');editorEmotion.refresh(job);$('lipsync-model').value=activeModel('lipsync',job.lipsync_model||'latentsync_15');$('restoration-model').value=activeModel('restoration',job.restoration_model||'none');$('restoration-strength').value=job.restoration_strength??.35;updateModelHints();
}
async function generate(mode){
  if(state.submitting||!editorEmotion.valid())return;const text=$('script').value.trim();
  if(!text){toast('先写下你想说的话。');$('script').focus();return;}
  if(!audioId()){toast('请选择一个克隆声音，或先到语音克隆页创建声音。');$('editor-voice').focus();return;}
  if(mode==='video'&&!videoId()){toast('请选择一个数字人形象。');$('editor-avatar').focus();return;}
  if(!state.ready){toast('请等右上角显示“本地模型就绪”后再生成。');return;}
  state.submitting=true;renderEditor();
  try{const body={text,mode,avatar_id:state.avatarId,voice_id:state.voiceId,language:$('language').value,quality:$('quality').value,...state.snapshot,...modelOptions()};const cloud_confirmed=await cloudSpeechUI.authorize(body.tts_model,audioId(),text);const job=await send('/api/jobs',{...body,cloud_confirmed});dataEpoch++;state.jobs=[job,...state.jobs];state.selectedJob=job.id;renderLibraries();renderEditor();}
  catch(error){toast(error.message);}finally{state.submitting=false;renderEditor();}
}
$('generate-video').onclick=()=>generate('video');$('generate-audio').onclick=()=>generate('audio');
async function cancelJob(id){try{const job=await send(`/api/jobs/${id}/cancel`,{});replaceJob(job);}catch(error){toast(error.message);}}
function replaceJob(job){dataEpoch++;state.jobs=[job,...state.jobs.filter(j=>j.id!==job.id)].sort((a,b)=>b.created_at.localeCompare(a.created_at));renderLibraries();renderEditor();renderVoiceTest();}
async function renderAudio(id){
  if(!videoId()){toast('请先选择数字人形象，再使用这份配音生成视频。');$('editor-avatar').focus();return;}
  try{const {tts_model,...options}=modelOptions();const job=await send(`/api/jobs/${id}/render`,{avatar_id:state.avatarId,video_id:videoId(),quality:$('quality').value,...options});replaceJob(job);}catch(error){toast(error.message);}
}

async function openAsset(kind,id=null){
  const profile=(kind==='avatar'?state.avatars:state.voices).find(p=>p.id===id);
  state.modal={kind,profile,assetId:profile?.[kind==='avatar'?'video_id':'audio_id']||'',uploading:false,saving:false,token:Symbol()};
  const modal=state.modal;
  $('asset-title').textContent=profile?(kind==='avatar'?'编辑数字人':'编辑克隆声音'):(kind==='avatar'?'创建数字人':'克隆新声音');
  $('asset-eyebrow').textContent=kind==='avatar'?'你的形象，反复使用':'你的声音，自由表达';
  $('asset-description').textContent=kind==='avatar'?'上传正面、单人、嘴部清晰的视频。保留原有背景与动作，用新配音驱动口型。':'上传 3–10 秒清晰人声。较长录音使用前 10 秒，保存后可输入新文案试听。';
  $('asset-name-label').textContent=kind==='avatar'?'数字人名称':'声音名称';$('asset-name').value=profile?.name||'';
  $('asset-file').accept=kind==='avatar'?'video/mp4,video/quicktime,video/webm,.mkv,.avi':'audio/*,video/mp4';$('asset-file').value='';
  $('avatar-options').hidden=kind!=='avatar';$('voice-options').hidden=kind!=='voice';$('save-test-voice').hidden=kind!=='voice';
  $('save-asset').textContent=kind==='avatar'?'保存数字人':'保存声音';$('save-asset').classList.toggle('dark',kind==='avatar');
  $('copy-asset').hidden=!profile;
  $('asset-reference').value=profile?.reference_text||'';$('asset-reference-language').value=profile?.reference_language||'auto';$('asset-language').value=profile?.language||'zh';
  $('asset-tts-model').value=activeModel('tts',profile?.tts_model||'indextts2');assetEmotion.refresh(profile||{});
  $('asset-lipsync-model').value=activeModel('lipsync',profile?.lipsync_model||'latentsync_15');$('asset-restoration-model').value=activeModel('restoration',profile?.restoration_model||'none');$('asset-restoration-strength').value=profile?.restoration_strength??.35;updateModelHints();
  $('asset-default-voice').replaceChildren(new Option('暂不设置',''));for(const voice of state.voices)$('asset-default-voice').add(new Option(voice.name,voice.id));$('asset-default-voice').value=profile?.voice_id||'';
  $('asset-error').hidden=true;resetModalMedia();syncModal();$('asset-dialog').showModal();$('asset-name').focus();
  if(modal.assetId){modal.uploading=true;syncModal();try{const meta=await api('/api/uploads/'+modal.assetId);if(state.modal===modal)showModalMedia(meta);}catch(error){if(state.modal===modal){modal.assetId='';assetError(error.message);}}finally{if(state.modal===modal){modal.uploading=false;syncModal();}}}
}
function resetModalMedia(){for(const id of ['asset-video','asset-audio']){$(id).hidden=true;$(id).removeAttribute('src');$(id).load();}$('upload-title').textContent=state.modal?.kind==='avatar'?'选择人物视频':'选择参考录音';$('upload-hint').textContent='点击选择，或拖入文件 · 最大 1 GB';}
function showModalMedia(meta){resetModalMedia();const player=$(state.modal.kind==='avatar'?'asset-video':'asset-audio');player.src=mediaUrl(meta.id);player.hidden=false;$('upload-title').textContent=meta.name;$('upload-hint').textContent=`${meta.duration.toFixed(1)} 秒 · 点击更换${state.modal.kind==='voice'&&meta.duration>10?(engines.tts?.find(m=>m.id===$('asset-tts-model').value)?.cloud?' · 使用整段录音':' · 使用前 10 秒'):''}`;}
function assetError(message){$('asset-error').textContent=message;$('asset-error').hidden=false;}
function syncModal(){const locked=state.modal?.uploading||state.modal?.saving||assetEmotion.uploading;assetEmotion.lock(locked);for(const id of ['choose-file','save-asset','save-test-voice','copy-asset','cancel-asset','close-asset'])$(id).disabled=!!locked;}
$('copy-asset').onclick=()=>{if(!state.modal||state.modal.uploading||state.modal.saving)return;state.modal.profile=null;$('asset-name').value=$('asset-name').value.slice(0,36)+' 副本';$('asset-title').textContent=state.modal.kind==='avatar'?'另存数字人':'另存声音';$('copy-asset').hidden=true;$('asset-name').focus();};
async function uploadAsset(file){
  const modal=state.modal;if(!modal||modal.uploading||modal.saving||!file)return;
  if(file.size>1024**3){assetError('文件请控制在 1 GB 以内。');return;}
  modal.uploading=true;syncModal();$('asset-error').hidden=true;$('upload-title').textContent='正在导入并检查素材…';
  try{const meta=await uploadMedia(file,modal.kind==='avatar'?'video':'audio');if(state.modal!==modal)return;modal.assetId=meta.id;if(modal.kind==='voice'){$('asset-reference').value='';$('asset-reference-language').value='auto';}showModalMedia(meta);if(!$('asset-name').value.trim())$('asset-name').value=file.name.replace(/\.[^.]+$/,'').slice(0,40);}
  catch(error){if(state.modal===modal){assetError(error.message);$('upload-title').textContent='导入未完成，点击重试';}}
  finally{if(state.modal===modal){modal.uploading=false;$('asset-file').value='';syncModal();}}
}
$('choose-file').onclick=()=>$('asset-file').click();$('asset-file').onchange=event=>uploadAsset(event.target.files[0]);
$('upload-area').ondragover=event=>{event.preventDefault();$('upload-area').classList.add('dragover');};$('upload-area').ondragleave=()=>$('upload-area').classList.remove('dragover');$('upload-area').ondrop=event=>{event.preventDefault();$('upload-area').classList.remove('dragover');uploadAsset(event.dataTransfer.files[0]);};
function closeAsset(){if(state.modal?.uploading||state.modal?.saving||assetEmotion.uploading)return;$('asset-dialog').close();state.modal=null;resetModalMedia();}
$('close-asset').onclick=closeAsset;$('cancel-asset').onclick=closeAsset;$('asset-dialog').addEventListener('cancel',event=>{event.preventDefault();closeAsset();});
async function saveAsset(test=false){
  const modal=state.modal;if(!modal||modal.uploading||modal.saving)return;
  if(!$('asset-form').reportValidity()||(modal.kind==='voice'&&!assetEmotion.valid()))return;
  if(!modal.assetId){assetError(modal.kind==='avatar'?'请先上传人物视频。':'请先上传参考录音。');return;}
  const name=$('asset-name').value.trim();if(!name){assetError('请填写一个名称。');return;}
  const body=modal.kind==='avatar'?{name,video_id:modal.assetId,voice_id:$('asset-default-voice').value,lipsync_model:$('asset-lipsync-model').value,restoration_model:$('asset-restoration-model').value,restoration_strength:Number($('asset-restoration-strength').value)}:{name,audio_id:modal.assetId,reference_text:$('asset-reference').value.trim(),reference_language:$('asset-reference-language').value,language:$('asset-language').value,tts_model:$('asset-tts-model').value,...assetEmotion.read()};
  if(modal.profile)body.revision=modal.profile.revision;
  modal.saving=true;syncModal();$('asset-error').hidden=true;
  try{
    if(modal.kind==='voice')await cloudSpeechUI.prepare(body.tts_model,body.audio_id);
    const collection=modal.kind==='avatar'?'avatars':'voices',profile=await send('/api/'+collection+(modal.profile?'/'+modal.profile.id:''),body,modal.profile?'PUT':'POST');dataEpoch++;
    state[collection]=[profile,...state[collection].filter(p=>p.id!==profile.id)];
    if(modal.kind==='avatar'){state.avatarId=profile.id;delete state.snapshot.video_id;if(!state.voiceId&&profile.voice_id)state.voiceId=profile.voice_id;}
    else{state.voiceId=profile.id;for(const key of ['audio_id','reference_text','reference_language'])delete state.snapshot[key];}
    state.selectedJob=null;remember();modal.saving=false;closeAsset();renderLibraries();renderSelectors();renderEditor();toast(`“${profile.name}”已保存，可以随时选用。`);
    if(modal.kind==='avatar')applyAvatarModels();else applyVoiceModel();
    window.dispatchEvent(new CustomEvent('studio-asset-saved',{detail:{kind:modal.kind,profile}}));
    if(test){openVoiceTest(profile.id);await generateVoiceTest();}
  }catch(error){modal.saving=false;assetError(error.message);syncModal();}
}
$('asset-form').onsubmit=event=>{event.preventDefault();saveAsset();};$('save-test-voice').onclick=()=>saveAsset(true);
for(const id of ['add-avatar','editor-add-avatar'])$(id).onclick=()=>openAsset('avatar');for(const id of ['add-voice','editor-add-voice'])$(id).onclick=()=>openAsset('voice');

function openVoiceTest(id){
  const voice=state.voices.find(p=>p.id===id);if(!voice)return;state.testVoiceId=id;state.testJobId=latestVoiceJob(id)?.id||null;state.testError='';testResultKey='';
  $('voice-test-name').textContent=voice.name+' · 输入新文案，试听克隆后的声音。';$('voice-test-language').value=voice.language;
  $('voice-test-model').value=activeModel('tts',voice.tts_model||'gpt_sovits');testEmotion.refresh(voice);
  $('voice-test-text').value=voice.language==='en'?'Hello, this is my cloned voice. I can use it again with a new script.':'你好，这是我的克隆声音。换一段新文案，就能继续使用。';
  renderVoiceTest();$('voice-dialog').showModal();
}
$('close-voice-test').onclick=()=>$('voice-dialog').close();$('voice-use').onclick=()=>{$('voice-dialog').close();useVoice(state.testVoiceId);};
async function generateVoiceTest(){
  const previous=state.jobs.find(j=>j.id===state.testJobId);if(state.testSubmitting||busy(previous)||!testEmotion.valid())return;
  const text=$('voice-test-text').value.trim();if(!text){state.testError='请先填写试听文案。';renderVoiceTest();$('voice-test-text').focus();return;}
  if(!state.ready){state.testError='模型还没有就绪，请稍后再试。';renderVoiceTest();return;}
  state.testError='';state.testSubmitting=true;renderVoiceTest();
  try{const revision=modelRevision(),model=$('voice-test-model').value,voice=state.voices.find(v=>v.id===state.testVoiceId);const cloud_confirmed=await cloudSpeechUI.authorize(model,voice.audio_id,text);const job=await send('/api/jobs',{text,model_config_revision:revision,voice_id:state.testVoiceId,tts_model:model,language:$('voice-test-language').value,mode:'audio',...testEmotion.read(),cloud_confirmed});state.testJobId=job.id;replaceJob(job);}
  catch(error){state.testError=error.message;}finally{state.testSubmitting=false;renderVoiceTest();}
}
$('voice-test-generate').onclick=generateVoiceTest;
$('voice-test-model').onchange=()=>{testEmotion.refresh();state.testJobId=state.jobs.find(j=>j.voice_id===state.testVoiceId&&(j.tts_model||'gpt_sovits')===$('voice-test-model').value&&j.status==='completed'&&j.audio_ready)?.id||null;state.testError='';renderVoiceTest();};
function renderVoiceTest(){
  const job=state.jobs.find(j=>j.id===state.testJobId);testEmotion.lock(state.testSubmitting||busy(job));$('voice-test-generate').disabled=state.testSubmitting||!!busy(job)||testEmotion.uploading;
  $('voice-test-model').disabled=state.testSubmitting||!!busy(job);
  $('voice-test-language').disabled=($('voice-test-model').value!=='gpt_sovits'&&!engines.tts?.find(m=>m.id===$('voice-test-model').value)?.cloud)||state.testSubmitting||!!busy(job);
  const key=(job?[job.id,job.status,job.audio_ready].join(':'):'empty')+':'+state.testError;
  if(key!==testResultKey){testResultKey=key;
    if(state.testError)$('voice-test-result').innerHTML=`<p class="error-text" role="alert">${esc(state.testError)}</p>`;
    else if(!job)$('voice-test-result').innerHTML='<p>生成后可在这里播放，试听也会保存到「我的作品」。</p>';
    else if(busy(job))$('voice-test-result').innerHTML=`<p id="voice-progress-label"></p><div class="progress-track"><div class="progress-fill" id="voice-progress-fill"></div></div><button class="button" data-action="cancel-job" data-id="${job.id}">停止试听生成</button>`;
    else if(job.status==='completed')$('voice-test-result').innerHTML=`<p>${esc(modelName('tts',job.tts_model||'gpt_sovits'))}${esc(expressionName(job))} · ${esc(job.text)}</p><audio controls preload="none" src="${fileUrl(job,'voice.wav')}"></audio><a href="${fileUrl(job,'voice.wav',true)}">下载这段配音</a>`;
    else $('voice-test-result').innerHTML=`<p class="error-text">${esc(job.error||'生成已停止，可以再次试听。')}</p>`;
  }
  if(busy(job)&&$('voice-progress-label')){$('voice-progress-label').textContent=job.message;$('voice-progress-fill').style.width=job.progress+'%';}
}

document.addEventListener('click',event=>{
  const button=event.target.closest('[data-action]');if(!button)return;const {action,id}=button.dataset;
  const actions={'delete-job':()=>openProjectDelete([id]),'restore-job':()=>restoreProjects([id]),'delete-avatar':()=>openDelete('avatar',id),'delete-voice':()=>openDelete('voice',id),'add-avatar':()=>openAsset('avatar'),'add-voice':()=>openAsset('voice'),'edit-avatar':()=>openAsset('avatar',id),'edit-voice':()=>openAsset('voice',id),'use-avatar':()=>useAvatar(id),'use-voice':()=>useVoice(id),'test-voice':()=>openVoiceTest(id),'open-job':()=>openJob(id),'cancel-job':()=>cancelJob(id),'render-job':()=>renderAudio(id),'retry-job':()=>generate(state.jobs.find(j=>j.id===id)?.mode||'video')};
  if(actions[action])Promise.resolve(actions[action]()).catch(error=>toast(error.message));
});

async function refresh(){
  if(refreshing||state.projectMutating||state.deletion?.pending)return;refreshing=true;const epoch=dataEpoch;
  try{
    const [health,avatars,voices,jobs,trashJobs]=await Promise.all([api('/api/health'),api('/api/avatars'),api('/api/voices'),api('/api/projects'),api('/api/projects?trash=true')]);
    if(epoch!==dataEpoch)return;
    state.ready=health.ready;state.avatars=avatars;state.voices=voices;state.jobs=jobs;state.trashJobs=trashJobs;
    if(health.model_settings)window.modelSettingsUI?.accept(health.model_settings);
    populateModels(health.engines||{});
    $('model-status').innerHTML=`<span class="status-dot ${health.ready?'':'pending'}"></span>${health.ready?'本地模型就绪':`模型准备中 · ${health.downloaded}/${health.total}`}`;$('gpu-info').textContent=health.gpu;
    if(!state.initialized){
      state.initialized=true;let saved={};try{saved=JSON.parse(localStorage.getItem('avatar-studio.selection')||'{}');if(!saved.avatarId)saved.avatarId=localStorage.getItem('avatar-studio.selected-avatar');}catch{}
      state.avatarId=avatars.some(p=>p.id===saved.avatarId)?saved.avatarId:(avatars[0]?.id||'');
      state.voiceId=voices.some(p=>p.id===saved.voiceId)?saved.voiceId:(findAvatar()?.voice_id||voices[0]?.id||'');remember();
      applyVoiceModel();applyAvatarModels();
    }
    reconcileSelections();renderLibraries();renderEditor();renderVoiceTest();
  }catch(error){state.ready=false;$('model-status').innerHTML='<span class="status-dot pending"></span>本地服务未连接';console.error('工作室读取失败',error.message);}
  finally{refreshing=false;}
}
// Idle and hidden tabs must not compete with playback for the home connection.
async function poll(){
  if(!state.initialized||!document.hidden)await refresh();
  setTimeout(poll,remoteStudio?(state.jobs.some(busy)?3500:15000):2200);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
route();poll();
