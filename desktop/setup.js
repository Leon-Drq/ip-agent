let mode='builtin', busy=false;
const $=id=>document.getElementById(id);
function message(text,error=false){$('status').textContent=text;$('status').className=error?'error':'';}
function select(value){mode=value;for(const name of ['builtin','connect'])$(name).classList.toggle('active',name===mode);$('local-panel').hidden=mode!=='builtin';$('remote-panel').hidden=mode!=='connect';}
function lock(value){busy=value;document.querySelectorAll('button,input').forEach(el=>el.disabled=value);}
async function act(callback){if(busy)return;lock(true);try{await callback();}catch(error){message(error.message.replace(/^Error invoking remote method '[^']+': Error: /,''),true);}finally{lock(false);}}
async function refresh(){const s=await window.desktop.status();$('version').textContent='v'+s.version+' · '+s.arch;$('media-state').textContent=s.mediaReady?'已就绪':'尚未安装';$('install').textContent=s.mediaReady?'重新安装媒体工具':'下载并安装媒体工具';if(s.url)$('url').value=s.url;select(s.mode||'builtin');}
$('builtin').onclick=()=>select('builtin');$('connect').onclick=()=>select('connect');
$('install').onclick=()=>act(async()=>{await window.desktop.installMedia();await refresh();message('媒体工具已就绪，可以进入工作室。');});
$('choose').onclick=()=>act(async()=>{await window.desktop.chooseMedia();await refresh();});
$('launch').onclick=()=>act(async()=>{message('正在连接工作室…');await window.desktop.launch(mode,$('url').value.trim());});
$('data').onclick=()=>act(()=>window.desktop.openData());
window.desktop.onProgress(text=>message(text));refresh().catch(e=>message(e.message,true));
