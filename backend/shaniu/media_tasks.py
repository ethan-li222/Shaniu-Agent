"""Read-only tracking and same-origin delivery of conversation media tasks."""
import asyncio
import time

from .mcp.common import ToolError


class MediaTasks:
    def __init__(self, store, moneyprinter):
        self.store, self.moneyprinter = store, moneyprinter
        self.locks = {}
        self.slots = asyncio.Semaphore(4)

    async def status(self, owner, record):
        tid = record['task_id']
        async with self.locks.setdefault(tid, asyncio.Lock()):
            cached, updated = self.store.cached_external_task(tid)
            if cached:
                cached = dict(cached)
                cached['postprocess'] = self.moneyprinter.post.list(owner, tid)
                enhancement = self.moneyprinter.motion.latest(owner, tid)
                if enhancement:
                    cached['enhancement'] = enhancement
            if cached and (cached.get('status') in {'completed', 'failed'} or time.time()-updated < 3):
                return {**record, **cached}
            result = cached or dict(task_id=tid, status='submitted', progress=0, artifacts=[])
            try:
                async with self.slots:
                    result = await self.moneyprinter.call(owner, 'moneyprinter_status', dict(task_id=tid))
            except ToolError:
                # A temporarily unavailable service is not a failed generation.
                result = {**result, 'connection_error': '暂时无法获取进度，正在自动重连。'}
            self.store.cache_external_task(tid, result)
            return {**record, **result}

    async def list(self, owner, cid):
        records = self.store.conversation_external_tasks(owner, cid)
        return await asyncio.gather(*(self.status(owner, record) for record in records))

    def public(self, cid, records):
        result = []
        for record in records:
            record = dict(record)
            record['artifacts'] = [dict(file, url=f"/api/conversations/{cid}/media-tasks/{record['task_id']}/files/{file['filename']}")
                                   for file in record.get('artifacts', [])]
            if record.get('enhancement'):
                enhancement = dict(record['enhancement'])
                if enhancement['status'] == 'completed':
                    base = f"/api/conversations/{cid}/media-tasks/{record['task_id']}/motion/{enhancement['job_id']}"
                    enhancement['video_url'] = base+'/enhanced.mp4'
                    enhancement['project_url'] = base+'/project.zip'
                    enhancement['report_url'] = base+'/report.json'
                record['enhancement'] = enhancement
            jobs = []
            for job in record.get('postprocess', []):
                job = dict(job)
                base = f"/api/conversations/{cid}/media-tasks/{record['task_id']}/post/{job['job_id']}"
                job['artifacts'] = [dict(filename=name, url=base+'/'+name) for name in job.get('files', [])]
                jobs.append(job)
            record['postprocess'] = jobs
            result.append(record)
        return result
