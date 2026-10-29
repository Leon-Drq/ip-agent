'use strict';
const cloudSpeechUI = {
  active: false,
  isCloud(model) { return !!engines.tts?.find(m=>m.id===model)?.cloud; },
  async settings(inline=this.inline||false) {
    this.inline=inline;
    (inline?$('models-services'):$('cloud-settings-dialog')).append($('cloud-service-list'));
    const rows=await api('/api/cloud-speech');
    $('cloud-service-list').innerHTML=rows.map(p=>`<form data-cloud-provider="${p.id}" class="cloud-service-card"><div class="cloud-service-heading"><strong>${esc(p.name)}</strong><span>${p.configured?'已连接':'尚未连接'}</span></div><p>${p.id==='minimax'?'中文克隆 · HD / Turbo · 情感预设':'多语言克隆 · Eleven v4 / v4 Turbo · 情感预设'}</p>${p.id==='minimax'?`<label>账号地区<select name="region"><option value="cn" ${p.region==='cn'?'selected':''}>中国站</option><option value="global" ${p.region==='global'?'selected':''}>国际站</option></select></label>`:'<input type="hidden" name="region" value="global">'}<label>API Key<input name="api_key" type="password" autocomplete="off" placeholder="${p.configured?'已加密保存；填写可更换':'粘贴服务商的 API Key'}" required></label><p>${esc(p.note)} <a href="${esc(p.pricing)}" target="_blank" rel="noopener noreferrer">查看价格 ↗</a></p><div class="cloud-service-actions"><button class="button dark" type="submit">保存并验证</button>${p.configured?'<button class="button" type="button" data-cloud-disconnect>断开连接</button>':''}</div><p class="error-text" role="status" data-cloud-message></p></form>`).join('');
    if(!inline&&!$('cloud-settings-dialog').open)$('cloud-settings-dialog').showModal();
    if(inline)$('models-cloud-status').textContent=rows.filter(p=>p.configured).length+' / '+rows.length+' 已连接';
  },
  confirm(title, text, action) {
    if(this.active) return Promise.reject(new Error('请先完成当前云端操作。'));
    this.active=true;
    $('cloud-confirm-title').textContent=title;$('cloud-confirm-copy').textContent=text;$('cloud-confirm-ok').textContent=action;
    return new Promise(resolve=>{
      const finish=accepted=>{this.active=false;$('cloud-confirm-dialog').close();resolve(accepted);};
      $('cloud-confirm-ok').onclick=()=>finish(true);$('cloud-confirm-cancel').onclick=()=>finish(false);
      $('cloud-confirm-dialog').oncancel=event=>{event.preventDefault();finish(false);};$('cloud-confirm-dialog').showModal();
    });
  },
  async prepare(model,audioId) {
    if(!this.isCloud(model))return;
    if(remoteStudio)throw new Error('请在本机工作室设置和使用云端声音。');
    if(!engines.tts.find(m=>m.id===model).available){await this.settings(false);throw new Error('请先连接所选语音服务，然后再次保存或试听。');}
    const body={tts_model:model,audio_id:audioId,model_config_revision:modelRevision()};
    const result=await send('/api/cloud-speech/voice/status',body);
    if(result.ready)return;
    const meta=await api('/api/uploads/'+audioId);
    if(meta.duration<10||meta.duration>300)throw new Error('云端克隆需要 10 秒至 5 分钟的清晰单人录音，建议 30–60 秒。请在编辑声音中更换录音。');
    const name=modelName('tts',model);
    if(!await this.confirm('启用云端克隆声音',`将「${meta.name}」（${meta.duration.toFixed(1)} 秒）上传给 ${name.split(' · ')[0]}，创建可复用的音色。请使用你有权克隆的录音。声音创建或首次使用可能收费，以服务商账号为准。`, '上传并启用声音'))throw new Error('已取消上传录音。');
    toast('正在启用云端声音，请稍候…');
    await send('/api/cloud-speech/voice/prepare',{...body,confirmed:true});
    toast('云端声音已保存。MiniMax 新声音请在 7 天内生成一次试听以激活。');
  },
  async authorize(model,audioId,text) {
    if(!this.isCloud(model))return false;
    await this.prepare(model,audioId);
    const providers=await api('/api/cloud-speech'),provider=providers.find(p=>p.id===engines.tts.find(m=>m.id===model).provider);
    if(!await this.confirm('确认云端配音',`使用 ${modelName('tts',model)} 生成这段 ${text.length} 字的配音。文案会发送给该服务商。\n\n${provider.note}\n\n生成后保存到本地，可继续对口型。`, '确认并生成配音'))throw new Error('已取消云端配音。');
    return true;
  },
  async remixAuthorize(project) {
    const ids=project.voice_scope==='roles'?Object.values(project.role_voice_ids||{}):[project.voice_id];
    const voices=[...new Set(ids)].map(id=>state.voices.find(v=>v.id===id)).filter(Boolean).map(v=>({...v,tts_model:activeModel('tts',v.tts_model)})).filter(v=>this.isCloud(v.tts_model));
    if(!voices.length)return false;
    for(const v of voices)await this.prepare(v.tts_model,v.audio_id);
    const providers=await api('/api/cloud-speech'),used=new Set(voices.map(v=>engines.tts.find(m=>m.id===v.tts_model).provider));
    if(!await this.confirm('确认角色配音',`本次使用云端声音：${voices.map(v=>v.name+'（'+modelName('tts',v.tts_model)+'）').join('、')}。这些角色的台词将发送给相应服务商，分段生成。\n\n${providers.filter(p=>used.has(p.id)).map(p=>p.name+'：'+p.note).join('\n')}\n\n该费用与 HypiHub 视频积分分开。`, '确认并生成角色配音'))throw new Error('已取消云端角色配音。');
    return true;
  }
};
document.body.insertAdjacentHTML('beforeend',`<dialog id="cloud-settings-dialog" aria-labelledby="cloud-settings-title"><div class="dialog-heading"><div><span class="eyebrow">CLOUD VOICES</span><h2 id="cloud-settings-title">云端语音设置</h2></div><button class="button" id="cloud-settings-close" type="button">关闭</button></div><p class="dialog-description">连接后，请在模型配置中启用一个语音模型。密钥仅加密保存在这台电脑。</p><div id="cloud-service-list"></div></dialog><dialog id="cloud-confirm-dialog" aria-labelledby="cloud-confirm-title"><h2 id="cloud-confirm-title"></h2><p class="dialog-description" id="cloud-confirm-copy"></p><div class="dialog-actions"><button class="button" id="cloud-confirm-cancel">取消</button><button class="button dark" id="cloud-confirm-ok"></button></div></dialog>`);
$('cloud-settings-close').onclick=()=>$('cloud-settings-dialog').close();
$('cloud-settings-dialog').addEventListener('close',()=>{$('cloud-settings-dialog').querySelectorAll('input[type=password]').forEach(i=>i.value='');});
document.addEventListener('click',event=>{if(event.target.closest('[data-cloud-settings]'))cloudSpeechUI.settings().catch(e=>toast(e.message));});
$('cloud-service-list').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,button=form.querySelector('[type=submit]'),message=form.querySelector('[data-cloud-message]');
  button.disabled=true;message.textContent='正在验证连接…';
  try{await send('/api/cloud-speech/'+form.dataset.cloudProvider,{api_key:form.elements.api_key.value,region:form.elements.region.value},'PUT');form.elements.api_key.value='';populateModels(await api('/api/models'));await cloudSpeechUI.settings();await window.modelSettingsUI?.load();toast('连接成功，请在模型配置中启用云端语音模型。');}
  catch(e){message.textContent=e.message;}finally{button.disabled=false;}
});
$('cloud-service-list').addEventListener('click',async event=>{
  const button=event.target.closest('[data-cloud-disconnect]');if(!button)return;button.disabled=true;
  try{await api('/api/cloud-speech/'+button.closest('form').dataset.cloudProvider,{method:'DELETE'});populateModels(await api('/api/models'));await cloudSpeechUI.settings();await window.modelSettingsUI?.load();}
  catch(e){toast(e.message);}finally{button.disabled=false;}
});
