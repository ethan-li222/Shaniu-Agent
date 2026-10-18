"""Owned asynchronous motion enhancement jobs; original engines stay read-only."""
import asyncio
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import aiohttp

from .config import ROOT
from .mcp.common import ToolError


def parse_srt(text):
    cues = []
    pattern = r'(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})'
    for block in re.split(r'\n\s*\n', text.replace('\r', '').strip()):
        lines = block.splitlines()
        index = next((i for i, line in enumerate(lines) if '-->' in line), None)
        if index is None:
            continue
        stamps = re.findall(pattern, lines[index])
        if len(stamps) != 2:
            raise ToolError('字幕时间格式无效。')
        values = [int(h)*3600+int(m)*60+int(s)+int(ms)/1000 for h, m, s, ms in stamps]
        if values[1] <= values[0] or (cues and values[0] < cues[-1]['t0']):
            raise ToolError('字幕时间顺序无效。')
        literal = '\n'.join(lines[index+1:]).strip()
        if literal:
            cues.append(dict(id=len(cues)+1, t0=values[0], t1=values[1], zh=literal, en='', formatting='srt-basic'))
    return cues


def plan(subject, cues):
    """Sentence-level timing only. Cards quote input instead of inventing facts."""
    cards = [dict(t0=0., t1=4., text=subject[:80], kind='title')]
    previous = 0.
    for cue in cues:
        if cue['t0'] < max(5., previous+8.) or cue['t1']-cue['t0'] < 1.5:
            continue
        text = re.sub(r'<[^>]*>', '', cue['zh']).replace('\n', ' ').strip()
        if len(text) > 48:
            continue
        cards.append(dict(t0=cue['t0'], t1=min(cue['t1'], cue['t0']+5.), text=text, kind='point'))
        previous = cue['t0']
        if len(cards) >= 24:
            break
    return cards


class MotionTasks:
    def __init__(self, settings, store, moneyprinter):
        self.settings, self.store, self.moneyprinter = settings, store, moneyprinter
        self.root = store.path.parent / 'motion'
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.slot = asyncio.Semaphore(1)
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS motion_jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES external_tasks(task_id),
                data TEXT NOT NULL)''')
            for row in db.execute('SELECT id,data FROM motion_jobs').fetchall():
                data = json.loads(row['data'])
                if data['status'] in {'submitted', 'processing'}:
                    data.update(status='failed', failure_reason='增强期间服务重启，请重新提交；普通版仍保留。')
                    db.execute('UPDATE motion_jobs SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), row['id']))

    def update(self, job_id, **values):
        with self.store.connect() as db:
            row = db.execute('SELECT data FROM motion_jobs WHERE id=?', (job_id,)).fetchone()
            if row:
                data = json.loads(row['data'])
                data.update(values)
                db.execute('UPDATE motion_jobs SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), job_id))

    def latest(self, owner, task_id):
        with self.store.connect() as db:
            row = db.execute('SELECT id,data FROM motion_jobs WHERE owner=? AND task_id=? ORDER BY rowid DESC LIMIT 1', (owner, task_id)).fetchone()
        return dict(job_id=row['id'], **json.loads(row['data'])) if row else None

    def file(self, owner, task_id, job_id, filename):
        if filename not in {'enhanced.mp4', 'project.zip', 'report.json'}:
            raise ToolError('文件不存在。')
        with self.store.connect() as db:
            row = db.execute('SELECT data FROM motion_jobs WHERE id=? AND owner=? AND task_id=?', (job_id, owner, task_id)).fetchone()
        if not row or json.loads(row['data'])['status'] != 'completed':
            raise ToolError('增强文件不存在或尚未完成。')
        path = self.root / job_id / filename
        if not path.is_file():
            raise ToolError('增强文件不存在。')
        return path

    def music_directory(self, owner):
        path = self.root / 'music' / hashlib.sha256(owner.encode()).hexdigest()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def music(self, owner, music_id):
        if not isinstance(music_id, str) or not re.fullmatch(r'[0-9a-f]{32}', music_id):
            raise ToolError('配乐 ID 无效。')
        path = self.music_directory(owner) / (music_id+'.audio')
        if not path.is_file():
            raise ToolError('配乐不存在或不属于当前用户。')
        return path

    async def submit(self, owner, task_id, options):
        record = self.store.external_task(owner, task_id)
        if not record or record['kind'] != 'video':
            raise ToolError('请选择本用户已完成的视频任务。')
        if options.get('style', 'clean') not in {'clean', 'ink'}:
            raise ToolError('风格必须为 clean 或 ink。')
        if any(not isinstance(options.get(k, True), bool) for k in ('subtitles', 'sound_effects')):
            raise ToolError('字幕和音效必须为布尔值。')
        music = self.music(owner, options['music_id']) if options.get('music_id') else None
        result = await self.moneyprinter.call(owner, 'moneyprinter_status', dict(task_id=task_id))
        if result['status'] != 'completed':
            raise ToolError('请等待普通视频生成完成，再制作增强版。')
        files = result.get('artifacts', [])
        video = next((f for f in files if f['kind'] == 'combined_videos'), None)
        voice = next((f for f in files if f['kind'] == 'audio_file'), None)
        subtitle = next((f for f in files if f['kind'] == 'subtitle_path'), None)
        if not video or not voice:
            raise ToolError('此任务缺少基础合成视频或独立旁白，请用当前 MoneyPrinter 包装服务重新生成。')
        if options.get('subtitles', True) and not subtitle:
            raise ToolError('此任务未生成 SRT；请关闭增强版字幕，或重新生成带字幕的普通版。')
        async with self.lock:
            current = self.latest(owner, task_id)
            if current and current['status'] in {'submitted', 'processing'}:
                return dict(task_id=task_id, enhancement=current, message='此任务已有增强版正在生成。')
            if sum(not task.done() for task in self.tasks.values()) >= 5:
                raise ToolError('增强队列已满，请稍后重试。')
            job_id = uuid4().hex
            data = dict(status='submitted', progress=0, stage='等待动效渲染', style=options.get('style', 'clean'), subtitles=options.get('subtitles', True), sound_effects=options.get('sound_effects', True))
            with self.store.connect() as db:
                db.execute('INSERT INTO motion_jobs VALUES (?,?,?,?)', (job_id, owner, task_id, json.dumps(data, ensure_ascii=False)))
            self.tasks[job_id] = asyncio.create_task(self.run(job_id, record, data, video, voice, subtitle, music))
            self.tasks[job_id].add_done_callback(lambda _: self.tasks.pop(job_id, None))
        return dict(task_id=task_id, enhancement=dict(job_id=job_id, **data), message='动效增强任务已提交，普通版保留。')

    async def download(self, task_id, artifact, destination, limit):
        allowed = self.moneyprinter.artifact(task_id, artifact['url'])
        if not allowed:
            raise ToolError('基础素材地址不合法。')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as client:
            async with client.get(allowed['url'], allow_redirects=False) as response:
                if response.status != 200:
                    raise ToolError('无法读取基础素材。')
                size = 0
                with destination.open('wb') as output:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > limit:
                            raise ToolError('基础素材超出当前增强功能的大小限制。')
                        output.write(chunk)

    async def run(self, job_id, record, options, video, voice, subtitle, music):
        process = None
        try:
            async with self.slot:
                self.update(job_id, status='processing', progress=5, stage='准备基础视频与时间轴')
                directory = self.root / job_id
                directory.mkdir()
                await self.download(record['task_id'], video, directory / 'base.mp4', 512*1024*1024)
                await self.download(record['task_id'], voice, directory / 'voice.audio', 64*1024*1024)
                cues = []
                if subtitle:
                    await self.download(record['task_id'], subtitle, directory / 'source.srt', 1024*1024)
                    cues = parse_srt((directory / 'source.srt').read_text(encoding='utf-8-sig'))
                    if options.get('subtitles', True) and not cues:
                        raise ToolError('SRT 没有有效字幕，请关闭增强字幕或重新生成时间轴。')
                if music:
                    shutil.copy2(music, directory / 'music.audio')
                manifest = dict(schema='shaniu-motion/1', subject=record['subject'], **options, cues=cues,
                                cards=plan(record['subject'], cues), fps=24, music=bool(music))
                (directory / 'project.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
                for name in ['motion-primitives.js', 'subtitles.js', 'audiolib.py', 'LICENSE', 'SOURCE.json', 'THIRD_PARTY.md']:
                    shutil.copy2(ROOT / 'vendor/adu-motion' / name, directory / name)
                for name in ['index.html', 'motion.js', 'style.css', 'render.py']:
                    shutil.copy2(ROOT / 'motion' / name, directory / name)
                python = Path(self.settings.media_python)
                if not python.is_file():
                    python = Path(sys.executable)
                with (directory / 'worker.log').open('wb') as log:
                    process = await asyncio.create_subprocess_exec(str(python), '-X', 'utf8', str(directory / 'render.py'), str(directory), stdout=asyncio.subprocess.PIPE, stderr=log)
                    async with asyncio.timeout(3600):
                        async for line in process.stdout:
                            try:
                                progress = json.loads(line)
                                self.update(job_id, progress=min(99, max(5, int(progress['progress']))), stage=str(progress['stage'])[:80])
                            except (ValueError, KeyError, TypeError):
                                pass
                        code = await process.wait()
                    if code:
                        raise ToolError('动效渲染未完成，请检查媒体环境、视频时长或配乐时长；普通版仍可播放。')
                if not all((directory / name).is_file() for name in ['enhanced.mp4', 'project.zip', 'report.json']):
                    raise ToolError('增强流程未返回完整结果。')
                self.update(job_id, status='completed', progress=100, stage='动效增强完成')
        except asyncio.CancelledError:
            self.update(job_id, status='failed', failure_reason='增强已中止，普通版仍保留。')
            raise
        except Exception as error:
            self.update(job_id, status='failed', failure_reason=str(error) if isinstance(error, ToolError) else '增强失败，请检查本机媒体环境；普通版仍保留。')
        finally:
            if process and process.returncode is None:
                # This worker owns its Chrome/FFmpeg children. Windows terminate()
                # alone leaves those children running and holding project files.
                import os
                if os.name == 'nt':
                    cleanup = await asyncio.create_subprocess_exec('taskkill','/PID',str(process.pid),'/T','/F',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                    await cleanup.wait()
                    await process.wait()
                else:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()

    async def shutdown(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
