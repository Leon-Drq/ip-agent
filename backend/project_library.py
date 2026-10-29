"""One recoverable library for local productions and saved remix runs."""
import copy
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from remix_service import ACTIVE


class Selection(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=1000)


class ProjectLibrary:
    def __init__(self, jobs, job_lock, processes, change_jobs, remixes):
        self.jobs, self.job_lock, self.processes = jobs, job_lock, processes
        self.change_jobs, self.remixes = change_jobs, remixes
        self.router = self.routes()

    def remix_entry(self, p):
        run = p.get('run') or {}
        ready = bool(run.get('completed')) and (self.remixes.projects / p['id'] / run['id'] / 'result.mp4').is_file()
        status = ('completed' if ready else 'running' if p['status'] in ACTIVE else
                  'review' if p['status'] == 'storyboard' else 'failed')
        label = {'imitate': '模仿创作', 'character': '角色替换'}.get(p.get('edit_mode'), '视频复刻')
        return {'id': 'remix:' + p['id'], 'source': 'remix', 'project_id': p['id'],
                'title': p['title'], 'text': p.get('script') or p.get('transcript') or '',
                'mode': 'video', 'status': status, 'video_ready': ready, 'audio_ready': False,
                'created_at': p['created_at'], 'updated_at': p.get('updated_at', p['created_at']),
                'deleted_at': p.get('library_deleted_at'), 'description': label,
                'poster_url': f'/api/projects/remix:{p["id"]}/poster?run={run.get("id", "")}',
                'result_url': f'/api/remixes/{p["id"]}/result?run={run.get("id", "")}',
                'download_url': f'/api/remixes/{p["id"]}/result?download=true'}

    def listing(self, trash=False):
        # Remix previews read local speech jobs while holding the remix lock.
        # Use that same order so concurrent legacy listing cannot deadlock.
        with self.remixes.lock, self.job_lock:
            rows = [copy.deepcopy(j) for j in self.jobs.values() if bool(j.get('deleted_at')) == trash]
            rows += [self.remix_entry(p) for p in self.remixes.records.values()
                     if p.get('run') and bool(p.get('library_deleted_at')) == trash]
            return sorted(rows, key=lambda row: row['created_at'], reverse=True)

    def change(self, ids, deleted):
        # Validate the entire mixed selection under both locks before any changes.
        with self.remixes.lock, self.job_lock:
            ids = list(dict.fromkeys(ids))
            local, remix = [], []
            for key in ids:
                if key.startswith('remix:'):
                    pid = key.removeprefix('remix:')
                    p = self.remixes.get(pid)
                    if not p.get('run'):
                        raise HTTPException(404, '作品不存在，请刷新后重试。')
                    if p['status'] in ACTIVE or (p.get('audio_render') or {}).get('status') in {'queued', 'running'}:
                        raise HTTPException(409, '选中的作品仍在生成，请先暂停任务。')
                    remix.append(pid)
                else:
                    j = self.jobs.get(key)
                    if not j:
                        raise HTTPException(404, '作品不存在，请刷新后重试。')
                    proc = self.processes.get(key)
                    if j['status'] in {'queued', 'running'} or proc and proc.poll() is None:
                        raise HTTPException(409, '选中的作品仍在生成或停止中，请稍后再试。')
                    local.append(key)
            changed = {j['id']: j for j in self.change_jobs(local, deleted)['jobs']} if local else {}
            from extraction_service import timestamp
            for pid in remix:
                p = self.remixes.get(pid)
                if bool(p.get('library_deleted_at')) != deleted:
                    p = self.remixes.update(pid, library_deleted_at=timestamp() if deleted else None)
                item = self.remix_entry(p)
                changed[item['id']] = item
            return {'jobs': [changed[key] for key in ids]}

    def routes(self):
        router = APIRouter(prefix='/api/projects')

        @router.get('')
        def listing(trash: bool = False):
            return self.listing(trash)

        @router.post('/batch-delete')
        def delete_many(values: Selection):
            return self.change(values.job_ids, True)

        @router.post('/batch-restore')
        def restore_many(values: Selection):
            return self.change(values.job_ids, False)

        @router.delete('/{key}')
        def delete(key: str):
            return self.change([key], True)

        @router.post('/{key}/restore')
        def restore(key: str):
            return self.change([key], False)

        @router.get('/{key}/poster')
        def poster(key: str):
            if not key.startswith('remix:'):
                raise HTTPException(404, '封面不存在。')
            p = self.remixes.get(key.removeprefix('remix:'))
            if p.get('library_deleted_at') or not p.get('run'):
                raise HTTPException(404, '封面不存在。')
            folder = self.remixes.projects / p['id'] / p['run']['id']
            for name in ['image-000.png', 'master.png']:
                if (folder / name).is_file():
                    return FileResponse(folder / name)
            if p.get('reference_id'):
                return FileResponse(self.remixes.asset(p['reference_id'])[1].parent / 'poster.jpg')
            raise HTTPException(404, '封面尚未生成。')

        return router
