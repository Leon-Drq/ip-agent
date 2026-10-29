function serviceURL(value) {
  const url = new URL(value);
  const loopback = ['127.0.0.1','localhost','[::1]'].includes(url.hostname);
  if (url.username || url.password || (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)))
    throw new Error('请输入 HTTPS 地址，或本机的 http://127.0.0.1:端口');
  if (url.pathname !== '/' || url.search || url.hash)
    throw new Error('请只填写服务地址和端口，不含页面路径。');
  return url.origin;
}
function sameOrigin(value, origin) {
  try { return new URL(value).origin === origin; } catch { return false; }
}
module.exports = {serviceURL, sameOrigin};
