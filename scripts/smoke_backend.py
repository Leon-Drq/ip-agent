"""Exercise the source or frozen backend with a fresh, disposable user workspace.

No external services, API credentials, private media, or paid generations are used.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
import httpx

root=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='ip-agent-test-') as tmp:
    folder=Path(tmp)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    token=secrets.token_hex(32)
    env={**os.environ,'IP_AGENT_WORKSPACE':str(folder),'IP_AGENT_SESSION_TOKEN':token,
         'IP_AGENT_MASTER_KEY':secrets.token_hex(32),'PYTHONUTF8':'1'}
    frozen='--frozen' in sys.argv
    executable=root/'dist/backend/ip-agent-service'/('ip-agent-service.exe' if os.name=='nt' else 'ip-agent-service')
    cmd=[str(executable)] if frozen else [sys.executable,str(root/'backend/desktop_backend.py')]
    with (folder/'service.log').open('w+',encoding='utf-8') as logfile:
        proc=subprocess.Popen([*cmd,'--port',str(port)],env=env,stdout=logfile,stderr=logfile,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10) as client:
                for _ in range(100):
                    if proc.poll() is not None:break
                    try:
                        if client.get('/api/health').status_code==401:break
                    except httpx.HTTPError:pass
                    time.sleep(.2)
                else:raise AssertionError('Startup timeout')
                if proc.poll() is not None:
                    logfile.seek(0);raise AssertionError(logfile.read())
                assert client.get('/api/health').status_code==401
                client.headers['x-ip-agent-token']=token
                assert client.get('/api/health').json()['app']=='avatar-studio'
                assert client.get('/api/health',headers={'host':'evil.example'}).status_code==403
                assert client.get('/api/jobs',headers={'origin':'https://evil.example'}).status_code==403
                page=client.get('/').text
                assert 'IP智能体' in page and 'mobile.css' in page
                for route in ['/api/voices','/api/avatars','/api/projects','/api/jobs','/api/remixes']:
                    response=client.get(route);assert response.status_code==200,(route,response.text)
                    assert response.json()==[],route
                assert client.get('/api/models').status_code==200
                assert client.get('/api/model-settings').status_code==200
                # Verify media intake, Range playback and persistence when tools are supplied.
                if os.environ.get('IP_AGENT_MEDIA_BIN'):
                    audio=folder/'sample.wav'
                    with wave.open(str(audio),'wb') as wav:
                        wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*64000)
                    with audio.open('rb') as source:
                        response=client.post('/api/uploads',data={'kind':'audio'},files={'file':('sample.wav',source,'audio/wav')})
                    assert response.status_code==200,response.text
                    upload=response.json();assert upload['duration']==4
                    media=client.get(f'/api/uploads/{upload["id"]}/content',headers={'Range':'bytes=0-43'})
                    assert media.status_code==206 and len(media.content)==44
                    work=folder/'worker-test';work.mkdir()
                    (work/'job.json').write_text('{}')
                    (work/'inputs.json').write_text(json.dumps({'reference':str(audio)}))
                    completed=subprocess.run([*cmd,'--worker','worker','prepare',str(work/'job.json')],env=env,capture_output=True,timeout=30)
                    assert completed.returncode==0,completed.stderr.decode(errors='replace')
                    assert (work/'reference.wav').stat().st_size>1000
                print(('Frozen' if frozen else 'Source')+' backend: clean workspace, auth, UI, library, settings and media passed')
        finally:
            proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
