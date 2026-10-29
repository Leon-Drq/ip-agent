"""HypiHub HTTP adapter. Credentials never enter drafts, responses, or job logs."""
import base64
import ctypes
import ipaddress
import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import httpx

BASE = 'https://hypit.ai/v1'
PUBLIC = 'https://hypit.ai/api/hub/public/models'
TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}
class HubSubmissionUnknown(RuntimeError):
    pass

def validate_asset_url(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
        raise RuntimeError('云端成片下载地址无效。')
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    # This machine's TUN proxy maps public hosts into its 198.18/15 fake-IP pool.
    # Permit that mapping only for the exact storage host returned by HypiHub;
    # private/loopback/link-local addresses and arbitrary hosts remain rejected.
    proxy_pool=ipaddress.ip_network('198.18.0.0/15')
    trusted_proxy_host=parsed.hostname=='storage.googleapis.com'
    def allowed(address):
        ip=ipaddress.ip_address(address)
        return ip.is_global or trusted_proxy_host and ip.version==4 and ip in proxy_pool
    if not addresses or any(not allowed(a[4][0]) for a in addresses):
        raise RuntimeError('云端成片地址不能指向本机或内网。')

def clean_error(value):
    text = re.sub(r'sk-[A-Za-z0-9_-]+', '[密钥已隐藏]', str(value))
    return re.sub(r'https?://\S+', '[服务地址]', text)[:450]

def protect(data, decrypt=False):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = os.environ.get('IP_AGENT_MASTER_KEY', '')
    if not key:
        raise RuntimeError('请通过桌面应用启动，以启用系统加密密钥存储。')
    cipher = AESGCM(bytes.fromhex(key))
    if decrypt:
        if not data.startswith(b'IPA1'):
            raise RuntimeError('密钥格式不同，请在当前工作环境重新连接账号。')
        return cipher.decrypt(data[4:16], data[16:], b'ip-agent-credentials')
    nonce = os.urandom(12)
    return b'IPA1' + nonce + cipher.encrypt(nonce, data, b'ip-agent-credentials')

class Hub:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.secret = self.directory / 'hypihub.dpapi'
        self.cache = self.directory / 'models.json'
        self._upload_check = None
        self._upload_lock = threading.Lock()

    def key(self):
        if self.secret.exists():
            return protect(self.secret.read_bytes(), True).decode()
        return os.environ.get('HYPIT_API_KEY', '')

    def configured(self):
        return self.secret.exists() or bool(os.environ.get('HYPIT_API_KEY'))

    def connect(self, key):
        key = key.strip()
        if not key or len(key) > 1000 or any(c.isspace() for c in key):
            raise RuntimeError('请填写 HypiHub API Key。')
        data = self.request('GET', '/models', key=key)
        if not isinstance(data.get('data'), list):
            raise RuntimeError('未能读取账号可用模型，请检查密钥。')
        tmp = self.secret.with_suffix('.tmp')
        tmp.write_bytes(protect(key.encode()))
        tmp.replace(self.secret)
        self.cache.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return self.models()

    def request(self, method, path, *, body=None, key=None, request_id=None, on_retry=None):
        token = self.key() if key is None else key
        if not token:
            raise RuntimeError('请先在「连接 HypiHub」中保存 API Key。')
        headers = {'Authorization': 'Bearer ' + token}
        if request_id:
            headers['Idempotency-Key'] = request_id
        # Connection establishment failed before the request was sent: safe to retry.
        # Once a POST may have been written, preserve the unknown outcome instead.
        readonly = method.upper() in {'GET', 'HEAD'}
        attempts = 4
        def retry(attempt):
            delay = 2 ** attempt
            if on_retry: on_retry(attempt + 1, delay)
            time.sleep(delay)
        for attempt in range(attempts):
            try:
                response = httpx.request(method, BASE + path, headers=headers, json=body,
                                         timeout=httpx.Timeout(180, connect=20), follow_redirects=False)
            except httpx.HTTPError as error:
                logging.getLogger(__name__).warning('HypiHub %s %s attempt %d: %s', method, path.split('?')[0], attempt+1, type(error).__name__)
                safe = readonly or isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                if safe and attempt + 1 < attempts:
                    retry(attempt); continue
                if not safe:
                    raise HubSubmissionUnknown('视频服务未返回提交回执，暂时无法确认是否已接收；已保留原请求，请勿新建重复任务。') from None
                raise RuntimeError('读取 HypiHub 状态时连接中断，自动重试后仍未恢复；已接收的云端任务可能仍在执行，可继续原任务查询结果。'
                                   if readonly else '连接视频服务失败，尚未发出本次生成请求；已尝试连接 4 次，可从原任务继续。') from None
            if readonly and response.status_code in TRANSIENT_STATUSES and attempt + 1 < attempts:
                retry(attempt); continue
            try:
                data = response.json()
            except ValueError:
                if response.is_success and readonly and attempt + 1 < attempts:
                    retry(attempt); continue
                if not readonly:
                    raise HubSubmissionUnknown('视频服务返回的提交回执无法读取；已保留原请求，请勿新建重复任务。') from None
                raise RuntimeError(f'HypiHub 返回异常响应（HTTP {response.status_code}）；原任务已保存，可稍后继续。') from None
            if not response.is_success:
                detail = data.get('error', {}) if isinstance(data, dict) else {}
                message = detail.get('message', '服务暂不可用') if isinstance(detail, dict) else str(detail)
                if not readonly and response.status_code >= 500:
                    raise HubSubmissionUnknown(f'视频服务返回异常（HTTP {response.status_code}），提交结果待核实；已保留原请求，请勿新建重复任务。') from None
                raise RuntimeError(f'HypiHub ({response.status_code})：' + clean_error(message))
            return data

    def models(self, refresh=False):
        if refresh:
            if self.configured():
                data = self.request('GET', '/models')
            else:
                response = httpx.get(PUBLIC, timeout=25)
                response.raise_for_status()
                data = response.json()
            self.cache.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        if not self.cache.exists():
            return []
        rows = json.loads(self.cache.read_text(encoding='utf-8')).get('data', [])
        return [{**row, 'id': row.get('id') or row.get('name')} for row in rows]

    def upload_status(self):
        return {'ready': True, 'message': '内置跨平台上传组件已就绪'}

    def ensure_uploader(self):
        return

    def upload(self, path, person=False):
        from hub_upload import upload_reference
        return upload_reference(self, path, person)

    def download(self, job_id, target):
        for attempt in range(4):
            try:
                return self._download_once(job_id, target)
            except httpx.HTTPError as error:
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in TRANSIENT_STATUSES:
                    raise RuntimeError(f'读取云端成片失败（HTTP {error.response.status_code}）；任务编号已保留。') from None
                if attempt == 3:
                    raise RuntimeError('云端素材已生成，但下载连接中断；自动重试后仍未恢复，继续原任务可重新下载已有素材。') from None
                time.sleep(2 ** attempt)

    def _download_once(self, job_id, target):
        items = self.request('GET', f'/jobs/{job_id}/assets').get('items', [])
        if not items:
            raise RuntimeError('云端任务已结束，但没有返回可下载的文件。')
        url = items[0]['url']
        temporary = Path(target).with_suffix('.download')
        try:
            for _ in range(5):
                validate_asset_url(url)
                with httpx.stream('GET', url, timeout=120, follow_redirects=False) as response:
                    if response.is_redirect:
                        from urllib.parse import urljoin
                        url = urljoin(url, response.headers['location'])
                        continue
                    response.raise_for_status()
                    size = 0
                    with temporary.open('wb') as out:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 1024**3:
                                raise RuntimeError('云端输出超过 1 GB。')
                            out.write(chunk)
                temporary.replace(target)
                return
            raise RuntimeError('云端下载重定向过多。')
        finally:
            temporary.unlink(missing_ok=True)

def estimate(card, seconds=1, resolution='', reference_seconds=0, audio=True):
    """Match the published credit rate, including reference-input charges. Fail closed."""
    pricing = card.get('pricing') or {}
    credits = pricing.get('credits') or {}
    rates = credits.get('media_rates')
    if not rates and pricing.get('media_rates'):
        raise RuntimeError('该模型暂未提供积分报价，请刷新模型列表或选择其他模型。')
    flags = {'reference_video': reference_seconds > 0, 'audio': audio}
    if rates:
        matches = [r for r in rates if r.get('resolution', resolution) == resolution
                   and all(flags.get(k) == v for k,v in r.get('flags',{}).items())
                   and not r.get('quality')]
        if not matches:
            raise RuntimeError('该模型的当前分辨率报价尚不能确定，请选择其他模型。')
        rate = max(matches, key=lambda r: r['unit'])
        amount = float(rate['unit']) * seconds + float(rate.get('input_unit',0)) * reference_seconds
    else:
        unit = credits.get('per_second') if card.get('modality') == 'video' else credits.get('base')
        if unit is None or pricing.get('flag_ratio'):
            raise RuntimeError('该模型未提供可确认的积分报价，请选择其他模型。')
        ratios = pricing.get('resolution_ratio', {})
        if ratios and resolution not in ratios:
            raise RuntimeError('模型不支持此分辨率的报价。')
        amount = float(unit) * seconds * float(ratios.get(resolution,1))
    if not math.isfinite(amount) or amount < 0:
        raise RuntimeError('模型报价无效。')
    return round(amount,4)
