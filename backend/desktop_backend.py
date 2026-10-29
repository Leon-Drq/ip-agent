"""Frozen service entry point. Electron supplies a new bearer token each launch."""
import argparse
import hmac
import json
import os
import sys
from pathlib import Path

def run_worker(module, stage, job_path):
    from settings import environment
    os.environ.update(environment())
    path = Path(job_path)
    job = json.loads(path.read_text(encoding='utf-8'))
    try:
        if module == 'worker_cloud_speech' and stage == 'tts':
            from cloud_speech import cloud
            cloud.synthesize(job, path.parent)
        elif module == 'worker' and stage in ('prepare', 'speed'):
            from worker import prepare, video_speed
            inputs = json.loads((path.parent/'inputs.json').read_text(encoding='utf-8'))
            (prepare if stage == 'prepare' else video_speed)(job, path.parent, inputs)
        else:
            raise RuntimeError('此功能需要单独安装本地模型引擎，或连接已有工作室。')
    except Exception as error:
        print('@@ERROR ' + (str(error)[:400] if isinstance(error, (ValueError, RuntimeError)) else '处理未完成，请检查运行环境。'), flush=True)
        sys.exit(1)

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        run_worker(*sys.argv[2:5])
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    token = os.environ.get('IP_AGENT_SESSION_TOKEN', '')
    if len(token) < 32:
        raise SystemExit('IP_AGENT_SESSION_TOKEN must contain at least 32 characters')
    import uvicorn
    from fastapi.responses import JSONResponse
    from server import app
    @app.middleware('http')
    async def desktop_auth(request, call_next):
        candidate = request.headers.get('x-ip-agent-token', '')
        if not hmac.compare_digest(candidate, token):
            return JSONResponse({'detail': '请从 IP智能体桌面应用访问'}, status_code=401)
        if request.headers.get('host', '') != f'127.0.0.1:{args.port}':
            return JSONResponse({'detail': '无效的访问地址'}, status_code=403)
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; media-src 'self' blob:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        return response
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, log_level='warning')

if __name__ == '__main__':
    main()
