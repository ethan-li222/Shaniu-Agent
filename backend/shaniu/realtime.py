"""Authenticated, process-owned loopback bridge. aiortc/torch stay in media Python."""
import asyncio
import json
import os
import secrets
from uuid import uuid4
from aiohttp import ClientSession, ClientTimeout, ClientError
from .config import ROOT


class SentenceBuffer:
    def __init__(self):
        self.text = ''

    def feed(self, delta, final=False):
        self.text += delta
        output = []
        while self.text:
            cut = next((i+1 for i, char in enumerate(self.text[:100]) if char in '。！？!?；;\n' or (i >= 48 and char in '，,' and not self.text[i-1].isdigit())), 0)
            if not cut:
                cut = 100 if len(self.text) >= 100 else len(self.text) if final else 0
            if not cut:
                break
            part, self.text = self.text[:cut].strip(' \t\r'), self.text[cut:]
            if part.strip():
                output.append(part)
        return output


class RealtimeBridge:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.client = None
        self.port = None
        self.token = secrets.token_urlsafe(32)
        self.cid = None
        self.sid = None
        self.lock = asyncio.Lock()

    def connected(self, cid, sid=None):
        return self.cid == cid and self.sid is not None and (sid is None or sid == self.sid)

    async def start(self):
        if self.process and self.process.returncode is None:
            return
        if self.client:
            await self.client.close()
        env = {k: v for k, v in os.environ.items() if k != 'SHANIU_API_KEY'}
        self.process = await asyncio.create_subprocess_exec(str(self.settings.media_python), str(ROOT / 'scripts/media_service.py'),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)
        self.process.stdin.write(json.dumps(dict(token=self.token, source=str(self.settings.avatar_path), speech=self.settings.speech_config)).encode()+b'\n')
        await self.process.stdin.drain()
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), 45)
            self.port = json.loads(line)['port']
        except (ValueError, KeyError, asyncio.TimeoutError):
            await self.shutdown()
            raise RuntimeError('实时媒体服务启动失败，请安装 requirements-media.txt 并检查 CUDA 环境') from None
        self.client = ClientSession(timeout=ClientTimeout(total=45))

    async def request(self, op, data=None):
        if not self.client or not self.process or self.process.returncode is not None:
            raise RuntimeError('实时媒体服务已断开，请重新连接')
        try:
            async with self.client.post(f'http://127.0.0.1:{self.port}/{op}', json=data or {}, headers={'X-Media-Token': self.token}) as response:
                if response.status != 200:
                    raise RuntimeError('实时媒体请求失败，请重新连接或使用手动视频')
                return await response.json()
        except (ClientError, OSError, asyncio.TimeoutError):
            raise RuntimeError('实时媒体连接失败，请重新连接或使用手动视频') from None

    async def offer(self, cid, data):
        async with self.lock:
            if self.cid and self.cid != cid:
                raise RuntimeError('本机当前只支持一个实时数字人连接，请先断开原连接')
            await self.start()
            answer = await self.request('offer', data)
            self.cid, self.sid = cid, str(uuid4())
            return dict(answer, connection_id=self.sid)

    async def interrupt(self, cid):
        if self.connected(cid):
            try:
                await self.request('interrupt')
            except RuntimeError:
                self.cid = self.sid = None

    async def close(self, cid, sid=None):
        async with self.lock:
            if self.connected(cid, sid):
                try:
                    await self.request('close')
                finally:
                    self.cid = self.sid = None

    async def shutdown(self):
        if self.client:
            await self.client.close()
            self.client = None
        process = self.process
        if process and process.returncode is None:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 8)
            except asyncio.TimeoutError:
                if os.name == 'nt':
                    killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()
                else:
                    process.kill()
                await process.wait()
        self.process = None
        self.cid = self.sid = None
