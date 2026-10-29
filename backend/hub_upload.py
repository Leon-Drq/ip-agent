"""Independent HTTP upload adapter; no Hypit SDK code or runtime is bundled.

Implements HypiHub's negotiated API-multipart / signed-part wire protocol.
Credentials are sent only to the fixed API origin, never to signed storage URLs.
"""
import base64
import hashlib
import math
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote, urljoin
import httpx

def upload_reference(hub, path, person=False):
    from remix_hub import BASE, validate_asset_url
    path = Path(path)
    size = path.stat().st_size
    if not 0 < size <= 1024**3:
        raise RuntimeError('参考素材大小须在 1 字节到 1 GB 之间。')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        head = source.read(512)
        digest.update(head)
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    policy = hub.request('POST', '/files/uploads', body={
        'filename': path.name, 'bytes': size, 'mime_type': mime, 'purpose': 'reference',
        'is_person_reference': person, 'sha256': digest.hexdigest(),
        'head_base64': base64.b64encode(head).decode(),
    })
    if policy.get('upload_mode') == 'api_multipart':
        if urljoin(BASE+'/', policy.get('endpoint', '')) != BASE+'/files':
            raise RuntimeError('服务返回了无效的素材上传地址。')
        if size > int(policy.get('max_bytes', 1024**3)):
            raise RuntimeError('素材超出服务允许的大小。')
        with path.open('rb') as source:
            response = httpx.post(BASE+'/files', headers={'Authorization': 'Bearer '+hub.key()},
                data={'purpose': 'reference', 'is_person_reference': str(person).lower()},
                files={'file': (path.name, source, mime)}, timeout=600, follow_redirects=False)
        if response.status_code >= 400:
            raise RuntimeError(f'参考素材上传失败（HTTP {response.status_code}）。')
        result = response.json()
    elif policy.get('upload_mode') == 's3_multipart':
        uid = quote(str(policy['upload_id']), safe='')
        part_size, count = int(policy['part_size']), int(policy['part_count'])
        if not 0 < part_size <= 128 * 1024**2 or not 0 < count <= 10000 or count != math.ceil(size/part_size):
            raise RuntimeError('服务返回了无效的分段上传参数。')
        completed = []
        try:
            with path.open('rb') as source:
                for number in range(1, count+1):
                    content = source.read(part_size)
                    checksum = base64.b64encode(hashlib.sha256(content).digest()).decode()
                    declaration = {'part_number': number, 'bytes': len(content), 'checksum_sha256': checksum}
                    for attempt in range(3):
                        signed = hub.request('POST', f'/files/uploads/{uid}/parts', body={'parts': [declaration]})['parts']
                        part = next(p for p in signed if p['part_number'] == number)
                        validate_asset_url(part['url'])
                        headers = {k.lower():str(v) for k,v in part['headers'].items()}
                        if headers.get('content-length') != str(len(content)) or headers.get('x-amz-checksum-sha256') != checksum:
                            raise RuntimeError('上传分段的长度或校验值不匹配。')
                        try:
                            response = httpx.put(part['url'], content=content,
                                headers={k:headers[k] for k in ('content-length','x-amz-checksum-sha256')},
                                timeout=180, follow_redirects=False)
                            response.raise_for_status()
                            etag = response.headers.get('etag')
                            if not etag or response.headers.get('x-amz-checksum-sha256', checksum) != checksum:
                                raise RuntimeError('分段上传校验未通过。')
                            completed.append({'part_number':number, 'etag':etag, 'checksum_sha256':checksum})
                            break
                        except httpx.HTTPError:
                            if attempt == 2:
                                raise RuntimeError('素材上传连接中断，请继续原任务。') from None
                            time.sleep(2**attempt)
            result = hub.request('POST', f'/files/uploads/{uid}/complete', body={'parts':completed})
        except Exception:
            try:
                hub.request('DELETE', f'/files/uploads/{uid}')
            except Exception:
                pass
            raise
    else:
        raise RuntimeError('当前服务使用了尚不支持的上传协议，请更新应用。')
    url = result.get('url', '')
    validate_asset_url(url)
    return url
