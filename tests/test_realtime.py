import asyncio
import tempfile
import unittest
from pathlib import Path
from aiohttp import CookieJar, ClientSession, DummyCookieJar
from aiohttp.test_utils import TestClient, TestServer
from shaniu.config import Settings
from shaniu.realtime import SentenceBuffer
from shaniu.server import create_app, SERVICE


class SentenceTests(unittest.TestCase):
    def test_commas_keep_context_and_paragraph_breaks_survive(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.feed('如果今天学习累了，可以先休息一下，再继续。'), ['如果今天学习累了，可以先休息一下，再继续。'])
        self.assertEqual(buffer.feed('第一段\n'), ['第一段\n'])

    def test_fragmentation_preserves_order_and_bounds(self):
        source = '你好，我是傻妞。' + '继续学习' * 80 + '！最后一句'
        for size in [1, 7, 101, len(source)]:
            buffer = SentenceBuffer()
            output = []
            for start in range(0, len(source), size):
                output.extend(buffer.feed(source[start:start+size]))
            output.extend(buffer.feed('', final=True))
            self.assertEqual(''.join(output), source)
            self.assertTrue(all(1 <= len(part) <= 100 for part in output))


class FakeBridge:
    def __init__(self, cid):
        self.cid = cid
        self.sid = 'connection'
        self.calls = []
        self.fail_speech = False

    def connected(self, cid, sid=None):
        return cid == self.cid and (sid is None or sid == self.sid)

    async def request(self, op, data=None):
        self.calls.append((op, data))
        if op == 'sentence' and self.fail_speech:
            raise RuntimeError('测试媒体断开')
        return {'playing': False}

    async def interrupt(self, cid):
        self.calls.append(('interrupt', cid))

    async def close(self, cid, sid=None):
        self.calls.append(('close', sid))

    async def shutdown(self):
        pass


class Provider:
    async def stream(self, history, emotion, memories):
        yield '第一句话。'
        await asyncio.sleep(.01)
        yield '第二句话。'


class RealtimeTests(unittest.IsolatedAsyncioTestCase):
    headers = {'X-Shaniu-Client': 'web'}

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Settings(database=Path(self.tmp.name)/'test.db'), Provider())
        self.service = self.app[SERVICE]
        self.client = TestClient(TestServer(self.app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.get('/api/health')
        record = await (await self.client.post('/api/conversations', json={}, headers=self.headers)).json()
        self.cid = record['id']
        self.base = '/api/conversations/'+self.cid
        self.bridge = FakeBridge(self.cid)
        self.service.realtime = self.bridge

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def complete(self):
        response = await self.client.post(self.base+'/turns', json={'text': 'hello', 'playback': 'realtime', 'connection_id': self.bridge.sid}, headers=self.headers)
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(self.service.tasks[self.cid], 2)

    async def test_sentence_streaming_and_finish(self):
        await self.complete()
        sentences = [data['text'] for op, data in self.bridge.calls if op == 'sentence']
        self.assertEqual(sentences, ['第一句话。', '第二句话。'])
        self.assertIn('finish', [op for op, _ in self.bridge.calls])
        self.assertEqual(self.service.store.messages(self.cid)[-1]['status'], 'done')

    async def test_media_failure_preserves_completed_text(self):
        self.bridge.fail_speech = True
        await self.complete()
        message = self.service.store.messages(self.cid)[-1]
        self.assertEqual(message['status'], 'done')
        self.assertEqual(message['content'], '第一句话。第二句话。')
        self.assertNotIn('finish', [op for op, _ in self.bridge.calls])

    async def test_markdown_stream_is_saved_raw_but_media_gets_plain_text(self):
        class MarkdownProvider:
            async def stream(self, *args):
                for part in ['# 介绍\n\n我是 **傻', '妞**。请看 [文档](https://exam', 'ple.com)。\n\n```python\nprint(1)\n```']:
                    yield part
        self.service.provider = MarkdownProvider()
        await self.complete()
        script = ''.join(data['text'] for op, data in self.bridge.calls if op == 'sentence')
        self.assertIn('我是 傻妞。请看 文档。', script)
        self.assertIn('代码内容请查看屏幕。', script)
        self.assertNotIn('https', script)
        self.assertNotIn('**', script)
        saved = self.service.store.messages(self.cid)[-1]
        self.assertIn('**傻妞**', saved['content'])
        history = await (await self.client.get(self.base+'/messages')).json()
        self.assertNotIn('https', history['messages'][-1]['speech_text'])

    async def test_stale_connection_and_owner_are_rejected(self):
        response = await self.client.post(self.base+'/turns', json={'text': 'hello', 'playback': 'realtime', 'connection_id': 'old'}, headers=self.headers)
        self.assertEqual(response.status, 409)
        self.assertFalse(self.service.store.messages(self.cid))
        url = self.base+'/media/connection/'+self.bridge.sid
        async with ClientSession(cookie_jar=DummyCookieJar()) as stranger:
            self.assertEqual((await stranger.get(self.client.make_url(url))).status, 404)
        self.assertEqual((await self.client.get(self.base+'/media/connection/old')).status, 404)
        self.assertEqual((await self.client.post(self.base+'/media/offer', json={'type': 'answer', 'sdp': 'fake'}, headers=self.headers)).status, 400)
        self.assertEqual((await self.client.post(self.base+'/turns', json={'text': 'hello', 'expression_strength': 99}, headers=self.headers)).status, 400)
