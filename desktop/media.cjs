const fs = require('node:fs/promises');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {gunzipSync} = require('node:zlib');
const hashes = {
  'win32-x64': ['8883a3dffbd0a16cf4ef95206ea05283f78908dbfb118f73c83f4951dcc06d77','f309e6223ad89d2fe54bccd420a7709b66fd27540674e92309578ed491a43c8d'],
  'darwin-arm64': ['8923876afa8db5585022d7860ec7e589af192f441c56793971276d450ed3bbfa','d986a8ec7b030899fe66a8a288ed809a3543338705a3ce178cfb85869c5d80be'],
  'darwin-x64': ['929b375c1182d956c51f7ac25e0b2b0411fb01f6f407aa15c9758efeb4242106','d4da574d6e2e197bd259b47d69cf262df9e312af24ad960444f6d806d3d4c186']
};
async function installMedia(directory, fetch, progress) {
  const target = `${process.platform}-${process.arch}`;
  if (!hashes[target]) throw new Error('当前系统暂不支持自动安装，请手动选择 FFmpeg 目录。');
  await fs.mkdir(directory, {recursive:true});
  for (const [i,name] of ['ffmpeg','ffprobe'].entries()) {
    progress(`正在下载媒体组件 ${i+1}/2…`);
    const url = `https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/${name}-${target}.gz`;
    const response = await fetch(url, {signal:AbortSignal.timeout(300000)});
    if (!response.ok) throw new Error(`下载失败（${response.status}），请重试或手动选择目录。`);
    const chunks = []; let size = 0;
    for await (const chunk of response.body) {
      size += chunk.byteLength;
      if (size > 60*1024*1024) throw new Error('媒体组件大小异常。');
      chunks.push(Buffer.from(chunk));
      progress(`下载 ${name} · ${(size/1024/1024).toFixed(1)} MB`);
    }
    const bytes = Buffer.concat(chunks);
    if (createHash('sha256').update(bytes).digest('hex') !== hashes[target][i]) throw new Error('下载校验失败，请重试。');
    const file = path.join(directory,name+(process.platform==='win32'?'.exe':''));
    await fs.writeFile(file+'.tmp', gunzipSync(bytes, {maxOutputLength:150*1024*1024}), {mode:0o755});
    await fs.rename(file+'.tmp', file);
  }
  progress('媒体组件已安装');
}
module.exports = {installMedia, hashes};
