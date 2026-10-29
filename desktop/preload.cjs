const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('desktop', Object.freeze({
  status:()=>ipcRenderer.invoke('setup:status'),
  launch:(mode,url)=>ipcRenderer.invoke('setup:launch',{mode,url}),
  installMedia:()=>ipcRenderer.invoke('setup:install-media'),
  chooseMedia:()=>ipcRenderer.invoke('setup:choose-media'),
  openData:()=>ipcRenderer.invoke('setup:open-data'),
  onProgress:callback=>{ipcRenderer.on('setup:progress', (_event,text)=>callback(text));}
}));
