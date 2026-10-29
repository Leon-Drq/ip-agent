const {app, BrowserWindow, Menu, dialog, shell, ipcMain, safeStorage, net, session} = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const crypto = require('node:crypto');
const tcp = require('node:net');
const {spawn, execFile} = require('node:child_process');
const {promisify} = require('node:util');
const {pathToFileURL} = require('node:url');
const {serviceURL, sameOrigin} = require('./security.cjs');
const {installMedia} = require('./media.cjs');
const execute = promisify(execFile);
const smoke = process.argv.includes('--smoke-test');
if(smoke)app.setPath('userData',fs.mkdtempSync(path.join(require('node:os').tmpdir(),'ip-agent-desktop-')));
let window, setupWindow, backend, backendOrigin='', token='', quitting=false, shuttingDown=false;
const previews=new Map();
let configuration={mode:'builtin',url:'http://127.0.0.1:7865'};
const setupFile=path.join(__dirname,'setup.html');
const home=()=>app.getPath('userData');
const workspace=()=>path.join(home(),'workspace');
const mediaDir=()=>configuration.mediaDir||path.join(workspace(),'bin');
function progress(text){if(setupWindow&&!setupWindow.isDestroyed())setupWindow.webContents.send('setup:progress',text);}
function save(){fs.writeFileSync(path.join(home(),'desktop.json'),JSON.stringify(configuration,null,2),{mode:0o600});}
function securePreferences(preload){return {nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,...(preload?{preload}:{} )};}
function openExternal(url){try{const u=new URL(url);if(u.protocol==='https:'&&!u.username&&!u.password)shell.openExternal(u.href);}catch{}}
function protectWindow(win,origin){
  win.webContents.setWindowOpenHandler(({url})=>{
    if(origin&&sameOrigin(url,origin)){
      const preview=new BrowserWindow({title:'查看素材',width:820,height:800,parent:win,webPreferences:securePreferences()});
      const id=preview.webContents.id;previews.set(id,preview);protectWindow(preview,origin);
      preview.on('closed',()=>previews.delete(id));preview.loadURL(url).catch(()=>preview.close());
    }else openExternal(url);
    return {action:'deny'};
  });
  win.webContents.on('will-navigate',(event,url)=>{if(origin?!sameOrigin(url,origin):url!==pathToFileURL(setupFile).href){event.preventDefault();openExternal(url);}});
  win.webContents.on('will-attach-webview',event=>event.preventDefault());
}
function showSetup(){
  if(setupWindow&&!setupWindow.isDestroyed()){setupWindow.show();setupWindow.focus();return;}
  setupWindow=new BrowserWindow({title:'IP智能体 · 工作环境',width:930,height:940,minWidth:560,minHeight:600,backgroundColor:'#f8fafc',webPreferences:securePreferences(path.join(__dirname,'preload.cjs'))});
  protectWindow(setupWindow,null);setupWindow.loadFile(setupFile);
  setupWindow.on('closed',()=>{setupWindow=null;});
}
function fromSetup(event){
  if(!setupWindow||event.sender!==setupWindow.webContents||event.senderFrame!==setupWindow.webContents.mainFrame)
    throw new Error('此操作仅限工作环境设置窗口。');
}
async function masterKey(){
  if(!await safeStorage.isAsyncEncryptionAvailable())throw new Error('系统安全存储暂不可用，请解锁钥匙串后重试。');
  const file=path.join(home(),'vault.secret');
  if(fs.existsSync(file)){
    const decoded=await safeStorage.decryptStringAsync(fs.readFileSync(file));
    if(decoded.shouldReEncrypt)fs.writeFileSync(file,await safeStorage.encryptStringAsync(decoded.result),{mode:0o600});
    return decoded.result;
  }
  const key=crypto.randomBytes(32).toString('hex');
  fs.writeFileSync(file,await safeStorage.encryptStringAsync(key),{mode:0o600});return key;
}
async function mediaReady(){
  try{for(const name of ['ffmpeg','ffprobe'])await execute(path.join(mediaDir(),name+(process.platform==='win32'?'.exe':'')),['-version'],{windowsHide:true,timeout:8000});return true;}catch{return false;}
}
async function freePort(){return new Promise((resolve,reject)=>{const server=tcp.createServer();server.on('error',reject);server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});}
async function health(origin,auth=''){
  const response=await fetch(origin+'/api/health',{headers:auth?{'x-ip-agent-token':auth}:{},signal:AbortSignal.timeout(5000),redirect:'error'});
  if(!response.ok)throw new Error(`连接失败（HTTP ${response.status}）`);
  const result=await response.json();if(result.app!=='avatar-studio')throw new Error('该地址不是兼容的 IP智能体工作室。');return result;
}
async function startBackend(){
  if(backend&&backend.exitCode===null){await health(backendOrigin,token);return backendOrigin;}
  await fsp.mkdir(workspace(),{recursive:true});
  const port=await freePort();token=crypto.randomBytes(32).toString('hex');backendOrigin=`http://127.0.0.1:${port}`;
  const env={...process.env,IP_AGENT_WORKSPACE:workspace(),IP_AGENT_MEDIA_BIN:mediaDir(),IP_AGENT_SESSION_TOKEN:token,IP_AGENT_MASTER_KEY:await masterKey(),PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'};
  // Never let development loader settings leak into the bundled Python runtime.
  delete env.PYTHONHOME;delete env.PYTHONPATH;
  let command,args;
  if(app.isPackaged){command=path.join(process.resourcesPath,'backend','ip-agent-service'+(process.platform==='win32'?'.exe':''));args=['--port',String(port)];}
  else {command=process.env.IP_AGENT_PYTHON||path.join(__dirname,'..','.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');args=[path.join(__dirname,'..','backend','desktop_backend.py'),'--port',String(port)];}
  const log=fs.openSync(path.join(home(),'service.log'),'a');
  backend=spawn(command,args,{env,windowsHide:true,detached:process.platform!=='win32',stdio:['ignore',log,log]});fs.closeSync(log);
  let error;
  backend.once('error',e=>{error=e;});
  backend.once('exit',()=>{if(!quitting&&!shuttingDown&&window&&!window.isDestroyed()){dialog.showMessageBox(window,{type:'warning',message:'本地工作室已停止',detail:'作品仍保存在本机。请打开“工作环境”重新进入。'});showSetup();}});
  for(let i=0;i<90;i++){
    if(error||backend.exitCode!==null)throw new Error('本地服务未能启动，请在数据文件夹中查看 service.log。');
    try{await health(backendOrigin,token);return backendOrigin;}catch{}
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  stopBackend();throw new Error('工作室启动超时，请重试。');
}
function stopBackend(){
  if(!backend||backend.exitCode!==null)return;
  shuttingDown=true;
  if(process.platform==='win32')spawn('taskkill',['/PID',String(backend.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
  else {try{process.kill(-backend.pid,'SIGTERM');}catch{backend.kill('SIGTERM');}}
  backend=null;
}
async function launch(mode,url){
  if(!['builtin','connect'].includes(mode))throw new Error('请选择工作环境。');
  let origin;
  if(mode==='builtin'){
    if(!await mediaReady())throw new Error('请先安装媒体工具，或选择已有的 FFmpeg 目录。');
    shuttingDown=false;origin=await startBackend();
  }else{origin=serviceURL(url);await health(origin);}
  configuration={...configuration,mode,url:mode==='connect'?origin:configuration.url};save();
  const oldWindow=window;
  for(const preview of previews.values())if(!preview.isDestroyed())preview.destroy();
  previews.clear();
  window=new BrowserWindow({title:'IP智能体',show:!smoke,width:1440,height:960,minWidth:560,minHeight:500,backgroundColor:'#f9fbfd',webPreferences:securePreferences()});
  const current=window;protectWindow(current,origin);
  // Token never enters renderer JavaScript or a URL; attach only to this exact origin.
  session.defaultSession.webRequest.onBeforeSendHeaders((details,callback)=>{
    if(!current.isDestroyed()&&mode==='builtin'&&(details.webContentsId===current.webContents.id||previews.has(details.webContentsId))&&sameOrigin(details.url,backendOrigin))
      details.requestHeaders['x-ip-agent-token']=token;
    callback({requestHeaders:details.requestHeaders});
  });
  current.on('closed',()=>{if(window===current)window=null;});
  await current.loadURL(origin);
  if(oldWindow&&!oldWindow.isDestroyed())oldWindow.destroy();
  if(setupWindow&&!setupWindow.isDestroyed())setupWindow.close();
}
if(!app.requestSingleInstanceLock()){app.quit();}else{
  app.on('second-instance',()=>{const w=window||setupWindow;if(w){if(w.isMinimized())w.restore();w.show();w.focus();}});
  app.whenReady().then(()=>{
    fs.mkdirSync(home(),{recursive:true});
    try{configuration={...configuration,...JSON.parse(fs.readFileSync(path.join(home(),'desktop.json'),'utf8'))};}catch{}
    const clipboardAllowed=(wc,p)=>p==='clipboard-sanitized-write'&&!!window&&wc===window.webContents;
    session.defaultSession.setPermissionRequestHandler((wc,p,callback)=>callback(clipboardAllowed(wc,p)));
    session.defaultSession.setPermissionCheckHandler((wc,p)=>clipboardAllowed(wc,p));
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      ...(process.platform==='darwin'?[{role:'appMenu'}]:[]),
      {label:'工作室',submenu:[{label:'工作环境…',accelerator:'CmdOrCtrl+,',click:showSetup},{label:'打开数据文件夹',click:()=>shell.openPath(home())},{type:'separator'},{role:'quit',label:'退出'}]},
      {label:'编辑',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
      {label:'视图',submenu:[{role:'reload',label:'刷新页面'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'},{role:'togglefullscreen'}]},
      {label:'帮助',submenu:[{label:'使用说明与源码',click:()=>openExternal('https://github.com/Leon-Drq/ip-agent')},{label:'下载更新',click:()=>openExternal('https://github.com/Leon-Drq/ip-agent/releases')} ]}
    ]));
    ipcMain.handle('setup:status',async event=>{fromSetup(event);return {version:app.getVersion(),arch:process.arch,mediaReady:await mediaReady(),...configuration};});
    ipcMain.handle('setup:launch',async(event,args)=>{fromSetup(event);await launch(args.mode,args.url);return true;});
    ipcMain.handle('setup:install-media',async event=>{fromSetup(event);await installMedia(path.join(workspace(),'bin'),net.fetch,progress);configuration.mediaDir=path.join(workspace(),'bin');save();return true;});
    ipcMain.handle('setup:choose-media',async event=>{fromSetup(event);const selected=await dialog.showOpenDialog(setupWindow,{title:'选择包含 FFmpeg 和 FFprobe 的文件夹',properties:['openDirectory']});if(selected.canceled)return false;const previous=configuration.mediaDir;configuration.mediaDir=selected.filePaths[0];if(!await mediaReady()){configuration.mediaDir=previous;throw new Error('这个目录中没有可运行的 FFmpeg 和 FFprobe。');}save();return true;});
    ipcMain.handle('setup:open-data',event=>{fromSetup(event);return shell.openPath(home());});
    if(smoke){
      configuration.mediaDir=process.env.IP_AGENT_SMOKE_MEDIA_BIN;
      return launch('builtin').then(async()=>{
        await health(backendOrigin,token);
        // Exercise native OS encryption without using a real credential.
        if(await masterKey()!==await masterKey())throw new Error('System key storage did not round-trip');
        if(!window.getTitle().includes('IP智能体'))throw new Error('Studio window failed to load');
        console.log('IP_AGENT_DESKTOP_SMOKE_OK');app.quit();
      });
    }
    showSetup();
  }).catch(error=>{if(smoke){console.error(error.message);app.exit(1);}else{dialog.showErrorBox('IP智能体启动失败',error.message);app.quit();}});
  app.on('window-all-closed',()=>{app.quit();});
  app.on('before-quit',()=>{quitting=true;stopBackend();});
  app.on('activate',()=>{if(!window&&!setupWindow)showSetup();});
}
