const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const packaged=process.argv.includes('--packaged');
let command,args;
if(packaged){
  command=process.platform==='win32'?path.resolve('release/win-unpacked/IP智能体.exe'):
    path.resolve(`release/${process.arch==='arm64'?'mac-arm64':'mac'}/IP智能体.app/Contents/MacOS/IP智能体`);
  args=['--smoke-test'];
}else{command=require('electron');args=['.','--smoke-test'];}
const env={...process.env,IP_AGENT_SMOKE_MEDIA_BIN:process.env.IP_AGENT_MEDIA_BIN||path.resolve('.desktop-test/bin')};
// Some host tools set this for their own helpers; a real Electron app needs its GUI runtime.
delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(command,args,{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
let output='';
child.stdout.on('data',b=>{output+=b.toString();process.stdout.write(b);});
child.stderr.on('data',b=>process.stderr.write(b));
child.on('error',error=>{console.error(error.message);process.exitCode=1;});
const timer=setTimeout(()=>{child.kill();console.error('Desktop startup timeout');process.exitCode=1;},90000);
child.on('exit',code=>{clearTimeout(timer);if(code!==0||!output.includes('IP_AGENT_DESKTOP_SMOKE_OK'))process.exitCode=1;});
