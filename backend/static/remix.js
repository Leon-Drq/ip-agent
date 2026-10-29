'use strict';
(() => {
  const ui=id=>document.getElementById('remix-'+id);
  const labels={draft:'草稿',analyzing:'正在拆解',scripting:'正在反推剧本',storyboard:'待确认分镜',recognizing:'正在识别角色',ready:'准备就绪',generating:'正在生成',completed:'已完成',failed:'未完成',paused:'已暂停',interrupted:'已中断'};
  let project=null,history=[],config={configured:false,models:[]},dirty=false,working=false,loading=false,quote=null,shotKey='',historyKey='',audioKey='',voicesKey='',showReference=false;
  let selectedAssets={reference_id:'',person_id:'',product_ids:[]},assetMeta={};
  let pendingQuote='',restored=false,dialogueKey='',audioDirty=false,voiceSelection=[];
  let roleVoiceIds={},roleNames={},speakerOverrides={},roleKey='';
  const character=()=>document.querySelector('input[name="remix-mode"]:checked').value==='character';
  const imitate=()=>document.querySelector('input[name="remix-mode"]:checked').value==='imitate';
  const original=()=>character()||ui('voice-mode').value==='original';
  const selectedVoice=()=>!imitate()&&!original()&&ui('voice-mode').value==='clone'&&ui('voice-scope').value==='roles';
  const needsText=()=>!imitate()&&!original()&&ui('script-source').value==='original'&&!project?.transcript;
  function remember(id){try{localStorage.setItem('studio-remix-project',id||'new');}catch{}}
  const busy=()=>!!project&&['analyzing','scripting','recognizing','generating'].includes(project.status);
  const frozen=()=>busy()||!!project?.locked;
  const completed=()=>project?.status==='completed'&&!!project?.video_ready;
  const assetURL=(id,kind='poster')=>`/api/remixes/assets/${id}/${kind}`;
  function error(message=''){ui('error').textContent=message;ui('error').hidden=!message;const notice=document.getElementById('workflow-error');if(notice){notice.textContent=message;notice.hidden=!message;}}
  const story=new ImitationEditor({project:()=>project,enabled:imitate,locked:()=>working||frozen(),
    change:()=>{dirty=true;audioDirty=true;quote=null;pendingQuote='';controls();},action,save,load});
  const workflow=new RemixWorkflow({action,load,project:()=>project});
  function values(){return {revision:project?.revision||0,title:ui('title').value.trim(),...selectedAssets,
    edit_mode:document.querySelector('input[name="remix-mode"]:checked').value,...story.values(),target_character:ui('target-character').value.trim(),
    voice_mode:character()?'original':ui('voice-mode').value,voice_id:selectedVoice()?'':ui('voice').value,script:ui('script').value.trim(),instructions:ui('instructions').value.trim(),
    script_source:ui('script-source').value,voice_scope:imitate()?'all':ui('voice-scope').value,voice_segments:voiceSelection,
    role_voice_ids:roleVoiceIds,role_names:roleNames,speaker_overrides:speakerOverrides,
    aspect:ui('aspect').value,resolution:ui('resolution').value,image_model:ui('image-model').value,video_model:ui('video-model').value,
    make_images:imitate()||ui('make-images').checked,shot_prompts:[...ui('shots').querySelectorAll('textarea')].map(x=>x.value.trim()),
    reuse_shots:imitate()?story.reused:[...ui('shots').querySelectorAll('[data-reuse-shot]:checked')].map(x=>Number(x.dataset.reuseShot))};}
  function controls(){
    const lock=working||frozen();
    for(const el of document.querySelectorAll('#page-remix .remix-editor input,#page-remix .remix-editor textarea,#page-remix .remix-editor select,#page-remix .remix-editor button,#remix-shots textarea'))el.disabled=lock;
    for(const el of document.querySelectorAll('input[name="remix-mode"]'))el.disabled=working||frozen()&&!completed();
    for(const checkbox of ui('shots').querySelectorAll('[data-reuse-shot]')){checkbox.disabled=lock;ui('shot-'+checkbox.dataset.reuseShot).disabled=lock||checkbox.checked;}
    ui('quote').disabled=working||frozen()&&!completed();
    let label='查看预计积分',hint='先查看本次费用，确认后才会开始云端生成。';
    if(working){label='正在处理…';hint='正在保存项目或获取报价，请稍候。';}
    else if(busy()){label=project.status==='analyzing'?'正在准备镜头…':project.status==='recognizing'?'正在识别角色…':'正在生成…';hint=project.message;}
    else if(frozen()){label=completed()?'复用并继续编辑':'任务待继续';hint=completed()?'当前是已完成的成片。点击继续编辑或选择其他模式，会创建副本并保留原成片。':'可在预览区继续任务，或复用为新项目。';}
    else if(!selectedAssets.reference_id){label='上传原视频';hint='先选择想替换角色的原视频。';}
    else if(character()&&!selectedAssets.person_id){label='上传人物照片';hint='用清晰的人物照片，告诉我们要换成谁。';}
    else if(character()&&!ui('target-character').value.trim()){label='填写要替换的角色';hint='说明原片里要替换谁，避免误换其他人物。';}
    else if(imitate()&&!story.plan){label='反推视频剧本';hint='在本机读取画面和台词，整理可编辑的剧本，不消耗云端积分。';}
    else if(imitate()&&!story.plan.characters.some(c=>c.photo_id)){label='设置固定角色';hint='为至少一个角色上传照片，其他角色也会通过统一定妆图固定。';}
    else if(imitate()&&!story.approved){label='检查剧本与角色';hint='点击跳转到剧本确认处；确认后即可查看预计积分。';}
    else if(!project?.analysis||needsText()){label='准备原文并继续';hint=original()?'自动拆分镜头，直接保留原声；此步骤不消耗积分。':'在本机准备镜头和原文，不消耗云端积分。';}
    else if(selectedVoice()&&!project?.cast){label='识别原片角色';hint='自动将同一角色的台词归在一起，本机处理，不消耗积分。';}
    else if(selectedVoice()&&!castRoles().some(r=>r.segment_ids.length&&roleVoiceIds[r.id])){label='为角色选择声音';hint='为想更换音色的角色选择声音，其他角色保留原声。';}
    else if(!selectedVoice()&&ui('voice-mode').value==='clone'&&!ui('voice').value){label='选择克隆声音';hint='选择已保存的音色，用于这次配音。';}
    else if(!original()&&ui('voice-mode').value==='clone'&&(audioDirty||!project?.audio_preview?.audio_ready)){
      const running=!audioDirty&&['queued','running','processing'].includes(project?.audio_preview?.status);
      label=running?'正在准备配音…':'生成配音并查看费用';hint=running?project.audio_preview.message:'使用当前语音模型生成配音试听，再显示视频费用。';ui('quote').disabled=running;
    }
    else if(!config.configured){label='连接 HypiHub';hint='参考镜头已准备好，连接账号后即可查看费用。';}
    ui('quote').innerHTML=esc(label)+icon('arrow');ui('next-step').textContent=hint;
    ui('new').disabled=working;ui('connect').disabled=working;
    ui('duplicate').hidden=!project;ui('duplicate').disabled=working||busy();
    ui('analyze').disabled=lock||!selectedAssets.reference_id||!!project?.analysis;
    ui('use-transcript').disabled=lock||!project?.transcript;
    ui('voice-wrap').hidden=original()||ui('voice-mode').value!=='clone';ui('audition-wrap').hidden=ui('voice-wrap').hidden;
    ui('sound-plan').value=original()?'original':ui('voice-mode').value==='native'?'native':ui('voice-scope').value;
    ui('global-voice').hidden=selectedVoice();ui('roles-wrap').hidden=!selectedVoice();
    ui('roles-empty').hidden=!!project?.cast;ui('role-corrections').hidden=!project?.cast;
    ui('roles-status').textContent=project?.cast_error||(busy()?project.message:'自动识别原片里的说话人，再为角色指定声音。');
    ui('identify-roles').disabled=lock||!selectedAssets.reference_id;
    ui('character-wrap').hidden=false;ui('products-wrap').hidden=character();ui('sound-options').hidden=character();
    ui('preserve').hidden=!original();ui('script-wrap').hidden=original()||selectedVoice();ui('role-script-note').hidden=!selectedVoice();ui('assets-heading').textContent=character()?'换成谁':'角色与素材';
    ui('aspect').disabled=lock||original()||selectedVoice();ui('aspect').closest('label').hidden=original()||selectedVoice();
    const sourceOriginal=ui('script-source').value==='original';
    ui('script-source').disabled=lock||selectedVoice();ui('script').hidden=sourceOriginal;ui('use-transcript').hidden=sourceOriginal;
    ui('original-script').hidden=!sourceOriginal||selectedVoice();ui('original-script').textContent=project?.transcript||'继续时自动提取原视频台词，无需重复填写。';
    ui('role-count').textContent=project?.cast?`${castRoles().filter(r=>r.segment_ids.length).length} 个角色`:'';
    ui('audition').disabled=lock||!audioDirty&&['queued','running','processing'].includes(project?.audio_preview?.status);
    ui('analysis-hint').textContent=project?.analysis?'参考镜头已准备好。':'继续时会自动准备，不消耗积分。';
    ui('analyze').textContent=project?.analysis?'镜头已准备':'准备参考镜头';
    ui('image-model').disabled=true;ui('video-model').disabled=true;
    ui('save-state').textContent=working?'正在处理…':dirty?'有未保存的修改':project?'已保存到本机 · '+stamp(project.updated_at):'素材与项目保存在本机';
    ui('connection-label').textContent=config.configured?'HypiHub 已连接':'连接 HypiHub';ui('connection-dot').classList.toggle('pending',!config.configured);
    document.getElementById('page-remix').classList.toggle('remix-imitation-active',imitate());
    document.querySelector('#page-remix .remix-steps').innerHTML=imitate()?'<span>反推剧本</span><i>→</i><span>固定角色与门店</span><i>→</i><span>分镜确认后生成</span>':'<span>上传原片</span><i>→</i><span>选择新角色</span><i>→</i><span>查看费用并生成</span>';
    ui('products-wrap').querySelector('.remix-asset-label span').textContent=imitate()?'最多 2 张':'最多 6 张';
    ui('instructions').placeholder=imitate()?'例如：对白保持自然，动作简洁，每个镜头都保持同一套服装。':'例如：保留原角色的动作和衣服，其他人物不变。';
    document.querySelector('#page-remix .page-heading h1').textContent=imitate()?'同一个故事，用你的角色重新演绎。':'换个角色，保留熟悉的故事。';
    document.querySelector('#page-remix .page-heading p').textContent=imitate()?'原片反推剧本 → 固定人物与门店 → 检查分镜 → 生成新视频。':'上传原片和人物照片，选择要替换的角色。对白和音乐可以直接沿用。';
    ui('sound-plan').querySelector('option[value="roles"]').hidden=imitate();
    if(imitate()){
      ui('character-wrap').hidden=true;ui('assets-heading').textContent='声音与产品';
      ui('script-wrap').hidden=true;ui('preserve').hidden=true;
      ui('aspect').disabled=lock;ui('aspect').closest('label').hidden=false;
      ui('analyze').disabled=lock||!selectedAssets.reference_id;
      ui('analyze').textContent=story.plan?'重新反推剧本':'反推视频剧本';
      ui('analysis-hint').textContent=busy()?project.message:story.plan?'剧本已准备，可编辑人物、门店和分镜。':'本机理解画面与台词，不消耗积分。';
      if(project?.status==='storyboard'){ui('next-step').textContent='右侧检查角色与分镜，确认后继续生成视频。';ui('quote').textContent='等待确认分镜';}
    }
    story.render();
    workflow.render(project,imitate(),working);
  }
  function populateModels(){
    for(const [id,endpoint,preferred] of [['image-model','image_edits','gpt-image-2'],['video-model','videos','seedance-2-mini']]){
      const kind=id==='image-model'?'image':'video';
      const value=frozen()?project?.[id.replace('-','_')]:activeModel(kind,project?.[id.replace('-','_')]||ui(id).value);
      const models=config.models.filter(m=>m.endpoints?.includes(endpoint)&&(endpoint!=='videos'||['seedance-2','seedance-2-mini','seedance-2-fast','seedance-2.5'].includes(m.id.split('/').pop())));
      ui(id).replaceChildren(new Option(models.length?'请选择模型':'连接后读取可用模型',''));
      for(const model of models){if(activeModelSettings&&model.id!==value)continue;ui(id).add(new Option(model.display_name||model.id,model.id));}
      if(value&&models.some(m=>m.id===value))ui(id).value=value;else ui(id).value=models.find(m=>m.id===preferred||m.id.endsWith('/'+preferred))?.id||models[0]?.id||'';
      displayManagedModel(ui(id),kind);
      if(project&&!frozen()&&ui(id).value!==project[id.replace('-','_')])dirty=true;
    }
  }
  function voiceChoices(selected){
    const key=JSON.stringify(state.voices.map(v=>[v.id,v.name]));
    if(key===voicesKey&&selected===undefined)return;voicesKey=key;
    const value=selected??ui('voice').value;ui('voice').replaceChildren(new Option('选择已克隆声音',''));
    for(const v of state.voices)ui('voice').add(new Option(v.name,v.id));ui('voice').value=value;
  }
  function assets(){
    const ref=assetMeta[selectedAssets.reference_id];ui('reference-name').textContent=ref?ref.name:'上传对标视频';
    const person=assetMeta[selectedAssets.person_id];ui('person-name').textContent=person?person.name:'上传人物照片或视频';ui('person-remove').hidden=!person;
    ui('person-thumb').innerHTML=person?`<img src="${assetURL(person.id)}" alt="目标人物">`:icon('person');
    ui('products').innerHTML=selectedAssets.product_ids.map(id=>`<div class="remix-product"><img src="${assetURL(id)}" alt="${esc(assetMeta[id]?.name||'产品参考')}"><button type="button" data-remove-product="${id}" aria-label="移除${esc(assetMeta[id]?.name||'产品参考')}">${icon('close')}</button></div>`).join('');
  }
  function drawShots(force=false){
    const analysis=project?.analysis;const key=JSON.stringify([project?.id,analysis]);if(!force&&key===shotKey)return;shotKey=key;
    ui('shot-count').textContent=analysis?.shots.length||0;ui('shot-method').textContent=analysis?`${analysis.duration.toFixed(1)} 秒 · 本地镜头拆分`:'拆解后可逐镜头修改';
    ui('shots').innerHTML=analysis?analysis.shots.map((s,i)=>`<article class="remix-shot"><div class="remix-shot-image"><img src="/api/remixes/${project.id}/analysis/${s.frame}" alt="镜头 ${i+1} 参考画面"><span>${String(i+1).padStart(2,'0')}</span></div><div><div class="remix-shot-title"><strong>镜头 ${i+1}</strong><span>生成 ${s.seconds} 秒</span></div>${project.reuse_source?`<label class="remix-checkbox"><input type="checkbox" data-reuse-shot="${i}" ${project.reuse_shots?.includes(i)?'checked':''}>保留镜头 ${i+1} 的成片 · 不重新生成</label>`:''}<label class="sr-only" for="remix-shot-${i}">镜头 ${i+1} 改编要求</label><textarea id="remix-shot-${i}" maxlength="2000" rows="3" placeholder="这个镜头要改什么？可留空，沿用整体要求。">${esc(project.shot_prompts?.[i]||'')}</textarea>${s.text?`<p>参考原文：${esc(s.text)}</p>`:''}</div></article>`).join(''):'<p class="remix-shots-empty">每个镜头的参考画面和改编要求会显示在这里。</p>';
  }
  function castRoles(){
    const rows=project?.dialogue_segments||[],assignments={...project?.cast?.assignments,...speakerOverrides};
    return (project?.cast?.roles||[]).map(role=>{const members=rows.filter(r=>assignments[r.id]===role.id);return {...role,name:roleNames[role.id]||role.name,segment_ids:members.map(r=>r.id),sample:members.find(r=>r.id===role.sample_segment)||members[0]};});
  }
  function drawRoles(){
    const roles=castRoles(),active=roles.filter(r=>r.segment_ids.length);
    const key=JSON.stringify([project?.id,roles,roleVoiceIds,speakerOverrides,state.voices.map(v=>[v.id,v.name])]);if(key===roleKey)return;roleKey=key;
    const voiceOptions=id=>`<option value="">保留原声</option>`+state.voices.map(v=>`<option value="${esc(v.id)}" ${roleVoiceIds[id]===v.id?'selected':''}>${esc(v.name)}</option>`).join('')+(roleVoiceIds[id]&&!state.voices.some(v=>v.id===roleVoiceIds[id])?`<option value="${esc(roleVoiceIds[id])}" selected>原声音已删除，请重新选择</option>`:'');
    ui('roles').innerHTML=active.map((r,i)=>`<article class="remix-role-card${roleVoiceIds[r.id]?' changed':''}"><div class="remix-role-top"><span class="remix-role-icon">${icon('person')}</span><div><h4>${esc(r.name)}</h4><small>全部 ${r.segment_ids.length} 句台词</small></div><button type="button" class="link-button" data-play-role="${r.id}" aria-label="试听${esc(r.name)}原声">${icon('play')} 听原声</button></div><p class="remix-role-quote">“${esc(r.sample?.text||'')}”</p><label for="remix-role-voice-${r.id}" class="sr-only">${esc(r.name)}的声音</label><select id="remix-role-voice-${r.id}" data-role-voice="${r.id}">${voiceOptions(r.id)}</select><p class="remix-role-outcome">${roleVoiceIds[r.id]?`${r.segment_ids.length} 句台词统一换音色 · 台词不变`:'保留这个角色原来的声音'}</p></article>`).join('');
    ui('role-names').innerHTML=active.map(r=>`<label class="remix-rename">角色名称<input maxlength="60" data-role-name="${r.id}" value="${esc(r.name)}" aria-label="修改${esc(r.name)}名称"></label>`).join('');
    const options=roles.map(r=>`<option value="${r.id}">${esc(r.name)}</option>`).join('');
    ui('merge-from').innerHTML=active.map(r=>`<option value="${r.id}">${esc(r.name)}</option>`).join('');ui('merge-to').innerHTML=ui('merge-from').innerHTML;if(active.length>1)ui('merge-to').value=active[1].id;
    const assigned={...project?.cast?.assignments,...speakerOverrides};
    ui('dialogue').innerHTML=roles.length?(project?.dialogue_segments||[]).map(r=>`<div class="remix-dialogue-row"><div><p>${esc(r.text)}</p><small>${r.start.toFixed(2)}–${r.end.toFixed(2)} 秒 <button class="link-button" data-play-dialogue="${r.id}" type="button" aria-label="播放原声：${esc(r.text)}">听原声</button></small></div><select data-dialogue-role="${r.id}" aria-label="台词归属：${esc(r.text)}">${options}</select></div>`).join(''):'';
    for(const select of ui('dialogue').querySelectorAll('[data-dialogue-role]'))select.value=assigned[select.dataset.dialogueRole];
  }
  function preview(){
    let src='';const ready=project?.video_ready;
    ui('preview').preload='metadata';
    if(ready&&!showReference)src=`/api/remixes/${project.id}/result?run=${project.run_id}`;
    else if(project?.analysis)src=`/api/remixes/${project.id}/analysis/preview.mp4?ref=${project.reference_id}`;
    else if(selectedAssets.reference_id)src=assetURL(selectedAssets.reference_id,'content');
    ui('preview-empty').hidden=!!src;ui('preview').hidden=!src;
    if(selectedAssets.reference_id&&!(ready&&!showReference))ui('preview').poster=assetURL(selectedAssets.reference_id);
    else ui('preview').removeAttribute('poster');
    if(ui('preview').getAttribute('src')!==src){if(src)ui('preview').src=src;else{ui('preview').removeAttribute('src');ui('preview').load();}}
    ui('preview-heading').textContent=ready&&!showReference?(imitate()?'你的模仿创作视频':'你的复刻视频'):'参考预览';
    ui('project-status').textContent=project?(labels[project.status]||project.status):'等待上传';
    ui('progress').hidden=!busy();ui('progress-label').textContent=project?.message||'';ui('progress-percent').textContent=Math.round(project?.progress||0)+'%';ui('progress-fill').style.width=(project?.progress||0)+'%';
    ui('pause').hidden=project?.status!=='generating';ui('resume').hidden=!project?.can_resume;ui('download').hidden=!ready;ui('show-reference').hidden=!ready;
    if(ready)ui('download').href=`/api/remixes/${project.id}/result?download=true`;
    ui('show-reference').textContent=showReference?'查看新视频':'查看参考原片';
    ui('process-note').textContent=project?.error||project?.warning||(character()?'只替换指定角色，原声与时长保留。画面由模型生成，背景细节和口型可能与原片有差异。':'生成前会显示预计积分，确认后才会上传素材并调用云端模型。');
    ui('process-note').classList.toggle('remix-error-state',!!project?.error);
    if(project?.error?.includes('上传组件缺失')&&config.uploader?.ready){
      ui('process-note').textContent='本地上传组件已就绪。点击「继续已保存的任务」可从上次中断处继续。';
      ui('process-note').classList.remove('remix-error-state');
    }
    ui('transcript-wrap').hidden=!project?.transcript;ui('transcript').textContent=project?.transcript||'';
  }
  function renderHistory(){
    const key=JSON.stringify([project?.id,history.map(p=>[p.id,p.status,p.title,p.updated_at])]);if(key===historyKey)return;historyKey=key;
    ui('history-count').textContent=history.length+' 个项目';
    const card=p=>`<button class="remix-history-card${project?.id===p.id?' active':''}" data-remix-open="${p.id}">${p.reference_id?`<img src="${assetURL(p.reference_id)}" alt="参考封面">`:icon('remix')}<div><strong>${esc(p.title)}</strong><small>${esc(labels[p.status]||p.status)} · ${esc(stamp(p.updated_at))}</small></div></button>`;
    ui('history').innerHTML=history.length?history.map(card).join(''):'<p class="remix-hint">保存的项目会出现在这里，可随时继续。</p>';
  }
  function draw(){assets();preview();drawShots();drawRoles();controls();showAudio();renderHistory();}
  function record(p){history=[p,...history.filter(x=>x.id!==p.id)].sort((a,b)=>b.updated_at.localeCompare(a.updated_at));}
  async function save(){
    if(project&&!dirty)return project;
    const body=values();if(!body.title)throw new Error('请填写项目名称。');
    const p=await send('/api/remixes'+(project?'/'+project.id:''),body,project?'PUT':'POST');project=p;remember(p.id);assetMeta={...assetMeta,...p.assets};record(p);dirty=false;audioDirty=false;draw();return p;
  }
  function load(p){
    project=p;dirty=false;showReference=false;quote=null;pendingQuote='';assetMeta=p?.assets||{};
    story.load(p);
    if(restored)remember(p?.id);
    document.querySelector(`input[name="remix-mode"][value="${p?.edit_mode|| (p?'remix':'character')}"]`).checked=true;
    selectedAssets={reference_id:p?.reference_id||'',person_id:p?.person_id||'',product_ids:p?.product_ids||[]};
    ui('products-wrap').open=!!selectedAssets.product_ids.length;
    for(const [id,key,def] of [['title','title','我的视频复刻'],['target-character','target_character',''],['script','script',''],['instructions','instructions',''],['voice-mode','voice_mode','original'],['aspect','aspect','9:16'],['resolution','resolution','720p']])ui(id).value=p?.[key]??def;
    ui('make-images').checked=p?.make_images??true;voiceChoices(p?.voice_id||'');populateModels();audioKey='';ui('audio').hidden=true;ui('audio-status').textContent='';drawShots(true);error();
    ui('script-source').value=p?.script_source||(p?.script?.trim()?'custom':'original');
    ui('voice-scope').value=p?.voice_scope==='all'?'all':'roles';voiceSelection=p?.voice_segments||[];
    roleVoiceIds={...p?.role_voice_ids};roleNames={...p?.role_names};speakerOverrides={...p?.speaker_overrides};audioDirty=false;
    if(p&&(!p.script_source||p.voice_scope==='selected')&&!frozen())dirty=true;
    seedLegacyRoles();if(selectedVoice())ui('script-source').value='original';draw();
  }
  function seedLegacyRoles(){
    if(!project?.cast||Object.values(roleVoiceIds).some(Boolean)||!voiceSelection.length||!project.voice_id)return;
    for(const r of castRoles())if(r.segment_ids.length&&r.segment_ids.every(id=>voiceSelection.includes(id)))roleVoiceIds[r.id]=project.voice_id;
    if(Object.keys(roleVoiceIds).length&&!frozen())dirty=true;
  }
  async function action(fn){if(working)return;working=true;error();controls();try{await fn();}catch(e){pendingQuote='';error(e.message);}finally{working=false;controls();}}
  async function upload(file,role){
    if(!file)return;if(file.size>(role==='product'?20:500)*1024**2)throw new Error('图片最大 20 MB，视频最大 500 MB。');
    const form=new FormData();form.append('role',role);form.append('file',file);
    const meta=await api('/api/remixes/assets',{method:'POST',body:form});assetMeta[meta.id]=meta;
    if(role==='product')selectedAssets.product_ids.push(meta.id);else selectedAssets[role+'_id']=meta.id;
    if(role==='reference'){story.plan=null;story.approved=false;story.key='';voiceSelection=[];roleVoiceIds={};roleNames={};speakerOverrides={};audioDirty=true;if(project){project={...project,screenplay:null,analysis:null,cast:null,cast_error:null,transcript:'',dialogue_segments:[],audio_preview:null};ui('shots').replaceChildren();}}
    dirty=true;await save();
  }
  for(const role of ['reference','person','product']){
    ui(role+'-upload').onclick=()=>ui(role+'-file').click();
    ui(role+'-file').onchange=()=>action(async()=>{const files=[...ui(role+'-file').files];if(role==='product'&&selectedAssets.product_ids.length+files.length>(imitate()?2:6))throw new Error(imitate()?'模仿创作最多使用 2 张产品图片。':'一个项目最多上传 6 张产品图片。');for(const file of files)await upload(file,role);ui(role+'-file').value='';});
  }
  for(const ev of ['dragover','dragleave','drop'])ui('reference-upload').addEventListener(ev,event=>{event.preventDefault();ui('reference-upload').classList.toggle('dragover',ev==='dragover');if(ev==='drop'&&!working&&!frozen())action(()=>upload(event.dataTransfer.files[0],'reference'));});
  ui('person-remove').onclick=()=>{selectedAssets.person_id='';dirty=true;draw();};
  ui('products').onclick=event=>{const btn=event.target.closest('[data-remove-product]');if(btn&&!working&&!frozen()){selectedAssets.product_ids=selectedAssets.product_ids.filter(x=>x!==btn.dataset.removeProduct);dirty=true;draw();}};
  document.querySelector('#page-remix').addEventListener('input',event=>{
    if(event.target.name==='remix-mode'&&completed()){
      const mode=event.target.value;
      document.querySelector(`input[name="remix-mode"][value="${project.edit_mode}"]`).checked=true;
      action(()=>duplicateProject(mode));return;
    }
    if(event.target.matches('input:not([type=file]),textarea,select')){dirty=true;quote=null;pendingQuote='';
    if(event.target.name==='remix-mode'&&imitate()){ui('voice-mode').value='native';ui('voice-scope').value='all';audioDirty=true;}
    if(event.target.id==='remix-sound-plan'){const plan=event.target.value;ui('voice-mode').value=['roles','all'].includes(plan)?'clone':plan;if(['roles','all'].includes(plan))ui('voice-scope').value=plan;}
    if(character())ui('voice-mode').value='original';
    if(imitate()&&event.target.matches('#remix-instructions,#remix-sound-plan,#remix-aspect')){story.approved=false;document.getElementById('imitation-approved').checked=false;}
    if(event.target.dataset.roleVoice!==undefined){roleVoiceIds[event.target.dataset.roleVoice]=event.target.value;voiceSelection=[];}
    if(event.target.dataset.roleName!==undefined)roleNames[event.target.dataset.roleName]=event.target.value.trim();
    if(event.target.dataset.dialogueRole!==undefined)speakerOverrides[event.target.dataset.dialogueRole]=event.target.value;
    if(event.target.matches('#remix-voice,#remix-sound-plan,#remix-voice-mode,#remix-script,#remix-script-source,#remix-voice-scope,[data-role-voice],[data-dialogue-role]'))audioDirty=true;
    if(selectedVoice())ui('script-source').value='original';if(event.target.matches('[data-role-voice],[data-dialogue-role]'))drawRoles();controls();preview();showAudio();}});
  ui('role-names').addEventListener('change',()=>{drawRoles();controls();});
  ui('save').onclick=()=>action(async()=>{await save();toast('复刻项目已保存。');});
  ui('new').onclick=()=>action(async()=>{if(dirty&&!frozen())await save();load(null);});
  async function duplicateProject(mode){
    if(dirty&&!frozen())await save();
    const copy=await send(`/api/remixes/${project.id}/duplicate`,{});record(copy);load(copy);
    if(mode&&mode!==copy.edit_mode){
      const choice=document.querySelector(`input[name="remix-mode"][value="${mode}"]`);
      choice.checked=true;choice.dispatchEvent(new Event('input',{bubbles:true}));await save();
    }
    toast('已打开可编辑副本，原成片已保留。');
  }
  ui('duplicate').onclick=()=>action(()=>duplicateProject());
  ui('analyze').onclick=()=>action(async()=>{if(imitate()&&story.plan&&!confirm('重新反推会替换当前剧本和角色设置，是否继续？'))return;await save();project=await send(`/api/remixes/${project.id}/${imitate()?'screenplay':'analyze'}`,{});record(project);draw();});
  ui('clone').onclick=()=>openAsset('voice');
  window.addEventListener('studio-asset-saved',event=>{if(state.route==='remix'&&event.detail.kind==='voice'){voiceChoices(event.detail.profile.id);ui('voice-mode').value='clone';dirty=true;audioDirty=true;drawRoles();controls();showAudio();}});
  async function prepareRoles(){
    await save();
    const endpoint=!project.analysis||!project.transcript?'analyze':'roles';
    project=await send(`/api/remixes/${project.id}/${endpoint}`,{});record(project);draw();
  }
  ui('identify-roles').onclick=()=>action(prepareRoles);
  ui('sound-plan').addEventListener('change',()=>{if(selectedVoice()&&selectedAssets.reference_id&&!project?.cast&&!busy())action(prepareRoles);});
  ui('merge-roles').onclick=()=>{
    if(working||frozen())return;const from=ui('merge-from').value,to=ui('merge-to').value;
    if(!from||!to||from===to){error('请选择两个不同的角色进行合并。');return;}
    if(roleVoiceIds[from]&&roleVoiceIds[to]&&roleVoiceIds[from]!==roleVoiceIds[to]){error('这两个角色使用不同的新音色，请先统一声音再合并。');return;}
    const role=castRoles().find(r=>r.id===from);for(const id of role.segment_ids)speakerOverrides[id]=to;
    if(roleVoiceIds[from])roleVoiceIds[to]=roleVoiceIds[from];delete roleVoiceIds[from];voiceSelection=[];
    dirty=true;audioDirty=true;quote=null;pendingQuote='';error();draw();
  };
  ui('reset-roles').onclick=()=>{if(working||frozen())return;speakerOverrides={};dirty=true;audioDirty=true;quote=null;pendingQuote='';draw();};
  ui('use-transcript').onclick=()=>{if(ui('script').value.trim()&&!confirm('用参考视频原文替换当前文案？'))return;ui('script').value=(project?.transcript||'').slice(0,8000);dirty=true;audioDirty=true;controls();showAudio();};
  ui('audition').onclick=()=>action(async()=>{await save();if(needsText()||selectedVoice()&&!project.cast){await prepareRoles();return;}await send(`/api/remixes/${project.id}/voice`,{model_config_revision:modelRevision(),cloud_confirmed:await cloudSpeechUI.remixAuthorize(project)});project=await api(`/api/remixes/${project.id}`);record(project);draw();});
  function showAudio(){
    const job=audioDirty?null:project?.audio_preview;
    const key=JSON.stringify(job);if(key===audioKey)return;audioKey=key;
    ui('audio-status').textContent=!job?'当前台词或声音尚未生成试听。':job.audio_ready?'配音已准备好 · 可试听后查看预计积分':job.error||job.message;
    ui('audio').hidden=!job?.audio_ready;
    if(job?.audio_ready)ui('audio').src=job.url;else{ui('audio').removeAttribute('src');ui('audio').load();}
  }
  let dialogueStop=null;
  function playOriginal(row){if(!row)return;showReference=true;preview();const player=ui('preview');player.currentTime=row.start;dialogueStop=row.end;player.play().catch(()=>error('原声暂未加载，请在右侧视频中播放。'));}
  ui('roles').onclick=event=>{const button=event.target.closest('[data-play-role]');if(button)playOriginal(castRoles().find(r=>r.id===button.dataset.playRole)?.sample);};
  ui('dialogue').onclick=event=>{const button=event.target.closest('[data-play-dialogue]');if(!button)return;
    const row=project.dialogue_segments.find(r=>r.id===Number(button.dataset.playDialogue));if(!row)return;
    playOriginal(row);
  };
  ui('preview').addEventListener('timeupdate',()=>{if(dialogueStop!==null&&ui('preview').currentTime>=dialogueStop){ui('preview').pause();dialogueStop=null;}});
  async function prepareQuote(){
    await save();
    if(imitate()){
      if(!story.plan){project=await send(`/api/remixes/${project.id}/screenplay`,{});record(project);draw();return;}
      if(!story.plan.characters.some(c=>c.photo_id)){document.getElementById('imitation-cast').scrollIntoView({behavior:'smooth',block:'center'});error('请先为要固定身份的角色上传照片。');return;}
      if(!story.approved){document.getElementById('imitation-approved').focus();error('请先确认剧本、角色与门店设置。');return;}
    }
    if(!project.analysis||needsText()){pendingQuote=project.id;project=await send(`/api/remixes/${project.id}/analyze`,{});record(project);draw();return;}
    if(selectedVoice()&&!project.cast){pendingQuote='';await prepareRoles();return;}
    if(project.voice_mode==='clone'&&!project.audio_preview?.audio_ready){
      if(!selectedVoice()&&!project.voice_id){pendingQuote='';ui('voice').focus();return;}
      if(selectedVoice()&&!castRoles().some(r=>r.segment_ids.length&&roleVoiceIds[r.id])){pendingQuote='';ui('roles').querySelector('select')?.focus();error('请选择要更换音色的角色，并为它指定声音。');return;}
      await send(`/api/remixes/${project.id}/voice`,{model_config_revision:modelRevision(),cloud_confirmed:await cloudSpeechUI.remixAuthorize(project)});project=await api(`/api/remixes/${project.id}`);pendingQuote=project.id;record(project);draw();return;
    }
    pendingQuote='';await showQuote();
  }
  async function showQuote(){
    if(!config.configured){navigate('models');return;}
    await save();quote=await send(`/api/remixes/${project.id}/quote`,{});
    ui('cost').textContent=quote.total.toLocaleString('zh-CN',{maximumFractionDigits:2});
    ui('cost-details').innerHTML=(quote.edit_mode==='imitate'?`<p>先生成定妆图与分镜，再生成约 ${Number(quote.source_seconds).toFixed(1)} 秒的新视频。</p>`:quote.voice_mode==='original'||['selected','roles'].includes(quote.voice_scope)?`<p>成片约 ${Number(quote.source_seconds).toFixed(1)} 秒 · ${quote.voice_mode==='clone'?'按角色更换音色，其他角色保留原声':'保留原视频声音与比例'}</p>`:'')+quote.items.map(s=>`<div><span>${esc(s.label||`镜头 ${s.shot}`)} · ${s.reused?'保留已有素材':s.seconds?`生成 ${s.seconds} 秒`:'生成 1 张'}</span><span>${s.reused?'不消耗积分':(s.image_credits+s.video_credits).toFixed(2)+' 积分'}</span></div>`).join('');
    ui('quote-note').textContent=quote.note;ui('generate-error').hidden=true;ui('quote-dialog').showModal();
  }
  ui('quote').onclick=()=>{
    if(working)return;
    if(completed()){action(()=>duplicateProject());return;}
    if(frozen())return;
    if(!selectedAssets.reference_id){ui('reference-file').click();return;}
    if(character()&&!selectedAssets.person_id){ui('person-file').click();return;}
    if(character()&&!ui('target-character').value.trim()){ui('target-character').focus();return;}
    if(imitate()&&story.plan?.characters.some(c=>c.photo_id)&&!story.approved){
      error();
      const approval=document.getElementById('imitation-approved');
      document.getElementById('imitation-approval-wrap').scrollIntoView({behavior:'instant',block:'center'});
      approval.focus({preventScroll:true});
      toast('检查剧本与角色后，勾选这里的确认，即可查看预计积分。');return;
    }
    action(prepareQuote);
  };
  ui('generate').onclick=async()=>{if(working||!quote)return;working=true;ui('generate').disabled=true;try{project=await send(`/api/remixes/${project.id}/generate`,{quote_id:quote.id,accepted_estimate:quote.total});quote=null;record(project);draw();ui('quote-dialog').close();}catch(e){ui('generate-error').textContent=e.message;ui('generate-error').hidden=false;}finally{working=false;ui('generate').disabled=false;controls();}};
  for(const id of ['quote-close','quote-cancel'])ui(id).onclick=()=>ui('quote-dialog').close();
  ui('pause').onclick=()=>action(async()=>{project=await send(`/api/remixes/${project.id}/pause`,{});draw();});
  ui('resume').onclick=()=>action(async()=>{project=await send(`/api/remixes/${project.id}/resume`,{});draw();});
  ui('show-reference').onclick=()=>{showReference=!showReference;preview();};
  ui('connect').onclick=()=>navigate('models');
  window.addEventListener('model-settings-changed',()=>{populateModels();if(!frozen()){quote=null;pendingQuote='';}controls();});
  ui('connect-close').onclick=()=>ui('connect-dialog').close();
  ui('connect-form').onsubmit=async event=>{event.preventDefault();ui('connect-save').disabled=true;ui('connect-error').hidden=true;try{const result=await send('/api/remixes/connect',{key:ui('key').value});ui('key').value='';config={...config,...result};populateModels();ui('connect-dialog').close();toast('HypiHub 已连接，可以查看预计积分后生成。');controls();}catch(e){ui('connect-error').textContent=e.message;ui('connect-error').hidden=false;}finally{ui('connect-save').disabled=false;}};
  document.addEventListener('click',event=>{const b=event.target.closest('[data-remix-open]');if(b)action(async()=>{if(dirty&&!frozen())await save();load(await api('/api/remixes/'+b.dataset.remixOpen));navigate('remix');});});
  window.addEventListener('studio-open-remix',event=>action(async()=>{
    if(dirty&&!frozen())await save();
    const p=await api('/api/remixes/'+event.detail);
    const url=new URL(location.href);url.searchParams.set('project',p.id);window.history.replaceState(null,'',url);
    load(p);navigate('remix');
  }));
  async function refreshRemix(){
    if(loading||working)return;loading=true;
    try{
      const [rows,settings]=await Promise.all([api('/api/remixes'),api('/api/remixes/config')]);history=rows;
      if(JSON.stringify(config)!==JSON.stringify(settings)){config=settings;populateModels();preview();}
      voiceChoices();
      if(!restored){let saved='';try{saved=localStorage.getItem('studio-remix-project')||'';}catch{}const requested=new URL(location.href).searchParams.get('project');if(rows.some(p=>p.id===requested))saved=requested;restored=true;if(!dirty&&saved!=='new'){const last=rows.find(p=>p.id===saved)||rows.find(p=>p.reference_id);if(last)load(last);}}
      if(project&&!dirty){const current=rows.find(p=>p.id===project.id);if(current&&JSON.stringify(current)!==JSON.stringify(project)){story.load(current);project=current;assetMeta={...assetMeta,...current.assets};roleVoiceIds={...current.role_voice_ids};roleNames={...current.role_names};speakerOverrides={...current.speaker_overrides};seedLegacyRoles();draw();}}
      showAudio();
      renderHistory();controls();
      if(pendingQuote&&project?.id===pendingQuote&&!busy()&&!dirty){
        if(project.status==='failed'||project.audio_preview?.status==='failed'){pendingQuote='';error(project.error||project.audio_preview?.error||'准备未完成，请重试。');}
        else if(project.analysis&&(!project.audio_preview||project.audio_preview.audio_ready))await action(prepareQuote);
      }
    }catch(e){if(state.route==='remix')error(e.message);}finally{loading=false;}
  }
  window.addEventListener('studio-route',event=>{if(['remix','projects'].includes(event.detail))refreshRemix();});
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  async function poll(){if(['remix','projects'].includes(state.route)&&!document.hidden)await refreshRemix();setTimeout(poll,2500);}
  load(null);poll();
})();
