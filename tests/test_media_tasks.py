import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from shaniu.config import Settings
from shaniu.server import create_app, SERVICE
from shaniu.storage import Store
from shaniu.mcp.common import ToolError


class MediaTaskTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tid = str(uuid4())
        self.state, self.reads = 4, 0
        async def status(request):
            self.reads += 1
            return web.json_response(dict(status=200, data=dict(state=self.state, progress=100 if self.state == 1 else 40,
                shaniu_stage='render', videos=[f'/tasks/{self.tid}/final-1.mp4'])))
        async def file(request):
            self.assertEqual(request.headers.get('Range'), 'bytes=0-31')
            return web.Response(status=206, body=b'\x00\x00\x00 ftypisom'+b'0'*20,
                headers={'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-31/1000', 'Accept-Ranges': 'bytes'})
        upstream = web.Application()
        upstream.router.add_get('/api/v1/tasks/{taskid}', status)
        upstream.router.add_get('/tasks/{taskid}/final-1.mp4', file)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        app = create_app(Settings(database=Path(self.temp.name)/'chat.db', moneyprinter_enabled=True,
                                  moneyprinter_url=str(self.upstream.make_url('')).rstrip('/')))
        self.service = app[SERVICE]
        self.client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.get('/api/health')
        self.owner = next(cookie.value for cookie in self.client.session.cookie_jar if cookie.key == 'shaniu_owner')
        self.cid = self.service.store.create(self.owner, 'cool')['id']
        self.mid = self.service.store.add_message(self.cid, str(uuid4()), 'assistant', '任务已提交：'+self.tid)
        self.service.store.save_external_task(self.owner, self.tid, 'video', '雪豹科普')
        self.url = f'/api/conversations/{self.cid}/media-tasks'

    async def asyncTearDown(self):
        await self.client.close()
        await self.upstream.close()
        self.temp.cleanup()

    def expire(self):
        with self.service.store.connect() as db:
            db.execute('UPDATE external_task_results SET updated_at=0')

    async def test_legacy_card_auto_progress_completion_and_same_origin_range_download(self):
        result = await (await self.client.get(self.url)).json()
        task = result['tasks'][0]
        self.assertEqual(task['message_id'], self.mid)
        self.assertEqual(task['progress'], 40)
        self.assertEqual(task['status'], 'processing')
        await self.client.get(self.url)
        self.assertEqual(self.reads, 1)  # Three-second dedup, no model requests.
        self.state = 1
        self.expire()
        result = await (await self.client.get(self.url)).json()
        self.assertEqual(result['tasks'][0]['status'], 'completed')
        url = result['tasks'][0]['artifacts'][0]['url']
        self.assertTrue(url.startswith(self.url))
        response = await self.client.get(url+'?download=1', headers={'Range': 'bytes=0-31'})
        self.assertEqual(response.status, 206)
        self.assertEqual(response.headers['Content-Range'], 'bytes 0-31/1000')
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn(b'ftyp', await response.read())
        await self.client.get(self.url)
        self.assertEqual(self.reads, 2)  # Completed tasks no longer hit upstream.

    async def test_fake_ids_and_foreign_tasks_never_get_cards_or_files(self):
        fake = str(uuid4())
        self.service.store.add_message(self.cid, str(uuid4()), 'assistant', fake)
        self.service.store.save_external_task('someone-else', fake, 'video', 'foreign')
        result = await (await self.client.get(self.url)).json()
        self.assertEqual([t['task_id'] for t in result['tasks']], [self.tid])
        self.assertEqual((await self.client.get(self.url+f'/{fake}/files/final-1.mp4')).status, 404)
        async with TestClient(TestServer(create_app(Settings(database=Path(self.temp.name)/'chat.db'))),
                              cookie_jar=CookieJar(unsafe=True)) as stranger:
            self.assertEqual((await stranger.get(self.url)).status, 404)
            self.assertEqual((await stranger.get(self.url+f'/{self.tid}/files/final-1.mp4')).status, 404)

    async def test_offline_preserves_progress_and_recovers_without_resubmission(self):
        await self.client.get(self.url)
        self.expire()
        original = self.service.moneyprinter.call
        async def offline(*args, **kwargs):
            raise ToolError('offline')
        self.service.moneyprinter.call = offline
        task = (await (await self.client.get(self.url)).json())['tasks'][0]
        self.assertEqual(task['progress'], 40)
        self.assertEqual(task['status'], 'processing')
        self.assertIn('connection_error', task)
        self.service.moneyprinter.call = original
        self.expire()
        task = (await (await self.client.get(self.url)).json())['tasks'][0]
        self.assertNotIn('connection_error', task)
        self.assertEqual(len(self.service.store.external_tasks(self.owner)), 1)

    async def test_links_survive_restart_and_deleting_chat_removes_links(self):
        self.service.store.finish(self.mid, '提交结果没有包含 ID', 'error')
        self.service.store.link_external_task(self.owner, self.tid, self.mid)
        second = self.service.store.add_message(self.cid, str(uuid4()), 'assistant', '查询结果')
        self.service.store.link_external_task(self.owner, self.tid, second)
        reopened = Store(self.service.store.path)
        records = reopened.conversation_external_tasks(self.owner, self.cid)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['message_id'], self.mid)
        reopened.delete(self.cid)
        self.assertEqual((await self.client.get(self.url)).status, 404)
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM external_task_messages').fetchone()[0], 0)
