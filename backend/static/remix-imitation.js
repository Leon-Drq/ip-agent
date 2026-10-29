'use strict';
class ImitationEditor {
  constructor(hooks){
    this.hooks=hooks;this.plan=null;this.reused=[];this.background='';this.scene='';this.approved=false;this.key='';this.photoRole='';
    $('imitation-scene').oninput=()=>{this.scene=$('imitation-scene').value;this.changed();};
    $('imitation-approved').onchange=()=>{this.approved=$('imitation-approved').checked;this.hooks.change();};
    $('imitation-cast').oninput=e=>{const input=e.target;if(!input.dataset.castField)return;const c=this.plan.characters.find(c=>c.id===input.dataset.character);c[input.dataset.castField]=input.value;this.changed();};
    $('imitation-script').oninput=e=>{
      if(e.target.dataset.storyReuse!==undefined){const i=Number(e.target.dataset.storyReuse);this.reused=e.target.checked?[...this.reused,i]:this.reused.filter(x=>x!==i);this.hooks.change();return;}
      if(e.target.dataset.storyRole!==undefined){const s=this.plan.shots[Number(e.target.dataset.shot)],id=e.target.dataset.storyRole;s.character_ids=e.target.checked?[...s.character_ids,id]:s.character_ids.filter(x=>x!==id);this.changed();return;}
      const field=e.target.dataset.storyField;if(!field)return;
      if(field==='summary')this.plan.summary=e.target.value;
      else {const s=this.plan.shots[Number(e.target.dataset.shot)];s[field]=field==='seconds'?Number(e.target.value):e.target.value;}
      this.changed();
    };
    $('imitation-cast').onclick=e=>{
      const existing=e.target.closest('[data-existing-character]');
      if(existing){this.hooks.action(async()=>{const c=this.plan.characters.find(c=>c.id===existing.dataset.existingCharacter);c.photo_id=this.hooks.project().person_id;c.appearance='以人物照片的五官、发型、年龄与性别为准';this.changed();this.render(true);await this.hooks.save();});return;}
      const button=e.target.closest('[data-character-photo]');if(!button)return;
      this.photoRole=button.dataset.characterPhoto;$('imitation-character-file').click();
    };
    $('imitation-character-file').onchange=()=>this.hooks.action(async()=>{
      const file=$('imitation-character-file').files[0];if(!file)return;
      const meta=await this.upload(file,'person');const character=this.plan.characters.find(c=>c.id===this.photoRole);character.photo_id=meta.id;character.appearance='以人物照片的五官、发型、年龄与性别为准';
      this.changed();this.render(true);await this.hooks.save();$('imitation-character-file').value='';
    });
    $('imitation-background-upload').onclick=()=>$('imitation-background-file').click();
    $('imitation-background-file').onchange=()=>this.hooks.action(async()=>{
      const file=$('imitation-background-file').files[0];if(!file)return;
      this.background=(await this.upload(file,'background')).id;this.changed();this.render(true);await this.hooks.save();$('imitation-background-file').value='';
    });
    $('imitation-background-remove').onclick=()=>{this.background='';this.changed();this.render(true);};
    $('imitation-continue').onclick=()=>this.hooks.action(async()=>{
      const p=this.hooks.project();this.hooks.load(await send(`/api/remixes/${p.id}/storyboard/approve`,{}));
    });
  }
  async upload(file,role){
    if(file.size>20*1024**2)throw new Error('照片最大 20 MB。');
    const form=new FormData();form.append('role',role);form.append('file',file);return api('/api/remixes/assets',{method:'POST',body:form});
  }
  load(p){this.reused=[...(p?.reuse_shots||[])];this.plan=p?.screenplay?structuredClone(p.screenplay):null;this.background=p?.background_id||'';this.scene=p?.imitation_scene||'';this.approved=!!p?.screenplay_approved;this.key='';}
  values(){return {screenplay:this.plan,background_id:this.background,imitation_scene:this.scene,screenplay_approved:this.approved};}
  changed(){this.approved=false;$('imitation-approved').checked=false;this.hooks.change();}
  render(force=false){
    const p=this.hooks.project(),enabled=this.hooks.enabled();
    $('imitation-setup').hidden=!enabled;$('imitation-script-panel').hidden=!enabled;
    const key=JSON.stringify([p?.id,!!this.plan,this.background]);
    if(force||key!==this.key){
      this.key=key;$('imitation-empty').hidden=!!this.plan;
      $('imitation-cast').innerHTML=this.plan?this.plan.characters.map(c=>`<article class="imitation-character"><div class="imitation-character-head"><button type="button" class="imitation-photo" data-character-photo="${c.id}" aria-label="上传${esc(c.name)}照片">${c.photo_id?`<img src="/api/remixes/assets/${c.photo_id}/poster" alt="${esc(c.name)}">`:icon('person')}<span>${c.photo_id?'更换照片':'上传照片'}</span></button><div><label>角色名称<input data-character="${c.id}" data-cast-field="name" value="${esc(c.name)}" maxlength="40"></label><small>${c.photo_id?'身份已固定为上传人物':'未上传照片时，按角色设定生成'}</small>${!c.photo_id&&p?.person_id?`<button type="button" class="link-button" data-existing-character="${c.id}">使用之前上传的人物照片</button>`:''}</div></div><label>人物设定<textarea data-character="${c.id}" data-cast-field="appearance" maxlength="800" rows="2">${esc(c.appearance)}</textarea></label><label>基础服装<input data-character="${c.id}" data-cast-field="wardrobe" value="${esc(c.wardrobe)}" maxlength="400" placeholder="如白色衬衫、黑色长裤；剧情换装在分镜中描述"></label></article>`).join(''):'';
      $('imitation-background-thumb').innerHTML=this.background?`<img src="/api/remixes/assets/${this.background}/poster" alt="门店背景">`:icon('home');
      $('imitation-background-name').textContent=this.background?'更换门店照片':'上传你的门店照片';$('imitation-background-remove').hidden=!this.background;
      $('imitation-scene').value=this.scene;
      $('imitation-script').innerHTML=this.plan?`<label>故事结构<textarea data-story-field="summary" maxlength="2000" rows="3">${esc(this.plan.summary)}</textarea></label>`+this.plan.shots.map(s=>`<details class="imitation-scene-card" ${s.index===0?'open':''}><summary>镜头 ${s.index+1}<span>${s.seconds} 秒 · ${esc(s.camera)}</span></summary><div class="imitation-scene-heading"><strong>镜头 ${s.index+1}</strong><label>时长 <input type="number" min="4" max="15" step="1" data-story-field="seconds" data-shot="${s.index}" value="${s.seconds}" aria-label="镜头 ${s.index+1} 时长"> 秒</label></div>${p?.reuse_source_mode==='imitate'?`<label class="imitation-reuse"><input type="checkbox" data-story-reuse="${s.index}" ${this.reused.includes(s.index)?'checked':''}>保留这个镜头的成片，不重新生成</label>`:''}<div class="imitation-present"><span>出场角色</span>${this.plan.characters.map(c=>`<label><input type="checkbox" data-story-role="${c.id}" data-shot="${s.index}" ${s.character_ids.includes(c.id)?'checked':''}>${esc(c.name)}</label>`).join('')}</div><label>景别与镜头<input data-story-field="camera" data-shot="${s.index}" maxlength="400" value="${esc(s.camera)}"></label><label>动作与互动<textarea data-story-field="action" data-shot="${s.index}" maxlength="1600" rows="3">${esc(s.action)}</textarea></label><details class="imitation-look" ${(s.look_start||s.look_end)?'open':''}><summary>本镜头造型 · 戴假发、换装等变化</summary><label>开始时的造型<textarea data-story-field="look_start" data-shot="${s.index}" maxlength="1200" rows="2" placeholder="如：顾客是灰白色极短发，尚未戴假发">${esc(s.look_start||'')}</textarea></label><label>结束时的造型<textarea data-story-field="look_end" data-shot="${s.index}" maxlength="1200" rows="2" placeholder="如：已戴好棕色盘发假发，脸和衣服保持不变">${esc(s.look_end||'')}</textarea></label></details><label>角色台词<textarea data-story-field="dialogue" data-shot="${s.index}" maxlength="1600" rows="3" placeholder="角色名：台词">${esc(s.dialogue)}</textarea></label></details>`).join(''):'<div class="imitation-empty">反推完成后，这里会出现可编辑的故事与分镜。</div>';
      $('imitation-approval-wrap').hidden=!this.plan;$('imitation-approved').checked=this.approved;
    }
    $('imitation-script-count').textContent=this.plan?`${this.plan.shots.length} 个镜头 · 约 ${this.plan.shots.reduce((n,s)=>n+s.seconds,0)} 秒`:'等待反推';
    const boards=p?.storyboard||[];
    $('imitation-board-panel').hidden=!enabled||!boards.length;
    const boardKey=JSON.stringify([p?.run_id,boards]);
    if(this.boardKey!==boardKey){this.boardKey=boardKey;$('imitation-board').innerHTML=boards.map(b=>`<figure><a href="${esc(b.url)}" target="_blank" rel="noopener"><img src="${esc(b.url)}" alt="${b.index<0?'统一定妆图':'分镜 '+(b.index+1)}" loading="lazy"></a><figcaption>${b.index<0?'统一定妆图':'镜头 '+(b.index+1)}</figcaption></figure>`).join('');}
    $('imitation-board-status').textContent=p?.status==='storyboard'?'等待你确认':'';
    $('imitation-continue').hidden=p?.status!=='storyboard';
    const locked=this.hooks.locked();
    for(const el of document.querySelectorAll('#imitation-setup input,#imitation-setup textarea,#imitation-setup button,#imitation-script-panel input,#imitation-script-panel textarea'))el.disabled=locked||el.dataset.storyReuse===undefined&&el.dataset.shot!==undefined&&this.reused.includes(Number(el.dataset.shot));
  }
}
