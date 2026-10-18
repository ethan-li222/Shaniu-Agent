"""Keep CUDA inference isolated from the web server's Python environment."""
import asyncio
import json
import os
import shutil
from uuid import uuid4
from .config import ROOT
from .avatar_catalog import DEFAULT_AVATAR, avatar_ready


def capabilities(settings):
    ready = all(p.exists() for p in [settings.media_python,
        settings.avatar_path / 'src/wav2lip/models', ROOT / 'data/avatar/wav2lip.pth',
        ROOT / 'data/avatar/manifest.json',
        ROOT / 'data/avatar/wav2lip_avatar_long_hair_girl/full_imgs',
        ROOT / 'data/avatar/wav2lip_avatar_long_hair_girl/face_imgs',
        ROOT / 'data/avatar/wav2lip_avatar_long_hair_girl/coords.pkl'])
    rtc = ready and (settings.media_python.parent.parent / 'Lib/site-packages/aiortc/__init__.py').is_file()
    return {'status': 'realtime_ready' if rtc else 'clip_ready' if ready else 'not_connected', 'clip_ready': ready,
            'realtime_ready': rtc, 'expression': 'lightweight_warp_or_independent_asset',
            'message': '支持本地 WebRTC 分句音视频和 MP4；默认表情为轻量变形，独立情绪素材按需配置。'}


class AvatarEngine:
    def __init__(self, settings):
        self.settings = settings
        self.jobs = {}
        self.files = {}
        self.busy = False

    async def cancel(self, cid):
        task = self.jobs.get(cid)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for key, (conversation, path) in list(self.files.items()):
            if conversation == cid:
                for suffix in ['.mp4', '.wav', '.srt', '.vtt']:
                    path.with_suffix(suffix).unlink(missing_ok=True)
                self.files.pop(key, None)

    async def render(self, cid, text, emotion, subtitles=True, avatar_id=DEFAULT_AVATAR):
        if not avatar_ready(avatar_id):
            raise RuntimeError('人物素材尚未准备好')
        if not capabilities(self.settings)['clip_ready']:
            raise RuntimeError('请先准备媒体环境和数字人素材')
        if self.busy:
            raise RuntimeError('数字人正在生成视频，请稍后再试')
        self.busy = True
        task = asyncio.current_task()
        self.jobs[cid] = task
        jobid = str(uuid4())
        output = ROOT / 'data/media' / (jobid + '.mp4')
        process = None
        success = False
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.settings.media_python), str(ROOT / 'scripts/render_avatar.py'),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env={k: v for k, v in os.environ.items() if k != 'SHANIU_API_KEY'})
            payload = dict(text=text, emotion=emotion, avatar_id=avatar_id, source=str(self.settings.avatar_path), output=str(output), speech=self.settings.speech_config, subtitles=subtitles)
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(payload).encode('utf-8')), 180)
            try:
                report = json.loads(stdout.decode('utf-8').strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise RuntimeError('媒体进程启动失败，请检查媒体环境') from None
            if process.returncode or not report.get('ok') or not output.is_file():
                raise RuntimeError('视频生成失败，请检查本机中文语音和 CUDA 环境')
            self.files[jobid] = (cid, output)
            success = True
            url = f'/api/conversations/{cid}/media/{jobid}'
            return dict(id=jobid, url=url, audio_url=url+'/audio', subtitle_url=url+'/srt', vtt_url=url+'/vtt', metrics=report)
        except asyncio.TimeoutError:
            raise RuntimeError('视频生成超时，请缩短回答后重试') from None
        finally:
            if process and process.returncode is None:
                if os.name == 'nt':
                    killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()
                else:
                    process.kill()
                await process.wait()
            if not success:
                for suffix in ['.mp4', '.wav', '.srt', '.vtt']:
                    output.with_suffix(suffix).unlink(missing_ok=True)
            for work in output.parent.glob(f'work-{jobid}-*'):
                if work.is_dir() and work.resolve().is_relative_to((ROOT / 'data/media').resolve()):
                    shutil.rmtree(work)
            if self.jobs.get(cid) is task:
                self.jobs.pop(cid, None)
            self.busy = False
