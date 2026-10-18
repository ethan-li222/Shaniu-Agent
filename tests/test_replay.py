import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from shaniu.config import Settings
from shaniu.server import create_app, SERVICE


class ReplayTests(IsolatedAsyncioTestCase):
    async def test_persistent_history_range_owner_and_delete(self):
        with tempfile.TemporaryDirectory() as temp, patch('shaniu.server.ROOT', Path(temp)):
            settings = Settings(database=Path(temp) / 'test.db')
            app = create_app(settings)
            headers = {'X-Shaniu-Client': 'web'}
            async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
                await client.get('/api/health')
                cid = (await (await client.post('/api/conversations', json={}, headers=headers)).json())['id']
                tid = str(uuid4())
                mid = app[SERVICE].store.add_message(cid, tid, 'assistant', '录制的回答', 'done')
                path = Path(temp) / 'data/replays' / (tid + '.mp4')
                path.parent.mkdir(parents=True)
                pending = path.with_suffix('.pending')
                pending.touch()
                history = await (await client.get(f'/api/conversations/{cid}/messages')).json()
                self.assertTrue(history['messages'][0]['replay_pending'])
                pending.unlink()
                path.write_bytes(b'0123456789')
                history = await (await client.get(f'/api/conversations/{cid}/messages')).json()
                url = history['messages'][0]['replay_url']
                partial = await client.get(url, headers={'Range': 'bytes=2-5'})
                self.assertEqual(partial.status, 206)
                self.assertEqual(await partial.read(), b'2345')
                # Reload websocket snapshot also carries persisted replay links.
                async with client.ws_connect(f'/api/conversations/{cid}/events') as ws:
                    self.assertEqual((await ws.receive_json())['payload']['messages'][0]['replay_url'], url)
                async with TestClient(TestServer(create_app(settings)), cookie_jar=CookieJar(unsafe=True)) as stranger:
                    self.assertEqual((await stranger.get(url)).status, 404)
                self.assertEqual((await client.get(f'/api/conversations/{cid}/messages/missing/replay')).status, 404)
                await client.delete(f'/api/conversations/{cid}', headers=headers)
                self.assertFalse(path.exists())
                self.assertEqual((await client.get(url)).status, 404)
