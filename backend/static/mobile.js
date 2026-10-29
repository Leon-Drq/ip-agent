'use strict';
(() => {
  // Keep videos inline on phones, including players created after page load.
  const inlineVideos=root=>{
    if(root.matches?.('video'))root.setAttribute('playsinline','');
    root.querySelectorAll?.('video').forEach(video=>video.setAttribute('playsinline',''));
  };
  inlineVideos(document);
  new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(inlineVideos)))
    .observe(document.body,{childList:true,subtree:true});
  const dock=document.querySelector('.workflow-dock');
  if(dock)new ResizeObserver(()=>{
    document.documentElement.style.setProperty('--mobile-dock-height',`${Math.ceil(dock.getBoundingClientRect().height)}px`);
  }).observe(dock);
  // The phone keyboard gets the available screen; don't float actions over it.
  function keyboard(){
    const input=document.activeElement;
    const editing=input?.matches('textarea,input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=file]):not([type=button]):not([type=submit])');
    const viewport=window.visualViewport;
    document.body.classList.toggle('mobile-keyboard-open',!!editing&&!!viewport&&window.innerHeight-viewport.height>120);
  }
  window.visualViewport?.addEventListener('resize',keyboard);
  document.addEventListener('focusin',keyboard);
  document.addEventListener('focusout',()=>requestAnimationFrame(keyboard));
})();
