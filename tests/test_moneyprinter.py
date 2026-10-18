import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from shaniu.config import Settings
from shaniu.moneyprinter import MoneyPrinterTools, ToolError
from shaniu.server import create_app, SERVICE
from shaniu.storage import Store


class MoneyPrinterTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'tasks.db')
        self.requests = []
        self.tid = str(uuid4())
        self.state = 4
        self.delay = False
        self.received = asyncio.Event()

        async def submit(request):
            self.requests.append((request.path, await request.json()))
            self.received.set()
            if self.delay:
                await asyncio.sleep(.05)
            return web.json_response(dict(status=200, data=dict(task_id=self.tid)))

        async def status(request):
            self.requests.append((request.path, None))
            return web.json_response(dict(status=200, data=dict(state=self.state, progress=100 if self.state == 1 else 20,
                videos=[f'http://unsafe.example/tasks/{self.tid}/final-1.mp4', 'file:///etc/passwd'],
                audio_file=f'C:\\ITEMS\\MoneyPrinterTurbo\\storage\\tasks\\{self.tid}\\audio.mp3',
                subtitle_path=f'/storage/tasks/{self.tid}/subtitle.srt')))

        async def spec(request):
            return web.json_response(dict(paths={path: {} for path in ['/api/v1/videos', '/api/v1/audio', '/api/v1/subtitle', '/api/v1/tasks/{task_id}']}))

        app = web.Application()
        app.router.add_get('/openapi.json', spec)
        for kind in ['videos', 'audio', 'subtitle']:
            app.router.add_post('/api/v1/' + kind, submit)
        app.router.add_get('/api/v1/tasks/{tid}', status)
        self.upstream = TestServer(app)
        await self.upstream.start_server()
        self.settings = replace(Settings(), moneyprinter_enabled=True, moneyprinter_url=str(self.upstream.make_url('')).rstrip('/'))
        self.tools = MoneyPrinterTools(self.settings, self.store)

    async def asyncTearDown(self):
        await self.upstream.close()
        self.temp.cleanup()

    async def test_video_subtitle_toggle_and_completion_artifacts(self):
        self.assertTrue((await self.tools.call('alice', 'moneyprinter_health', {}))['ready'])
        result = await self.tools.call('alice', 'moneyprinter_create', dict(kind='video', subject='春天', script='春天来了。', terms='spring', subtitles=False, aspect='16:9'))
        self.assertEqual(result['status'], 'submitted')
        path, payload = self.requests[-1]
        self.assertEqual(path, '/api/v1/videos')
        self.assertIs(payload['subtitle_enabled'], False)
        self.assertEqual(payload['video_aspect'], '16:9')
        pending = await self.tools.call('alice', 'moneyprinter_status', dict(task_id=self.tid))
        self.assertEqual(pending['status'], 'processing')
        self.assertFalse(pending['artifacts'])
        self.state = 1
        done = await self.tools.call('alice', 'moneyprinter_status', dict(task_id=self.tid))
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(len(done['artifacts']), 3)
        self.assertTrue(all(item['url'].startswith(self.settings.moneyprinter_url + '/tasks/' + self.tid + '/') for item in done['artifacts']))
        self.state = -1
        self.assertEqual((await self.tools.call('alice', 'moneyprinter_status', dict(task_id=self.tid)))['status'], 'failed')

    async def test_validation_and_owner_isolation_before_network(self):
        for arguments in [dict(kind='audio', subject='x'), dict(kind='subtitle', subject='x', script='正文', subtitles=False), dict(kind='video', subject='x', subtitles='false'), dict(kind='video', subject='x', voice_rate=float('nan')), dict(kind='video', subject='x', command='rm')]:
            with self.assertRaises(ToolError):
                await self.tools.call('alice', 'moneyprinter_create', arguments)
        self.assertFalse(self.requests)
        await self.tools.call('alice', 'moneyprinter_create', dict(kind='audio', subject='x', script='正文'))
        with self.assertRaises(ToolError):
            await self.tools.call('bob', 'moneyprinter_status', dict(task_id=self.tid))
        self.assertEqual(len(self.requests), 1)
        self.assertFalse((await self.tools.call('bob', 'moneyprinter_tasks', {}))['tasks'])
        # Registry survives a restart.
        reopened = MoneyPrinterTools(self.settings, Store(self.store.path))
        self.assertEqual((await reopened.call('alice', 'moneyprinter_tasks', {}))['tasks'][0]['task_id'], self.tid)

    async def test_video_without_script_or_terms_uses_moneyprinter_llm(self):
        result = await self.tools.call('alice', 'moneyprinter_create', dict(kind='video', subject='雪豹'))
        self.assertEqual(result['task_id'], self.tid)
        payload = self.requests[-1][1]
        self.assertFalse(payload['video_script'])
        self.assertFalse(payload['video_terms'])

    async def test_subtitle_checkout_string_contract(self):
        await self.tools.call('alice', 'moneyprinter_create', dict(kind='subtitle', subject='朗读', script='你好。'))
        self.assertEqual(self.requests[-1][0], '/api/v1/subtitle')
        self.assertEqual(self.requests[-1][1]['subtitle_enabled'], 'true')

    async def test_markdown_script_is_cleaned_before_external_submission(self):
        await self.tools.call('alice', 'moneyprinter_create', dict(kind='audio', subject='测试', script='# 介绍\n\n我是 **傻妞**。请看 [文档](https://example.com)。'))
        self.assertEqual(self.requests[-1][1]['video_script'], '介绍\n我是 傻妞。请看 文档。')

    async def test_cancellation_preserves_accepted_task_id(self):
        self.delay = True
        task = asyncio.create_task(self.tools.call('alice', 'moneyprinter_create', dict(kind='video', subject='x', script='正文。', terms='snow leopard')))
        await self.received.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(self.store.external_task('alice', self.tid))
        self.assertEqual(len(self.requests), 1)

    async def test_disabled_and_loopback_only(self):
        tools = MoneyPrinterTools(replace(self.settings, moneyprinter_enabled=False), self.store)
        with self.assertRaises(ToolError):
            await tools.call('alice', 'moneyprinter_health', {})
        for url in ['http://example.com', 'http://localhost/api', 'http://user:pass@localhost', 'file:///tmp']:
            with self.assertRaises(ValueError):
                MoneyPrinterTools(replace(self.settings, moneyprinter_url=url), self.store)

    async def test_http_api_cookie_ownership_and_errors(self):
        settings = replace(self.settings, database=Path(self.temp.name) / 'app.db')
        async with TestClient(TestServer(create_app(settings)), cookie_jar=CookieJar(unsafe=True)) as client:
            await client.get('/api/health')
            self.assertTrue((await (await client.get('/api/agent/tools')).json())['enabled'])
            base = '/api/agent/tools/'
            self.assertEqual((await client.post(base + 'moneyprinter_create', json={})).status, 403)
            for allowed in [None, False, 'true']:
                blocked = await client.post(base + 'moneyprinter_create', json=dict(kind='video', subject='春天', moneyprinter_enabled=allowed), headers={'X-Shaniu-Client': 'web'})
                self.assertEqual(blocked.status, 403)
            self.assertFalse(self.requests)
            response = await client.post(base + 'moneyprinter_create', json=dict(kind='audio', subject='朗读', script='你好', moneyprinter_enabled=True), headers={'X-Shaniu-Client': 'web'})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['task_id'], self.tid)
            async with TestClient(TestServer(create_app(settings)), cookie_jar=CookieJar(unsafe=True)) as stranger:
                self.assertEqual((await stranger.post(base + 'moneyprinter_status', json=dict(task_id=self.tid), headers={'X-Shaniu-Client': 'web'})).status, 400)
            self.assertEqual((await client.post(base + 'unknown', json={}, headers={'X-Shaniu-Client': 'web'})).status, 400)

    async def test_chat_agent_submits_and_returns_tool_result(self):
        model_requests = []
        async def model(request):
            payload = await request.json()
            if 'tools' not in payload:
                return web.json_response(dict(choices=[dict(message=dict(content='请先勾选视频工具。'), finish_reason='stop')]))
            model_requests.append(payload)
            if len(model_requests) == 1:
                self.assertIn('false', payload['messages'][0]['content'])
                self.assertIn('由 MoneyPrinter 自己调用独立配置的模型', payload['messages'][0]['content'])
                message = dict(content=None, tool_calls=[dict(id='create-1', type='function', function=dict(name='moneyprinter_create', arguments='{"kind":"video","subject":"春天"}'))])
            else:
                self.assertIn(self.tid, payload['messages'][-1]['content'])
                self.assertIn('submitted', payload['messages'][-1]['content'])
                message = dict(content='视频任务已提交，请稍后查询进度。')
            return web.json_response(dict(choices=[dict(message=message, finish_reason='stop')]))
        model_app = web.Application()
        model_app.router.add_post('/chat/completions', model)
        async with TestServer(model_app) as upstream_model:
            settings = replace(self.settings, database=Path(self.temp.name) / 'agent.db', provider='compatible', api_key='test', api_base=str(upstream_model.make_url('')).rstrip('/'))
            app = create_app(settings)
            async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
                headers = {'X-Shaniu-Client': 'web'}
                await client.get('/api/health')
                cid = (await (await client.post('/api/conversations', json={}, headers=headers)).json())['id']
                for allowed in [None, False]:
                    data = dict(text='请帮我做一个春天的视频', subtitles=False)
                    if allowed is not None:
                        data['moneyprinter_enabled'] = allowed
                    response = await client.post(f'/api/conversations/{cid}/turns', json=data, headers=headers)
                    self.assertEqual(response.status, 202)
                    await asyncio.wait_for(app[SERVICE].tasks[cid], 3)
                    self.assertFalse(self.requests)
                    self.assertFalse(model_requests)
                invalid = await client.post(f'/api/conversations/{cid}/turns', json=dict(text='制作视频', moneyprinter_enabled='true'), headers=headers)
                self.assertEqual(invalid.status, 400)
                response = await client.post(f'/api/conversations/{cid}/turns', json=dict(text='请帮我做一个春天的视频', subtitles=False, moneyprinter_enabled=True), headers=headers)
                self.assertEqual(response.status, 202)
                await asyncio.wait_for(app[SERVICE].tasks[cid], 3)
                answer = app[SERVICE].store.messages(cid)[-1]
                self.assertEqual(answer['status'], 'done')
                self.assertIn('已提交', answer['content'])
                self.assertIs(self.requests[-1][1]['subtitle_enabled'], False)
                self.assertFalse(self.requests[-1][1]['video_script'])
                self.assertFalse(self.requests[-1][1]['video_terms'])
                tasks = await (await client.post('/api/agent/tools/moneyprinter_tasks', json={}, headers=headers)).json()
                self.assertEqual(tasks['tasks'][0]['task_id'], self.tid)
                before = len(self.requests)
                await client.post(f'/api/conversations/{cid}/turns', json=dict(text='再制作一个视频', moneyprinter_enabled=False), headers=headers)
                await asyncio.wait_for(app[SERVICE].tasks[cid], 3)
                self.assertEqual(len(self.requests), before)
