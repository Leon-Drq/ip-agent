'use strict';
(() => {
  const ui=id=>document.getElementById('extract-'+id),video=ui('video'),track=ui('timeline');
  let current=null,duration=0,start=0,end=0,previewing=false,submitting=false,loading=false,clips=[],autoDownload=null,drag=null;
  const ranges=new Map();
  function clock(seconds){const cs=Math.round(Math.max(0,seconds)*100),s=Math.floor(cs/100);return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}.${(cs%100).toString().padStart(2,'0')}`;}
  const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));
  const rounded=value=>Math.round(value*1000)/1000;
  function valid(){return duration>0&&start>=0&&end<=duration+.001&&end-start>=.0999;}
  function problem(message=''){ui('trim-error').textContent=message;ui('trim-error').hidden=!message;}
  function stop(){previewing=false;ui('trim-play').textContent='预览片段';}
  function remember(){if(current)ranges.set(current.id,{start,end});}
  function seek(position){if(video.readyState>=1)video.currentTime=clamp(position,0,video.duration||duration);}
  function playhead(){ui('trim-playhead').style.left=(duration?clamp(video.currentTime/duration*100,0,100):0)+'%';}
  function controls(){
    const available=!!current?.video_ready&&duration>=.1,left=duration?start/duration*100:0,right=duration?end/duration*100:100;
    for(const point of ['start','end']){
      const handle=ui('trim-'+point+'-handle'),value=point==='start'?start:end;
      handle.disabled=!available;handle.style.left=(point==='start'?left:right)+'%';
      handle.setAttribute('aria-valuemin',point==='start'?0:rounded(start+.1));
      handle.setAttribute('aria-valuemax',point==='start'?rounded(Math.max(0,end-.1)):duration);
      handle.setAttribute('aria-valuenow',value);handle.setAttribute('aria-valuetext',clock(value));
      ui('trim-'+point+'-time').textContent=clock(value);
    }
    ui('trim-before').style.width=left+'%';ui('trim-after').style.width=(100-right)+'%';
    ui('trim-window').style.left=left+'%';ui('trim-window').style.width=(right-left)+'%';
    ui('trim-play').disabled=!available||!valid();ui('trim-export').disabled=!available||!valid()||submitting;
    ui('trim-export').textContent=submitting?'正在提交…':'导出片段';
    ui('trim-duration').textContent=available?`选中 ${clock(end-start)}`:'加载视频…';playhead();
  }
  function setPoint(point,value){
    stop();video.pause();problem();
    if(point==='start')start=rounded(clamp(value,0,Math.max(0,end-.1)));
    else end=rounded(clamp(value,Math.min(duration,start+.1),duration));
    remember();controls();seek(point==='start'?start:end);
  }
  function position(event){const box=track.getBoundingClientRect();return clamp((event.clientX-box.left)/box.width,0,1)*duration;}
  track.addEventListener('pointerdown',event=>{
    if(event.button!==0||!current?.video_ready||duration<.1)return;
    const handle=event.target.closest('[role=slider]');
    const mode=handle?(handle.id.includes('start')?'start':'end'):event.target===ui('trim-window')?'move':null;
    stop();video.pause();
    if(!mode){seek(position(event));return;}
    event.preventDefault();if(handle)handle.focus();
    drag={mode,x:position(event),start,end,pointer:event.pointerId};track.setPointerCapture(event.pointerId);track.classList.add('dragging');
  });
  track.addEventListener('pointermove',event=>{
    if(!drag||drag.pointer!==event.pointerId)return;
    if(drag.mode==='move'){
      const delta=clamp(position(event)-drag.x,-drag.start,duration-drag.end);
      start=rounded(drag.start+delta);end=rounded(drag.end+delta);remember();controls();seek(start);
    }else setPoint(drag.mode,position(event));
  });
  function release(event){if(drag?.pointer!==event.pointerId)return;drag=null;track.classList.remove('dragging');if(track.hasPointerCapture(event.pointerId))track.releasePointerCapture(event.pointerId);}
  track.addEventListener('pointerup',release);track.addEventListener('pointercancel',release);
  for(const point of ['start','end'])ui('trim-'+point+'-handle').addEventListener('keydown',event=>{
    let value=point==='start'?start:end,step=event.shiftKey?1:.1;
    if(['ArrowLeft','ArrowDown'].includes(event.key))value-=step;
    else if(['ArrowRight','ArrowUp'].includes(event.key))value+=step;
    else if(event.key==='Home')value=point==='start'?0:start+.1;
    else if(event.key==='End')value=point==='start'?end-.1:duration;
    else return;
    event.preventDefault();setPoint(point,value);
  });
  function thumbnails(){if(current?.video_ready&&ui('preview').open){const src=`/api/extractions/${current.id}/timeline`;if(ui('trim-frames').getAttribute('src')!==src){ui('trim-frames').classList.remove('ready');ui('trim-frames').src=src;}}}
  ui('trim-frames').onload=()=>ui('trim-frames').classList.add('ready');
  ui('trim-frames').onerror=()=>ui('trim-frames').classList.remove('ready');
  ui('preview').addEventListener('toggle',()=>{
    if(ui('preview').open&&current?.video_ready){if(video.preload==='none'){video.preload='metadata';video.load();}thumbnails();}
    else if(previewing){video.pause();stop();}
  });
  video.addEventListener('loadedmetadata',()=>{if(!duration&&current&&Number.isFinite(video.duration)){duration=Math.floor(video.duration*1000)/1000;end=duration;controls();}});
  video.addEventListener('timeupdate',()=>{if(previewing&&video.currentTime>=end){video.pause();seek(end);stop();}playhead();});
  video.addEventListener('ended',stop);video.addEventListener('pause',stop);
  ui('trim-play').onclick=async()=>{
    if(previewing){video.pause();stop();return;}if(!valid())return;
    try{video.currentTime=start;previewing=true;ui('trim-play').textContent='停止预览';await video.play();}catch{stop();problem('视频暂时无法播放，请稍后再试。');}
  };
  function renderClips(){
    const active=clips.filter(c=>['queued','running'].includes(c.status));
    ui('trim-status').hidden=!active.length;ui('trim-status').textContent=active.length?`正在导出 ${active.length} 个片段，完成后自动下载…`:'';
    ui('clip-history').hidden=!clips.length;ui('clip-count').textContent=clips.length;
    ui('clips').innerHTML=clips.map(c=>`<div class="extract-clip"><div><strong>${clock(c.start)} → ${clock(c.end)}</strong><small>${c.status==='completed'?'已保存 · '+clock(c.duration):c.status==='running'?'正在导出…':c.status==='queued'?'等待导出…':esc(c.error||'导出失败，请重新截取')}</small></div>${c.status==='completed'?`<a class="button" href="/api/extractions/${current.id}/clips/${c.id}/media?download=true" download>下载片段</a>`:''}</div>`).join('');
  }
  function download(tid,cid){const a=document.createElement('a');a.href=`/api/extractions/${tid}/clips/${cid}/media?download=true`;a.download='';document.body.append(a);a.click();a.remove();}
  async function refresh(){
    if(loading||!current?.video_ready)return;loading=true;const tid=current.id;
    try{
      const rows=await api(`/api/extractions/${tid}/clips`);if(current?.id!==tid)return;clips=rows;renderClips();
      if(autoDownload?.tid===tid){const row=rows.find(c=>c.id===autoDownload.cid);if(row?.status==='completed'){autoDownload=null;if(state.route==='extract'){download(tid,row.id);toast('片段已导出，已开始下载。');}}else if(row?.status==='failed'){autoDownload=null;problem(row.error||'导出失败，请重试。');}}
    }catch(e){if(current?.id===tid)problem(e.message);}finally{loading=false;}
  }
  ui('trim-export').onclick=async()=>{
    if(submitting||!current||!valid())return;const tid=current.id;submitting=true;controls();problem();
    try{
      const row=await send(`/api/extractions/${tid}/clips`,{start,end});
      if(current?.id===tid){clips=[row,...clips.filter(c=>c.id!==row.id)];renderClips();if(row.status==='completed')download(tid,row.id);else autoDownload={tid,cid:row.id};}
    }catch(e){if(current?.id===tid)problem(e.message);}finally{submitting=false;controls();}
  };
  window.addEventListener('studio-extraction-selected',event=>{
    const next=event.detail;if(next?.id===current?.id&&next?.video_ready===current?.video_ready)return;
    stop();drag=null;autoDownload=null;current=next;duration=Math.floor((Number(next?.duration)||0)*1000)/1000;
    const previous=next&&ranges.get(next.id);start=previous?.start||0;end=previous?.end??duration;clips=[];problem();
    if(next){ui('full-video').href=`/api/extractions/${next.id}/media?original=true`;renderClips();}else ui('clips').replaceChildren();
    ui('clip-history').open=false;controls();thumbnails();refresh();
  });
  window.addEventListener('studio-route',event=>{if(event.detail!=='extract'){autoDownload=null;if(previewing)video.pause();stop();}else refresh();});
  async function poll(){if(state.route==='extract'&&!document.hidden)await refresh();setTimeout(poll,1500);}
  controls();poll();
})();
