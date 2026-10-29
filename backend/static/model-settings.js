'use strict';
window.modelSettingsUI={
  data:null,category:'video',saving:false,loading:false,
  accept(value){
    if(!value)return;
    const changed=JSON.stringify(activeModelSettings)!==JSON.stringify({revision:value.revision,selected:value.selected});
    activeModelSettings={revision:value.revision,selected:value.selected};
    this.data={...this.data,...value};
    if(changed){
      if(engines.tts)populateModels(engines);
      window.dispatchEvent(new CustomEvent('model-settings-changed'));
    }
    if(changed||value.categories)this.render();
  },
  render(){
    if(!this.data?.categories)return;
    const {categories,selected}=this.data;
    const symbols={video:'video',tts:'mic',image:'person',lipsync:'play',restoration:'settings',transcription:'document',understanding:'document'};
    $('models-categories').innerHTML=categories.map(c=>{
      const model=c.models.find(m=>m.id===selected[c.id]);
      return `<button type="button" data-category="${c.id}" class="models-category${this.category===c.id?' active':''}" aria-pressed="${this.category===c.id}">${icon(symbols[c.id])}<span><strong>${esc(c.name)}</strong><small>${esc(model?.name||selected[c.id])}</small></span>${this.category===c.id?icon('arrow'):''}</button>`;
    }).join('');
    const category=categories.find(c=>c.id===this.category)||categories[0];
    const active=category.models.find(m=>m.id===selected[category.id]);
    let groups='';
    for(const source of ['本地','API']){
      const models=category.models.filter(m=>m.source===source&&m.supported!==false);if(!models.length)continue;
      groups+=`<div class="models-group-label"><span>${source==='本地'?'本地模型':'API 模型'}</span><small>${source==='本地'?'在这台电脑上运行':'按服务商用量计费'}</small></div><div class="models-choices">`;
      groups+=models.map(m=>{
        const checked=selected[category.id]===m.id;
        return `<label class="models-choice${checked?' selected':''}${m.available?'':' unavailable'}"><input type="radio" name="active-${category.id}" value="${esc(m.id)}" ${checked?'checked':''} ${!m.available||this.saving||remoteStudio?'disabled':''}><span class="models-choice-content"><span class="models-choice-title"><strong>${esc(m.name)}</strong>${checked?'<span class="models-active-tag">当前启用</span>':''}</span><span class="models-choice-description">${esc(m.description||'')}</span>${!m.available?`<span class="models-unavailable-reason">${esc(m.reason||'暂不可用')}</span>`:''}</span></label>`;
      }).join('')+'</div>';
    }
    const unsupported=category.models.filter(m=>m.supported===false);
    if(unsupported.length)groups+=`<details class="models-other"><summary>其他账号模型 · ${unsupported.length} 个暂未适配</summary><p>${unsupported.map(m=>esc(m.name)).join('、')}</p><p>这些模型暂不能用于当前复刻流程。</p></details>`;
    const service=category.id==='tts'?'speech':['video','image'].includes(category.id)?'hypihub':'';
    $('models-options').innerHTML=`<div class="models-panel-heading"><div><h2>${esc(category.name)}</h2><p>${esc(category.description)}</p></div><span class="models-single">单选</span></div>${active&&!active.available?'<p class="models-alert">当前模型暂不可用，请连接对应服务或选择其他模型。</p>':''}${groups}${service?`<button type="button" class="link-button models-service-shortcut" data-model-service="${service}">配置 ${service==='speech'?'MiniMax / ElevenLabs':'HypiHub'} 连接 ${icon('arrow')}</button>`:''}`;
    if(remoteStudio)$('models-message').textContent='请在本机工作室修改模型配置。';
  },
  async load(accounts=false){
    if(this.loading)return;this.loading=true;
    try{
      const [value,hub]=await Promise.all([api('/api/model-settings'),api('/api/remixes/config')]);
      this.accept(value);$('models-hub-status').textContent=hub.configured?'账号已连接 · '+(hub.uploader?.ready?'上传组件就绪':'上传组件待修复'):'尚未连接';
      $('models-hub-status').className=hub.configured&&hub.uploader?.ready?'models-connected':'';
      $('models-hub-status').title=hub.uploader?.message||'';
      if(accounts)await cloudSpeechUI.settings(true);
    }catch(e){$('models-message').textContent=e.message;}finally{this.loading=false;}
  },
  async choose(model){
    if(this.saving)return;this.saving=true;this.render();$('models-message').textContent='正在保存…';
    try{
      const value=await send('/api/model-settings/'+this.category,{model,revision:this.data.revision},'PUT');
      this.accept(value);$('models-message').textContent='已保存 · 新任务使用当前模型';
    }catch(e){await this.load();$('models-message').textContent=e.message;}
    finally{this.saving=false;this.render();}
  }
};
$('models-categories').onclick=event=>{const button=event.target.closest('[data-category]');if(!button||modelSettingsUI.saving)return;modelSettingsUI.category=button.dataset.category;$('models-message').textContent='';modelSettingsUI.render();};
$('models-options').onchange=event=>{if(event.target.matches('input[type=radio]'))modelSettingsUI.choose(event.target.value);};
$('models-options').onclick=event=>{
  const button=event.target.closest('[data-model-service]');if(!button)return;
  const speech=button.dataset.modelService==='speech';
  if(speech)$('models-cloud-services').open=true;
  $(speech?'models-cloud-services':'models-hub-connect').scrollIntoView({behavior:'smooth',block:'center'});
};
$('models-hub-connect').onclick=()=>{$('remix-connect-error').hidden=true;$('remix-key').value='';$('remix-connect-dialog').showModal();};
$('models-reload').onclick=async()=>{
  $('models-reload').disabled=true;
  try{if(!remoteStudio)await send('/api/remixes/models/refresh',{});await modelSettingsUI.load(true);$('models-message').textContent='模型和连接状态已更新';}
  catch(e){$('models-message').textContent=e.message;}finally{$('models-reload').disabled=false;}
};
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-open-models]');if(!button)return;event.preventDefault();
  if(button.dataset.modelCategory)modelSettingsUI.category=button.dataset.modelCategory;
  // Closing these dialogs preserves their in-memory inputs. Existing assets are untouched.
  button.closest('dialog')?.close();navigate('models');modelSettingsUI.render();
});
window.addEventListener('studio-route',event=>{if(event.detail==='models')modelSettingsUI.load(true);});
$('remix-connect-dialog').addEventListener('close',()=>modelSettingsUI.load());
$('cloud-settings-dialog').addEventListener('close',()=>modelSettingsUI.load());
modelSettingsUI.load(location.hash==='#models');
