'use strict';
(() => {
  const ui = id => document.getElementById('extract-' + id);
  const labels = {queued:'等待提取',running:'正在提取',completed:'已保存',failed:'提取失败',cancelled:'已停止'};
  let history=[], selected=null, task=null, loading=false, submitting=false, dirty=false, historyKey='', savePromise=null;
  const active = value => value && ['queued','running'].includes(value.status);
  function error(message=''){ui('input-error').textContent=message;ui('input-error').hidden=!message;}
  function controls(){
    ui('start').disabled=submitting;ui('upload').disabled=submitting;
    ui('start').textContent=submitting?'正在准备…':'开始提取文案';
    ui('save').disabled=!!savePromise||!dirty;
    ui('save-state').textContent=savePromise?'正在保存…':dirty?'有未保存的修改':task?.status==='completed'?'已自动保存到本机':'纯文字 · 不带时间戳';
    ui('word-count').textContent=ui('text').value.length+' 字';
  }
  function drawHistory(){
    ui('history-count').textContent=history.length;
    const key=JSON.stringify([selected,history.map(x=>[x.id,x.title,x.status])]);if(key===historyKey)return;historyKey=key;
    ui('history').innerHTML=history.length?history.map(x=>`<button class="extract-history-item${x.id===selected?' active':''}" data-extract-id="${x.id}" ${x.id===selected?'aria-current="true"':''}><strong>${esc(x.title)}</strong><small>${esc(labels[x.status]||x.status)} · ${esc(stamp(x.created_at))}</small></button>`).join(''):'<p class="extract-hint">提取的文案会保存在这里，随时打开继续使用。</p>';
  }
  function drawTask(loadText=false){
    ui('empty').hidden=!!task;ui('task').hidden=!task;
    if(!task){controls();window.dispatchEvent(new CustomEvent('studio-extraction-selected',{detail:null}));return;}
    ui('progress').hidden=!active(task);ui('failure').hidden=!['failed','cancelled'].includes(task.status);
    ui('document').hidden=task.status!=='completed';ui('preview').hidden=!task.video_ready;
    ui('progress-label').textContent=task.message;ui('progress-percent').textContent=Math.round(task.progress)+'%';
    ui('progress-fill').style.width=task.progress+'%';ui('progress-bar').setAttribute('aria-valuenow',task.progress);
    ui('failure-message').textContent=task.error||task.message;
    if(task.video_ready){const src=`/api/extractions/${task.id}/media`;if(ui('video').getAttribute('src')!==src)ui('video').src=src;}
    else{ui('video').removeAttribute('src');ui('preview').open=false;}
    if(loadText&&task.status==='completed'){ui('title').value=task.title;ui('text').value=task.text;dirty=false;}
    controls();
    window.dispatchEvent(new CustomEvent('studio-extraction-selected',{detail:{id:task.id,duration:task.duration,video_ready:task.video_ready}}));
  }
  function putRecord(value){history=[value,...history.filter(x=>x.id!==value.id)].sort((a,b)=>b.created_at.localeCompare(a.created_at));drawHistory();}
  async function save(){
    if(savePromise)return savePromise;
    if(!task||task.status!=='completed'||!dirty)return task;
    const current=task, title=ui('title').value.trim(), text=ui('text').value.trim();
    if(!title||!text)throw new Error('标题和文案不能为空。');
    savePromise=send(`/api/extractions/${current.id}`,{revision:current.revision,title,text},'PUT');controls();
    try{
      const result=await savePromise;putRecord(result);
      if(selected===result.id){task=result;dirty=ui('text').value.trim()!==text||ui('title').value.trim()!==title;}
      return result;
    }finally{savePromise=null;controls();}
  }
  async function choose(id){
    await save();selected=id;drawHistory();
    const value=await api(`/api/extractions/${id}`);
    if(selected!==id)return;task=value;dirty=false;error();drawTask(true);
  }
  async function start(values){
    if(submitting)return;
    await save();submitting=true;error();controls();
    try{const value=await send('/api/extractions',values);selected=value.id;task=value;dirty=false;putRecord(value);drawTask();}
    finally{submitting=false;controls();}
  }
  ui('form').onsubmit=async event=>{event.preventDefault();const share=ui('share').value.trim();if(!share){error('先粘贴一个抖音视频链接。');ui('share').focus();return;}try{await start({share});}catch(e){error(e.message);}};
  ui('upload').onclick=()=>ui('file').click();
  ui('file').onchange=async()=>{
    const file=ui('file').files[0];if(!file||submitting)return;submitting=true;error();controls();ui('start').textContent='正在上传视频…';
    try{await save();const media=await uploadMedia(file,'video');submitting=false;await start({video_id:media.id});}
    catch(e){error(e.message);}finally{submitting=false;ui('file').value='';controls();}
  };
  ui('history').onclick=event=>{const button=event.target.closest('[data-extract-id]');if(button)choose(button.dataset.extractId).catch(e=>error(e.message));};
  ui('cancel').onclick=async()=>{if(!task)return;try{task=await send(`/api/extractions/${task.id}/cancel`,{});putRecord(task);drawTask();}catch(e){error(e.message);}};
  ui('retry').onclick=async()=>{
    if(!task||submitting)return;submitting=true;controls();error();
    try{const result=await send(`/api/extractions/${task.id}/retry`,{});task=result;selected=result.id;dirty=false;putRecord(result);drawTask();}
    catch(e){error(e.message);}finally{submitting=false;controls();}
  };
  for(const name of ['title','text'])ui(name).oninput=()=>{dirty=true;controls();};
  ui('save').onclick=()=>save().then(()=>toast('文案已保存。')).catch(e=>error(e.message));
  ui('copy').onclick=async()=>{
    try{await navigator.clipboard.writeText(ui('text').value);toast('文案已复制。');}
    catch{ui('text').select();toast('请按 Ctrl+C 复制已选中的文案。');}
  };
  ui('download').onclick=async()=>{
    try{await save();const link=document.createElement('a');link.href=`/api/extractions/${selected}/export/txt`;link.download='';link.click();}
    catch(e){error(e.message);}
  };
  ui('use').onclick=async()=>{
    try{
      const text=ui('text').value.trim();if(!text)return;
      if(text.length>8000){error('创作视频最多支持 8000 字，请先精简这份文案。');return;}
      await save();newCreation();$('script').value=text;countText();$('script').focus();toast('文案已带入创作页，选择声音和数字人即可生成。');
    }catch(e){error(e.message);}
  };
  async function refreshExtraction(){
    if(loading||savePromise||submitting)return;loading=true;
    try{
      history=await api('/api/extractions');drawHistory();
      if(!selected&&history.length){await choose(history[0].id);return;}
      const row=history.find(x=>x.id===selected);
      if(row&&(!task||row.status!==task.status||row.updated_at!==task.updated_at||row.revision!==task.revision)){
        const id=selected,value=await api(`/api/extractions/${selected}`);if(id!==selected||savePromise)return;
        if(dirty){if(task?.revision!==value.revision)ui('save-state').textContent='其他窗口有更新，请先保留当前修改';return;}
        const loadText=!task||task.status!=='completed'||task.revision!==value.revision;task=value;drawTask(loadText);
      }
    }catch(e){error(e.message);}finally{loading=false;}
  }
  window.addEventListener('studio-route',event=>{if(event.detail==='extract')refreshExtraction();});
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  async function poll(){if(state.route==='extract'&&!document.hidden)await refreshExtraction();setTimeout(poll,2200);}
  drawHistory();drawTask();poll();
})();
