"""Cloud requests run in a separate process; errors never print response bodies or secrets."""
import json
import sys
from pathlib import Path
from cloud_speech import cloud

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    path = Path(sys.argv[2])
    try:
        job = json.loads(path.read_text(encoding='utf-8'))
        print('@@PROGRESS ' + json.dumps({'stage': 'tts', 'progress': 20, 'message': '云端正在生成配音'}, ensure_ascii=False), flush=True)
        cloud.synthesize(job, path.parent)
        print('@@PROGRESS ' + json.dumps({'stage': 'tts', 'progress': 34, 'message': '云端配音已保存'}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print('@@ERROR ' + (str(exc) if isinstance(exc, ValueError) else '云端语音处理失败，请核对账号和任务用量。'), flush=True)
        sys.exit(1)
