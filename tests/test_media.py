import asyncio
import tempfile
import unittest
from pathlib import Path
from aiohttp import CookieJar, ClientSession, DummyCookieJar
from aiohttp.test_utils import TestClient, TestServer
from shaniu.config import Settings
from shaniu.server import create_app, SERVICE


class FakeEngine:
    def __init__(self, root):
        self.busy = False
        self.jobs = {}
        self.files = {}
        self.root = root
        self.slow = False
        self.cancelled = False

    async def cancel(self, cid):
        task = self.jobs.pop(cid, None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.files.clear()

    async def render(self, cid, text, emotion, subtitles=True, avatar_id='wav2lip_avatar_long_hair_girl'):
        self.last_avatar_id = avatar_id
        self.last_text = text
        self.busy = True
        try:
            if self.slow:
                await asyncio.sleep(30)
            path = self.root / 'clip.mp4'
            path.write_bytes(b'0000ftypvideo')
            self.files['clip'] = (cid, path)
            return {'url': f'/api/conversations/{cid}/media/clip', 'metrics': {}}
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.busy = False


class MediaTests(unittest.IsolatedAsyncioTestCase):
    headers = {'X-Shaniu-Client': 'web'}

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = create_app(Settings(database=self.root / 'test.db'))
        self.service = self.app[SERVICE]
        self.engine = FakeEngine(self.root)
        self.service.avatar = self.engine
        self.client = TestClient(TestServer(self.app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.get('/api/health')
        record = await (await self.client.post('/api/conversations', json={}, headers=self.headers)).json()
        self.cid = record['id']
        self.base = f'/api/conversations/{self.cid}'
        self.mid = self.service.store.add_message(self.cid, 'test-turn', 'assistant', 'hello')

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_media_validation_ownership_and_range(self):
        invalid = await self.client.post(self.base + '/media', json={'message_id': 'missing'}, headers=self.headers)
        self.assertEqual(invalid.status, 400)
        long_id = self.service.store.add_message(self.cid, 'long', 'assistant', 'x' * 241)
        self.assertEqual((await self.client.post(self.base + '/media', json={'message_id': long_id}, headers=self.headers)).status, 400)
        response = await self.client.post(self.base + '/media', json={'message_id': self.mid}, headers=self.headers)
        self.assertEqual(response.status, 200)
        url = (await response.json())['url']
        video = await self.client.get(url, headers={'Range': 'bytes=0-7'})
        self.assertEqual(video.status, 206)
        self.assertEqual(await video.read(), b'0000ftyp')
        async with ClientSession(cookie_jar=DummyCookieJar()) as other:
            self.assertEqual((await other.get(self.client.make_url(url))).status, 404)
        await self.client.post(self.base + '/interrupt', headers=self.headers)
        self.assertEqual((await self.client.get(url)).status, 404)

    async def test_interrupt_cancels_media_without_late_result(self):
        self.engine.slow = True
        request = asyncio.create_task(self.client.post(self.base + '/media', json={'message_id': self.mid}, headers=self.headers))
        for _ in range(100):
            if self.engine.busy:
                break
            await asyncio.sleep(.01)
        self.assertTrue(self.engine.busy)
        self.assertEqual((await self.client.post(self.base + '/media', json={'message_id': self.mid}, headers=self.headers)).status, 409)
        await self.client.post(self.base + '/interrupt', headers=self.headers)
        self.assertEqual((await request).status, 409)
        self.assertTrue(self.engine.cancelled)
        self.assertFalse(self.engine.files)

    async def test_markdown_clip_uses_clean_script_and_clean_length_limit(self):
        raw = '# 问候\n\n**你好**，请看 [文档](https://example.com/' + 'x' * 400 + ')。'
        mid = self.service.store.add_message(self.cid, 'markdown-turn', 'assistant', raw)
        result = await self.client.post(self.base + '/media', json={'message_id': mid}, headers=self.headers)
        self.assertEqual(result.status, 200)
        self.assertEqual(self.engine.last_text, '问候\n你好，请看 文档。')
        self.service.store.avatar(self.cid, 'wav2lip_avatar_glass_man')
        result = await self.client.post(self.base + '/media', json={'message_id': mid}, headers=self.headers)
        self.assertEqual(result.status, 200)
        self.assertEqual(self.engine.last_avatar_id, 'wav2lip_avatar_glass_man')
        empty = self.service.store.add_message(self.cid, 'empty-turn', 'assistant', '---')
        result = await self.client.post(self.base + '/media', json={'message_id': empty}, headers=self.headers)
        self.assertEqual(result.status, 400)
