import asyncio
import tempfile
import unittest
from pathlib import Path

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from shaniu.config import Settings
from shaniu.providers import CompatibleProvider
from shaniu.server import create_app, SERVICE
from shaniu.storage import Store


class SlowProvider:
    async def stream(self, history, emotion, memories):
        yield emotion + ':'
        await asyncio.sleep(30)
        yield 'never'


class LateProvider:
    """A provider that emits one late chunk even after cancellation."""
    async def stream(self, history, emotion, memories):
        yield 'first'
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            yield 'STALE'


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(database=Path(self.tmp.name) / 'test.sqlite3')
        self.client = TestClient(TestServer(create_app(self.settings, SlowProvider())), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.get('/api/health')
        response = await self.client.post('/api/conversations', json={}, headers=self.headers)
        self.cid = (await response.json())['id']
        self.base = '/api/conversations/' + self.cid

    headers = {'X-Shaniu-Client': 'web'}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def read_event(self, ws, event):
        for _ in range(30):
            packet = await asyncio.wait_for(ws.receive_json(), 3)
            if packet['type'] == event:
                return packet
        self.fail('Missing event ' + event)

    async def test_cancel_persists_partial_and_never_finishes(self):
        ws = await self.client.ws_connect(self.base + '/events')
        await ws.receive_json()
        await self.client.post(self.base + '/turns', json={'text': '你好'}, headers=self.headers)
        await self.read_event(ws, 'assistant.delta')
        response = await self.client.post(self.base + '/interrupt', headers=self.headers)
        self.assertEqual(response.status, 200)
        await self.read_event(ws, 'turn.cancelled')
        data = await (await self.client.get(self.base + '/messages')).json()
        self.assertEqual(data['messages'][-1]['status'], 'cancelled')
        self.assertEqual(data['messages'][-1]['content'], 'cool:')
        self.assertIsNone(data['active_turn'])
        await ws.close()

    async def test_late_provider_chunk_cannot_escape_cancel(self):
        self.client.server.app[SERVICE].provider = LateProvider()
        ws = await self.client.ws_connect(self.base + '/events')
        await ws.receive_json()
        await self.client.post(self.base + '/turns', json={'text': '晚到数据'}, headers=self.headers)
        await self.read_event(ws, 'assistant.delta')
        await self.client.post(self.base + '/interrupt', headers=self.headers)
        await self.read_event(ws, 'turn.cancelled')
        data = await (await self.client.get(self.base + '/messages')).json()
        self.assertEqual(data['messages'][-1]['content'], 'first')
        self.assertEqual(data['messages'][-1]['status'], 'cancelled')
        await ws.close()

    async def test_switch_cancels_and_persists_emotion(self):
        ws = await self.client.ws_connect(self.base + '/events')
        await ws.receive_json()
        await self.client.post(self.base + '/turns', json={'text': '你好'}, headers=self.headers)
        await self.read_event(ws, 'assistant.delta')
        await self.client.patch(self.base + '/emotion', json={'emotion': 'happy'}, headers=self.headers)
        await self.read_event(ws, 'emotion.changed')
        data = await (await self.client.get(self.base + '/messages')).json()
        self.assertEqual(data['conversation']['emotion'], 'happy')
        self.assertEqual(data['messages'][-1]['status'], 'cancelled')
        with self.client.server.app[SERVICE].store.connect() as db:
            owner = db.execute('SELECT owner FROM conversations WHERE id=?', (self.cid,)).fetchone()[0]
        reopened = Store(self.settings.database)
        self.assertEqual(reopened.conversation(self.cid, owner)['emotion'], 'happy')
        await ws.close()

    async def test_memories_require_confirmation_and_owner(self):
        response = await self.client.post('/api/memories', json={'content': '叫我小千'}, headers=self.headers)
        self.assertEqual(response.status, 400)
        response = await self.client.post('/api/memories', json={'content': '叫我小千', 'confirmed': True}, headers=self.headers)
        memory = await response.json()
        self.assertEqual(response.status, 201)
        with self.client.server.app[SERVICE].store.connect() as db:
            owner = db.execute('SELECT owner FROM memories WHERE id=?', (memory['id'],)).fetchone()[0]
        self.assertEqual(Store(self.settings.database).memories(owner)[0]['content'], '叫我小千')
        self.client.session.cookie_jar.clear()
        response = await self.client.get(self.base + '/messages')
        self.assertEqual(response.status, 404)
        self.assertEqual(await (await self.client.get('/api/memories')).json(), [])
        response = await self.client.delete('/api/memories/' + memory['id'], headers=self.headers)
        self.assertEqual(response.status, 404)

    async def test_time_tool_returns_actual_result(self):
        ws = await self.client.ws_connect(self.base + '/events')
        await ws.receive_json()
        response = await self.client.post(self.base + '/turns', json={'text': '现在几点了？'}, headers=self.headers)
        self.assertEqual(response.status, 202)
        result = await self.read_event(ws, 'tool.finished')
        self.assertEqual(result['payload']['timezone'], 'Asia/Shanghai')
        done = await self.read_event(ws, 'assistant.done')
        self.assertIn(result['payload']['result'], done['payload']['text'])
        await ws.close()

    async def test_reconnect_snapshot_keeps_partial_text(self):
        ws = await self.client.ws_connect(self.base + '/events')
        await ws.receive_json()
        await self.client.post(self.base + '/turns', json={'text': '继续'}, headers=self.headers)
        await self.read_event(ws, 'assistant.delta')
        await ws.close()
        ws = await self.client.ws_connect(self.base + '/events')
        snapshot = await ws.receive_json()
        self.assertEqual(snapshot['payload']['messages'][-1]['content'], 'cool:')
        self.assertTrue(snapshot['payload']['active_turn'])
        await ws.close()

    async def test_validation_cross_site_and_deletion(self):
        response = await self.client.post(self.base + '/turns', json={'text': 'x' * 4001}, headers=self.headers)
        self.assertEqual(response.status, 400)
        response = await self.client.patch(self.base + '/emotion', json={'emotion': 'not-real'}, headers=self.headers)
        self.assertEqual(response.status, 400)
        response = await self.client.post(self.base + '/turns', json={'text': 'hello'}, headers={**self.headers, 'Origin': 'https://evil.example'})
        self.assertEqual(response.status, 403)
        response = await self.client.post(self.base + '/turns', json={'text': 'hello'})
        self.assertEqual(response.status, 403)
        await self.client.post(self.base + '/turns', json={'text': 'hello'}, headers=self.headers)
        response = await self.client.delete(self.base, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await self.client.get(self.base + '/messages')).status, 404)

    async def test_stream_parser_handles_split_utf8_and_lines(self):
        async def stream(request):
            payload = await request.json()
            self.assertEqual(payload['model'], 'test-model')
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            data = 'data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: [DONE]\n\n'.encode()
            for byte in data:
                await response.write(bytes([byte]))
            return response
        fake = web.Application()
        fake.router.add_post('/chat/completions', stream)
        server = TestServer(fake)
        await server.start_server()
        try:
            provider = CompatibleProvider(Settings(api_base=str(server.make_url('')).rstrip('/'), model='test-model'))
            result = [text async for text in provider.stream([{'role': 'user', 'content': '你好', 'status': 'done'}], 'cool', [])]
            self.assertEqual(''.join(result), '你好')
        finally:
            await server.close()


if __name__ == '__main__':
    unittest.main()
