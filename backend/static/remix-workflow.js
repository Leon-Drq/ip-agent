'use strict';
class RemixWorkflow {
  constructor(hooks){
    this.hooks=hooks;this.root=$('remix-workflow');this.project=null;this.selected=0;this.tab='boards';this.mediaKey='';this.galleryKey='';this.attention='';
    this.root.innerHTML=`
      <ol class="workflow-steps" aria-label="创作步骤"><li><b>1</b><span>剧本与角色</span></li><li><b>2</b><span>生成分镜</span></li><li><b>3</b><span>确认分镜</span></li><li><b>4</b><span>生成视频</span></li></ol>
      <div class="workflow-heading"><div><span id="workflow-badge" class="workflow-badge"></span><h2 id="workflow-title" tabindex="-1"></h2><p id="workflow-description" aria-live="polite"></p></div><div class="workflow-counts"><span><b id="workflow-images-count"></b>分镜已保存</span><span><b id="workflow-videos-count"></b>视频已保存</span></div></div>
      <div class="workflow-body">
        <section class="workflow-viewer" aria-label="分镜预览">
          <div class="workflow-viewer-top"><div class="workflow-tabs" role="group" aria-label="预览内容"><button type="button" data-workflow-tab="boards" aria-pressed="true">分镜</button><button type="button" data-workflow-tab="reference" aria-pressed="false">原片</button><button type="button" data-workflow-tab="result" aria-pressed="false" hidden>成片</button></div><button type="button" class="link-button" id="workflow-enlarge">查看大图 ↗</button></div>
          <div class="workflow-canvas"><img id="workflow-image" alt="当前分镜" hidden><video id="workflow-video" controls preload="metadata" hidden></video><div id="workflow-empty" class="workflow-empty"></div><span id="workflow-image-label" class="workflow-image-label"></span></div>
          <div id="workflow-gallery" class="workflow-gallery" aria-label="选择分镜"></div>
        </section>
        <aside class="workflow-inspector">
          <div class="workflow-inspector-heading"><span id="workflow-shot-number"></span><h3 id="workflow-shot-title"></h3></div>
          <div id="workflow-shot-copy"></div>
          <div id="workflow-review-guide" class="workflow-review-guide"><strong>确认前，看这三处</strong><p>人物身份是否一致、造型是否符合剧情、门店是否正确。</p><small>点击下方缩略图逐镜查看，图片可以放大。</small></div>
          <button class="button" type="button" id="workflow-repair" hidden>修正分镜…</button>
          <button class="button" type="button" id="workflow-reuse-image" hidden>沿用上一镜画面 · 免费</button>
          <div id="workflow-connection" class="workflow-connection" role="status" hidden></div>
          <details id="workflow-error-detail" class="workflow-detail" hidden><summary>查看中断详情</summary><p id="workflow-error-original"></p></details>
        </aside>
      </div>
      <div class="workflow-dock"><div class="workflow-dock-copy"><strong id="workflow-next-title"></strong><p id="workflow-next-description"></p><p id="workflow-error" role="alert" hidden></p></div><div class="workflow-dock-buttons"><button type="button" class="button" id="workflow-settings">查看项目设置</button><button type="button" class="button" id="workflow-pause" hidden>暂停任务</button><button type="button" class="button dark" id="workflow-primary"></button><a class="button dark" id="workflow-download" hidden>下载视频</a></div></div>`;
    const dialog=document.createElement('dialog');dialog.id='workflow-lightbox';dialog.className='workflow-lightbox';dialog.setAttribute('aria-label','放大查看分镜');
    dialog.innerHTML='<header><strong id="workflow-lightbox-title"></strong><button class="button" type="button" id="workflow-lightbox-close" aria-label="关闭大图">关闭 ×</button></header><img id="workflow-lightbox-image" alt="放大的分镜"><footer><button class="button" type="button" id="workflow-lightbox-previous">← 上一张</button><span id="workflow-lightbox-count"></span><button class="button" type="button" id="workflow-lightbox-next">下一张 →</button></footer>';
    document.body.append(dialog);this.dialog=dialog;
    $('workflow-lightbox-close').onclick=()=>dialog.close();
    $('workflow-lightbox-previous').onclick=()=>this.move(-1);
    $('workflow-lightbox-next').onclick=()=>this.move(1);
    dialog.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();this.move(e.key==='ArrowLeft'?-1:1);}});
    $('workflow-enlarge').onclick=()=>{this.drawLightbox();if(!dialog.open)dialog.showModal();};
    $('workflow-image').onclick=()=>$('workflow-enlarge').click();
    $('workflow-gallery').onclick=e=>{const button=e.target.closest('[data-workflow-shot]');if(!button)return;this.selected=Number(button.dataset.workflowShot);this.tab='boards';this.drawMedia();};
    this.root.querySelector('.workflow-tabs').onclick=e=>{const b=e.target.closest('[data-workflow-tab]');if(b){this.tab=b.dataset.workflowTab;this.drawMedia();}};
    $('workflow-settings').onclick=()=>this.settings(true);
    $('workflow-reuse-image').onclick=()=>this.hooks.action(async()=>{
      const p=this.hooks.project(),target=this.selected;
      this.hooks.load(await send(`/api/remixes/${p.id}/storyboard/reuse`,{source_index:target-1,target_index:target}));
    });
    $('workflow-back').onclick=()=>this.settings(false);
    $('workflow-primary').onclick=()=>{
      const p=this.hooks.project();if(!p)return;
      if(p.status==='storyboard')this.hooks.action(async()=>this.hooks.load(await send(`/api/remixes/${p.id}/storyboard/approve`,{})));
      else if(p.can_resume)this.hooks.action(async()=>this.hooks.load(await send(`/api/remixes/${p.id}/resume`,{})));
    };
    $('workflow-pause').onclick=()=>this.hooks.action(async()=>{
      const p=this.hooks.project();this.hooks.load(await send(`/api/remixes/${p.id}/pause`,{}));
    });
    const repair=document.createElement('dialog');repair.id='workflow-repair-dialog';repair.className='workflow-repair-dialog';repair.setAttribute('aria-label','修正选定分镜');
    repair.innerHTML='<h2>只修正有问题的分镜</h2><p>选中的图片会重新生成。原图会保留，其他分镜不变。</p><div id="workflow-repair-shots"></div><label for="workflow-repair-note">需要改哪里</label><textarea id="workflow-repair-note" rows="5" maxlength="2000" placeholder="例如：顾客此时已经戴好棕色假发，店员手里不再拿假发。人物、衣服和门店保持不变。"></textarea><p id="workflow-repair-cost" role="status"></p><p id="workflow-repair-error" role="alert"></p><footer><button class="button" id="workflow-repair-close">取消</button><button class="button dark" id="workflow-repair-submit">查看修正费用</button></footer>';
    document.body.append(repair);this.repairDialog=repair;
    $('workflow-repair-close').onclick=()=>repair.close();
    const invalidate=()=>{this.repairQuote=null;$('workflow-repair-cost').textContent='';$('workflow-repair-submit').textContent='查看修正费用';};
    repair.addEventListener('input',invalidate);
    $('workflow-repair').onclick=()=>{
      invalidate();$('workflow-repair-note').value='';$('workflow-repair-error').textContent='';
      $('workflow-repair-shots').innerHTML=(this.project.storyboard||[]).filter(b=>b.index>=0).map(b=>`<label><input type="checkbox" value="${b.index}" ${b.index===this.selected?'checked':''}>镜头 ${b.index+1}</label>`).join('');repair.showModal();$('workflow-repair-note').focus();
    };
    $('workflow-repair-submit').onclick=async()=>{
      const submit=$('workflow-repair-submit');submit.disabled=true;$('workflow-repair-error').textContent='';
      const fields=[...repair.querySelectorAll('input,textarea')];fields.forEach(el=>el.disabled=true);
      try{
        const p=this.hooks.project();
        if(this.repairQuote){
          const q=this.repairQuote;const updated=await send(`/api/remixes/${p.id}/storyboard/repair`,{quote_id:q.id,accepted_estimate:q.total});
          repair.close();this.repairQuote=null;this.hooks.load(updated);
        }else{
          const note=$('workflow-repair-note').value.trim(),indices=[...$('workflow-repair-shots').querySelectorAll('input:checked')].map(x=>x.value);
          if(!note||!indices.length)throw new Error('请选择分镜，并写明需要修改的内容。');
          const q=await send(`/api/remixes/${p.id}/storyboard/repair-quote`,{notes:Object.fromEntries(indices.map(i=>[i,note]))});
          this.repairQuote=q;$('workflow-repair-cost').textContent=`本次修正 ${indices.length} 张，预计追加 ${q.total} 积分。不会自动开始视频生成。`;
          submit.textContent='确认费用，修正分镜';
        }
      }catch(e){$('workflow-repair-error').textContent=e.message||String(e);}finally{submit.disabled=false;fields.forEach(el=>el.disabled=false);}
    };
  }
  settings(open){
    document.getElementById('page-remix').classList.toggle('show-project-settings',open);
    $('workflow-back').hidden=!open;
    (open?document.querySelector('#page-remix .remix-layout'):this.root).scrollIntoView({block:'start',behavior:'instant'});
  }
  model(p){
    const total=p.screenplay?.shots?.length||0,images=(p.storyboard||[]).filter(x=>x.index>=0).length;
    return p.workflow||{total,images_done:images,videos_done:p.video_ready?total:0,stage:p.video_ready?'complete':p.status==='storyboard'?'review':images===total?'video':'images',storyboard_approved:false};
  }
  render(p,enabled,working){
    const active=!!(enabled&&p?.run_id);this.root.hidden=!active;
    const page=$('page-remix');page.classList.toggle('has-workflow',active);
    if(!active){page.classList.remove('show-project-settings');$('workflow-back').hidden=true;return;}
    if(this.project?.id!==p.id){this.selected=0;this.tab=p.video_ready?'result':'boards';this.mediaKey='';this.galleryKey='';this.attention='';page.classList.remove('show-project-settings');$('workflow-back').hidden=true;}
    if(p.video_ready&&!this.project?.video_ready)this.tab='result';
    this.project=p;const w=this.model(p),review=p.status==='storyboard',failed=!!p.can_resume,complete=p.video_ready;
    const video=w.stage==='video'||w.stage==='complete',retrying=!failed&&w.connection?.state==='retrying',pausing=p.status==='generating'&&w.pause_requested;
    const current=w.stage==='images'?1:review?2:3;
    this.root.querySelectorAll('.workflow-steps li').forEach((el,i)=>{el.classList.toggle('done',i<current||complete);el.classList.toggle('current',i===current&&!complete);if(i===current&&!complete)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});
    this.root.dataset.state=review?'review':failed?'paused':complete?'complete':'running';
    $('workflow-badge').textContent=review?'需要你确认':p.status==='paused'?'已暂停':failed?'进度已保存':complete?'已完成':retrying?'正在重连':'正在制作';
    $('workflow-title').textContent=review?'分镜已就绪，确认后生成视频':failed?(video?'视频生成暂时中断':'分镜生成暂时中断'):complete?'你的新视频已完成':retrying?'连接波动，正在自动重连':video?'正在让分镜动起来':'正在生成角色与分镜';
    if(p.status==='paused')$('workflow-title').textContent='任务已暂停，随时可以继续';
    $('workflow-description').textContent=review?'先检查人物、造型变化和门店，再点击下方的确认按钮。':failed?`${w.images_done} 张分镜已保存${w.videos_done?`，${w.videos_done} 个视频镜头已保存`:''}。继续当前任务会复用已有素材。`:complete?'播放检查成片，满意后即可下载。':p.message||'任务正在进行，进度会自动更新。';
    $('workflow-images-count').textContent=`${w.images_done} / ${w.total}`;$('workflow-videos-count').textContent=`${w.videos_done} / ${w.total}`;
    $('workflow-next-title').textContent=review?'下一步：确认分镜，开始生成视频':failed?'从上次进度继续':complete?'成片已保存到本机':retrying?'正在恢复连接':'正在制作，进度会自动更新';
    $('workflow-next-description').textContent=review?(w.video_estimate?`视频阶段预计 ${w.video_estimate} 积分，包含在之前确认的总费用中。`:'使用之前已确认的生成费用，点击后开始生成动态视频。'):failed?'已完成内容保留，无需重新上传和反推剧本。':complete?'可在「我的作品」中再次查看和下载。':'生成分镜后会停下来等你确认。';
    if(video&&!failed&&!complete)$('workflow-next-description').textContent='分镜已确认。可暂停后续镜头，已经提交的镜头会保留。';
    const primary=$('workflow-primary');primary.hidden=complete;primary.disabled=working||!review&&!failed;primary.textContent=working?'正在处理…':review?'确认分镜，生成视频':failed?(video?'继续生成视频':'继续生成分镜'):retrying?'正在自动重连…':'正在生成…';
    $('workflow-download').hidden=!complete;$('workflow-download').href=`/api/remixes/${p.id}/result?download=true`;
    $('workflow-pause').hidden=p.status!=='generating';$('workflow-pause').disabled=working||pausing;$('workflow-pause').textContent=pausing?'正在暂停…':'暂停任务';
    $('workflow-repair').hidden=!(review||p.status==='paused')||w.storyboard_approved;
    $('workflow-repair').disabled=working;
    if(pausing){$('workflow-badge').textContent='正在暂停';$('workflow-title').textContent='完成当前镜头后暂停';$('workflow-description').textContent='暂停请求已收到。当前已提交的云端镜头完成后会保存，后续镜头不再提交。';$('workflow-next-title').textContent='已收到暂停请求';$('workflow-next-description').textContent='已生成的分镜和视频都会保留。';primary.textContent='正在暂停…';}
    $('workflow-review-guide').hidden=complete||video&&!review;$('workflow-error-detail').hidden=!p.error;$('workflow-error-original').textContent=p.error||'';
    const connection=$('workflow-connection');connection.hidden=!failed&&!retrying;
    connection.textContent=retrying?`连接暂时不稳定，正在第 ${w.connection.attempt} 次重试。` : w.connection?.state==='submission_unknown'?'提交回执未收到，云端是否接收尚未确定。请继续原任务处理，避免新建重复任务。':p.status==='paused'?'任务已按你的操作暂停，随时可以继续。':video?'中断发生在视频阶段，已确认的分镜会继续使用。':'分镜素材已保留，可继续完成剩余镜头。';
    const signature=JSON.stringify([p.id,p.run_id,p.storyboard,p.screenplay?.shots]);
    if(this.galleryKey!==signature){this.galleryKey=signature;const boards=new Map((p.storyboard||[]).map(b=>[b.index,b]));
      $('workflow-gallery').innerHTML=[{index:-1,name:'定妆图'},...(p.screenplay?.shots||[]).map(s=>({index:s.index,name:`镜头 ${s.index+1}`}))].map(s=>{const b=boards.get(s.index);return `<button type="button" data-workflow-shot="${s.index}" aria-label="查看${s.name}" aria-pressed="false">${b?`<img src="${esc(b.url)}" alt="${s.name}" loading="lazy">`:'<span class="workflow-thumb-empty">待生成</span>'}<span>${s.name}</span></button>`;}).join('');
    }
    this.drawMedia();
    const attention=p.run_id+(review?':review':complete?':complete':':run');
    if(state.route==='remix'&&this.attention!==attention){this.attention=attention;
      queueMicrotask(()=>{if(state.route!=='remix'||this.project?.id!==p.id)return;this.settings(false);if(review||complete)$('workflow-title').focus({preventScroll:true});});
    }
  }
  drawMedia(){
    const p=this.project;if(!p)return;
    $('workflow-reuse-image').hidden=!(p.status==='storyboard'&&this.selected>0&&!p.workflow?.storyboard_approved);
    const board=(p.storyboard||[]).find(b=>b.index===this.selected),shot=p.screenplay?.shots?.find(s=>s.index===this.selected);
    this.root.querySelectorAll('[data-workflow-tab]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.workflowTab===this.tab));if(b.dataset.workflowTab==='result')b.hidden=!p.video_ready;});
    this.root.querySelectorAll('[data-workflow-shot]').forEach(b=>b.setAttribute('aria-pressed',String(this.tab==='boards'&&Number(b.dataset.workflowShot)===this.selected)));
    const label=this.selected<0?'统一角色与门店定妆图':`镜头 ${this.selected+1}`;
    $('workflow-image-label').textContent=this.tab==='boards'?label:this.tab==='reference'?'参考原片':'新生成的视频';
    $('workflow-enlarge').hidden=this.tab!=='boards'||!board;
    const image=$('workflow-image'),video=$('workflow-video'),empty=$('workflow-empty');
    image.hidden=this.tab!=='boards'||!board;video.hidden=this.tab==='boards';empty.hidden=this.tab!=='boards'||!!board;
    if(board&&image.getAttribute('src')!==board.url)image.src=board.url;
    image.alt=label;
    empty.textContent=this.selected<0?'正在准备统一定妆图':'这个镜头尚未生成，完成后会自动出现在这里。';
    if(this.tab!=='boards'){
      const src=this.tab==='result'?`/api/remixes/${p.id}/result?run=${p.run_id}`:`/api/remixes/${p.id}/analysis/preview.mp4?ref=${p.reference_id}`;
      if(video.getAttribute('src')!==src)video.src=src;
    }else video.pause();
    $('workflow-shot-number').textContent=this.selected<0?'角色与场景基准':`${this.selected+1} / ${p.screenplay?.shots?.length||0} · ${shot?.seconds||0} 秒`;
    $('workflow-shot-title').textContent=this.selected<0?'全片保持一致':label;
    const copyKey=JSON.stringify([p.id,this.selected,shot?.dialogue,shot?.action,shot?.look_start,shot?.look_end]);
    if(this.copyKey!==copyKey){this.copyKey=copyKey;$('workflow-shot-copy').innerHTML=this.selected<0?'<p class="workflow-shot-action">后续镜头会参照这张图保持人物身份与门店一致，造型按各镜头剧情变化。</p>':`<p class="workflow-shot-action">${esc(shot?.action||'')}</p>${shot?.look_start||shot?.look_end?`<div class="workflow-dialogue workflow-look"><span>本镜头造型变化</span><p><strong>开始</strong> ${esc(shot.look_start||'沿用上一镜结束状态')}</p><p><strong>结束</strong> ${esc(shot.look_end||'保持开始状态')}</p></div>`:''}${shot?.dialogue?`<div class="workflow-dialogue"><span>这一镜的台词</span><p>${esc(shot.dialogue)}</p></div>`:''}`;}
    if(this.dialog.open)this.drawLightbox();
  }
  move(direction){
    const indices=(this.project?.storyboard||[]).map(b=>b.index);const i=indices.indexOf(this.selected);
    this.selected=indices[Math.max(0,Math.min(indices.length-1,i+direction))]??this.selected;this.drawMedia();
  }
  drawLightbox(){
    const boards=this.project?.storyboard||[],i=boards.findIndex(b=>b.index===this.selected),b=boards[i];if(!b)return;
    $('workflow-lightbox-title').textContent=this.selected<0?'统一角色与门店定妆图':`镜头 ${this.selected+1}`;
    $('workflow-lightbox-image').src=b.url;$('workflow-lightbox-count').textContent=`${i+1} / ${boards.length}`;
    $('workflow-lightbox-previous').disabled=i===0;$('workflow-lightbox-next').disabled=i===boards.length-1;
  }
}
