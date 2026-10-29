'use strict';
// A shared form used by the editor, saved voice profiles, and voice previews.
class SpeechControls {
  constructor(id, modelSelect, onBusy) {
    this.root=document.getElementById(id);this.modelSelect=modelSelect;this.onBusy=onBusy;
    this.uploading=false;this.token=0;
    this.root.innerHTML=`<fieldset class="speech-settings"><legend>情感表达</legend>
      <div class="options-row"><label>表达方式<select data-field="mode" aria-label="表达方式"></select></label><label data-wrap="preset">情感预设<select data-field="preset" aria-label="情感预设"></select></label></div>
      <label data-wrap="strength">情感强度 <output data-field="value">50%</output><input data-field="strength" aria-label="情感强度" type="range" min="0" max="1" step="0.05" value="0.5"></label>
      <div data-wrap="audio"><label>情绪参考录音<select data-field="source" aria-label="情绪参考录音"></select></label>
        <button class="button" type="button" data-field="upload">上传情绪录音</button><input data-field="file" type="file" accept="audio/*,video/mp4" hidden>
        <audio data-field="player" controls preload="none" hidden></audio></div>
      <p class="model-hint" data-field="hint"></p><p class="error-text" data-field="error" role="alert" hidden></p>
    </fieldset>`;
    this.field=name=>this.root.querySelector(`[data-field="${name}"]`);
    this.wrap=name=>this.root.querySelector(`[data-wrap="${name}"]`);
    this.field('mode').onchange=()=>this.refresh();
    this.field('strength').oninput=()=>this.refresh();
    this.field('source').onchange=()=>this.setAudio(this.field('source').value);
    this.field('upload').onclick=()=>this.field('file').click();
    this.field('file').onchange=()=>this.upload(this.field('file').files[0]);
    this.audioId='';
  }
  get model(){return document.getElementById(this.modelSelect).value;}
  refresh(values=null){
    const caps=engines.tts?.find(m=>m.id===this.model)?.emotion||{modes:[{id:'reference',name:'跟随原声'}],presets:[],strength:false};
    const previous=values||this.read();
    for(const [name,items,wanted] of [['mode',caps.modes,previous.emotion_mode],['preset',caps.presets,previous.emotion_preset]]){
      const key=JSON.stringify(items);if(this.field(name).dataset.key!==key){this.field(name).replaceChildren(...items.map(m=>new Option(m.name,m.id)));this.field(name).dataset.key=key;}
      this.field(name).value=items.some(m=>m.id===wanted)?wanted:(items[0]?.id||'');
    }
    if(values){this.token++;this.field('strength').value=values.emotion_strength??.5;this.setAudio(values.emotion_audio_id||'');this.field('error').hidden=true;}
    const mode=this.field('mode').value;
    this.root.hidden=caps.modes.length<2;
    this.wrap('preset').hidden=mode!=='preset';this.wrap('strength').hidden=!caps.strength||mode==='reference';this.wrap('audio').hidden=mode!=='audio';
    this.field('value').value=Math.round(Number(this.field('strength').value)*100)+'%';
    this.field('hint').textContent={reference:'保留参考录音中的表达习惯。',preset:this.model==='indextts2'?'先从 50% 强度试听；较高强度可能影响音色相似度。':'按所选情感生成，实际表现会随录音和文案变化。',auto:'由 IndexTTS 2 根据口播文案判断情感。',audio:'用另一段 3–10 秒录音提供情绪，音色仍来自当前克隆声音。'}[mode]||'';
    if(mode==='reference'&&engines.tts?.find(m=>m.id===this.model)?.cloud)this.field('hint').textContent='由云端模型结合文案自然表达；可切换情感预设。';
    if(mode==='audio')this.sources();
  }
  sources(){
    const select=this.field('source'),used=new Set(),items=[new Option('请选择或上传情绪录音','')];
    for(const voice of state.voices){if(!used.has(voice.audio_id)){items.push(new Option(voice.name+' · 参考录音',voice.audio_id));used.add(voice.audio_id);}}
    if(this.audioId&&!used.has(this.audioId))items.push(new Option(this.audioName||'已保存的情绪录音',this.audioId));
    select.replaceChildren(...items);select.value=this.audioId;
  }
  setAudio(id,name=''){
    this.audioId=id;this.audioName=name;const player=this.field('player');
    const src=id?mediaUrl(id):'';if(player.getAttribute('src')!==src){player.pause();if(src)player.src=src;else player.removeAttribute('src');}
    player.hidden=!id;this.sources();
  }
  read(){return {emotion_mode:this.field('mode').value||'reference',emotion_preset:this.field('preset').value||'happy',emotion_strength:Number(this.field('strength').value),emotion_audio_id:this.audioId||''};}
  valid(){
    const error=this.uploading?'情绪录音正在上传，请稍候。':this.read().emotion_mode==='audio'&&!this.audioId?'请先选择或上传情绪参考录音。':'';
    this.field('error').textContent=error;this.field('error').hidden=!error;return !error;
  }
  lock(value){this.root.querySelector('fieldset').disabled=!!value||this.uploading;}
  async upload(file){
    if(!file||this.uploading)return;
    const token=this.token;this.uploading=true;this.lock(true);this.onBusy();this.field('error').hidden=true;this.field('upload').textContent='正在导入…';
    try{const meta=await uploadMedia(file,'audio');if(token===this.token)this.setAudio(meta.id,meta.name);}
    catch(error){if(token===this.token){this.field('error').textContent=error.message;this.field('error').hidden=false;}}
    finally{this.uploading=false;this.field('file').value='';this.field('upload').textContent='上传情绪录音';this.lock(false);this.onBusy();}
  }
}
