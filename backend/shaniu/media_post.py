"""Owned post-processing jobs for completed MoneyPrinter videos."""
import asyncio
import json
import math
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from .config import ROOT
from .mcp.common import ToolError, schema


TOOLS = [dict(name='media_postprocess', description='对本用户已完成的 MoneyPrinter 视频提交裁剪、横竖转换、压缩、封面、字幕格式转换或技术检查任务。保留原版，不调用 AI 视频生成。返回子任务，不代表完成；页面自动显示进度和结果。', inputSchema=schema({
    'task_id':dict(type='string'), 'operation':dict(type='string',enum=['cut','reframe','compress','cover','subtitle','review']),
    'start':dict(type='number',minimum=0), 'end':dict(type='number',exclusiveMinimum=0),
    'time':dict(type='number',minimum=0,default=1), 'aspect':dict(type='string',enum=['9:16','16:9','1:1'],default='9:16'),
    'mode':dict(type='string',enum=['blur','crop'],default='blur'), 'crf':dict(type='integer',minimum=18,maximum=35,default=28),
    'format':dict(type='string',enum=['srt','vtt','ass'],default='vtt'),
    'source':dict(type='string',enum=['original','clean'],default='original',description='original 保留成片音效和已烧录字幕；clean 用无字幕底片与原旁白重制，不保留原成片 BGM。'),
    'subtitles':dict(type='boolean',default=False,description='仅 clean 生效，是否重新烧录原 SRT。')}, ['task_id','operation'])),
    dict(name='media_post_status',description='查询本用户一个视频的后期子任务，返回真实进度和产物。',inputSchema=schema({'task_id':dict(type='string')},['task_id']))]

ALLOWED = {'result.mp4','cover.jpg','review.jpg','subtitles.srt','subtitles.vtt','subtitles.ass','report.json'}


def options(arguments):
    permitted = set(TOOLS[0]['inputSchema']['properties'])
    if set(arguments)-permitted:
        raise ToolError('包含未知的后期处理参数。')
    operation = arguments.get('operation')
    if operation not in {'cut','reframe','compress','cover','subtitle','review'}:
        raise ToolError('后期操作无效。')
    result = dict(operation=operation, source=arguments.get('source','original'), subtitles=arguments.get('subtitles',False),
                  aspect=arguments.get('aspect','9:16'), mode=arguments.get('mode','blur'), format=arguments.get('format','vtt'), crf=arguments.get('crf',28))
    if result['source'] not in {'original','clean'} or not isinstance(result['subtitles'],bool):
        raise ToolError('源视频或字幕选项无效。')
    if result['source']=='original' and result['subtitles']:
        raise ToolError('请使用无字幕底片重制后添加字幕；原版已有字幕会直接保留。')
    if result['aspect'] not in {'9:16','16:9','1:1'} or result['mode'] not in {'blur','crop'} or result['format'] not in {'srt','vtt','ass'}:
        raise ToolError('画幅、裁切或字幕格式无效。')
    if type(result['crf']) is not int or not 18 <= result['crf'] <= 35:
        raise ToolError('压缩质量范围为 18–35。')
    for key in ('start','end','time'):
        value = arguments.get(key,1 if key=='time' else 0)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 <= value <= 600:
            raise ToolError('时间须为 0–600 秒的数字。')
        result[key] = float(value)
    if operation=='cut' and result['end'] <= result['start']:
        raise ToolError('裁剪结束时间必须晚于开始时间。')
    return result


class PostTasks:
    def __init__(self, settings, store, moneyprinter):
        self.settings,self.store,self.moneyprinter = settings,store,moneyprinter
        self.root=store.path.parent/'media-post'; self.root.mkdir(parents=True,exist_ok=True)
        self.tasks={}; self.lock=asyncio.Lock(); self.slot=asyncio.Semaphore(1)
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS media_post_jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES external_tasks(task_id), data TEXT NOT NULL)')
            for row in db.execute('SELECT id,data FROM media_post_jobs').fetchall():
                value=json.loads(row['data'])
                if value['status'] in {'submitted','processing'}:
                    value.update(status='failed',failure_reason='后期处理期间服务重启，请重新提交。')
                    db.execute('UPDATE media_post_jobs SET data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),row['id']))

    def update(self, job_id, **values):
        with self.store.connect() as db:
            row=db.execute('SELECT data FROM media_post_jobs WHERE id=?',(job_id,)).fetchone()
            if row:
                data=json.loads(row['data']); data.update(values)
                db.execute('UPDATE media_post_jobs SET data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),job_id))

    def list(self, owner, task_id):
        with self.store.connect() as db:
            rows=db.execute('SELECT id,data FROM media_post_jobs WHERE owner=? AND task_id=? ORDER BY rowid DESC LIMIT 30',(owner,task_id)).fetchall()
        return [dict(job_id=row['id'],**json.loads(row['data'])) for row in rows]

    def file(self, owner, task_id, job_id, filename):
        if filename not in ALLOWED or not re.fullmatch(r'[0-9a-f]{32}',job_id):
            raise ToolError('后期文件不存在。')
        job=next((job for job in self.list(owner,task_id) if job['job_id']==job_id),None)
        if not job or job['status']!='completed' or filename not in job.get('files',[]):
            raise ToolError('文件不存在、未完成或不属于当前用户。')
        path=self.root/job_id/filename
        if not path.is_file():
            raise ToolError('文件不存在。')
        return path

    async def call(self, owner, name, arguments):
        tid=arguments.get('task_id')
        if not isinstance(tid,str) or not self.store.external_task(owner,tid):
            raise ToolError('任务不存在或不属于当前用户。')
        if name=='media_post_status':
            if set(arguments)!={'task_id'}:
                raise ToolError('查询参数无效。')
            return dict(task_id=tid,postprocess=self.list(owner,tid))
        if name!='media_postprocess':
            raise ToolError('未知后期工具。')
        config=options(arguments)
        record=self.store.external_task(owner,tid)
        if record['kind']!='video':
            raise ToolError('请选择视频任务。')
        status=await self.moneyprinter.call(owner,'moneyprinter_status',dict(task_id=tid))
        if status['status']!='completed':
            raise ToolError('请等待视频生成完成。')
        artifacts=status.get('artifacts',[])
        kind='combined_videos' if config['source']=='clean' and config['operation'] in {'cut','reframe','compress'} else 'videos'
        source=next((f for f in artifacts if f['kind']==kind),None)
        voice=next((f for f in artifacts if f['kind']=='audio_file'),None)
        subtitle=next((f for f in artifacts if f['kind']=='subtitle_path'),None)
        if not source or (kind=='combined_videos' and not voice):
            raise ToolError('任务缺少所选底片或独立旁白，请选择保留原成片。')
        if (config['operation']=='subtitle' or (kind=='combined_videos' and config['subtitles'])) and not subtitle:
            raise ToolError('原任务没有 SRT，无法导出或添加字幕。')
        async with self.lock:
            jobs=self.list(owner,tid)
            active=next((job for job in jobs if job['status'] in {'submitted','processing'}),None)
            if active:
                if active['options'] != config:
                    raise ToolError('此视频已有后期任务正在处理，请等待完成。')
                return dict(task_id=tid,postprocess=active,message='相同后期任务已在处理，不重复提交。')
            if len(jobs)>=30 or len(self.tasks)>=5:
                raise ToolError('后期任务数量已达限制，请稍后重试或使用新的视频任务。')
            job_id=uuid4().hex
            data=dict(status='submitted',progress=0,stage='等待后期处理',operation=config['operation'],options=config,files=[])
            with self.store.connect() as db:
                db.execute('INSERT INTO media_post_jobs VALUES (?,?,?,?)',(job_id,owner,tid,json.dumps(data,ensure_ascii=False)))
            self.tasks[job_id]=asyncio.create_task(self.run(job_id,tid,config,source,voice,subtitle))
            self.tasks[job_id].add_done_callback(lambda _:self.tasks.pop(job_id,None))
        return dict(task_id=tid,postprocess=dict(job_id=job_id,**data),message='后期任务已提交，原视频保留，页面自动更新进度。')

    async def run(self, job_id, tid, config, source, voice, subtitle):
        process=None
        try:
            async with self.slot:
                self.update(job_id,status='processing',progress=5,stage='准备媒体')
                directory=self.root/job_id; directory.mkdir()
                download=self.moneyprinter.motion.download
                if config['operation']!='subtitle':
                    await download(tid,source,directory/'source.mp4',512*1024*1024)
                if config['source']=='clean' and config['operation'] in {'cut','reframe','compress'}:
                    await download(tid,voice,directory/'voice.audio',64*1024*1024)
                if subtitle:
                    await download(tid,subtitle,directory/'source.srt',1024*1024)
                (directory/'options.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
                python=Path(self.settings.media_python)
                if not python.is_file(): python=Path(sys.executable)
                with (directory/'worker.log').open('wb') as log:
                    process=await asyncio.create_subprocess_exec(str(python),'-X','utf8',str(ROOT/'scripts/media_post_worker.py'),str(directory),
                                                                stdout=asyncio.subprocess.PIPE,stderr=log)
                    async with asyncio.timeout(3600):
                        async for line in process.stdout:
                            try:
                                value=json.loads(line)
                                self.update(job_id,progress=max(5,min(99,int(value['progress']))),stage=str(value['stage'])[:80])
                            except (ValueError,KeyError,TypeError): pass
                        code=await process.wait()
                    if code:
                        raise ToolError('后期处理失败，请检查操作时间是否超出视频、字幕是否有效及媒体环境；原视频保留。')
                report=json.loads((directory/'report.json').read_text(encoding='utf-8'))
                files=report['files']
                if not files or set(files)-ALLOWED or not all((directory/name).is_file() for name in files):
                    raise ToolError('后期产物不完整。')
                self.update(job_id,status='completed',progress=100,stage='后期处理完成',files=files)
        except asyncio.CancelledError:
            self.update(job_id,status='failed',failure_reason='后期任务已中止，原视频保留。'); raise
        except Exception as error:
            self.update(job_id,status='failed',failure_reason=str(error) if isinstance(error,ToolError) else '后期处理异常，原视频保留。')
        finally:
            if process and process.returncode is None:
                if os.name=='nt':
                    cleanup=await asyncio.create_subprocess_exec('taskkill','/PID',str(process.pid),'/T','/F',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                    await cleanup.wait(); await process.wait()
                else:
                    process.terminate()
                    try: await asyncio.wait_for(process.wait(),5)
                    except TimeoutError: process.kill(); await process.wait()

    async def shutdown(self):
        tasks=list(self.tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
