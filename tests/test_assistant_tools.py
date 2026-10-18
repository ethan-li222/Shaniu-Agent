import asyncio
import base64
import json
import socket
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

from shaniu.assistant_tools import AssistantTools, PageText, PublicResolver, public_url, selected_groups
from shaniu.config import Settings
from shaniu.moneyprinter import ToolError
from shaniu.server import SERVICE, create_app
from shaniu.mcp.registry import ToolRegistry


class AssistantToolTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        (self.docs / 'realtime.md').write_text('WebRTC 实时数字人使用说明，音频与字幕可选。', encoding='utf-8')
        self.tools = AssistantTools(self.root / 'workspace', self.docs)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_file_lifecycle_isolation_and_search_sources(self):
        self.assertEqual((await self.tools.call('alice', 'workspace_list', {}))['entries'], [])
        result = await self.tools.call('alice', 'workspace_write', dict(path='notes/学习.md', content='学习 WebRTC，每天复习实时音视频。'))
        self.assertIn('/api/workspace/file?', result['artifacts'][0]['url'])
        with self.assertRaises(ToolError):
            await self.tools.call('alice', 'workspace_write', dict(path='notes/学习.md', content='覆盖'))
        with self.assertRaises(ToolError):
            await self.tools.call('bob', 'workspace_read', dict(path='notes/学习.md'))
        sources = [m['source'] for m in (await self.tools.call('alice', 'knowledge_search', dict(query='WebRTC')))['matches']]
        self.assertIn('docs/realtime.md', sources)
        self.assertIn('workspace/notes/学习.md', sources)
        bob = await self.tools.call('bob', 'workspace_search', dict(query='WebRTC'))
        self.assertEqual(bob['matches'], [])
        self.assertIn('学习', (await self.tools.call('alice', 'workspace_read', dict(path='notes/学习.md')))['content'])

    async def test_invalid_paths_parameters_and_selection(self):
        for path in ['../secret.txt', 'C:/secret.txt', '/secret.txt', '.env', 'note.exe', 'NUL.txt', 'notes./a.txt', 'a:stream.txt', 'a/../x.txt']:
            with self.subTest(path=path), self.assertRaises(ToolError):
                await self.tools.call('alice', 'workspace_write', dict(path=path, content='x'))
        for args in [dict(query=[]), dict(query=''), dict(query='x', command='bad')]:
            with self.assertRaises(ToolError):
                await self.tools.call('alice', 'knowledge_search', args)
        for data in [dict(enabled_tool_groups='files'), dict(enabled_tool_groups=['terminal']), dict(moneyprinter_enabled='true')]:
            with self.assertRaises(ToolError):
                selected_groups(data)

    async def test_shared_registry_denies_unselected_tools_before_side_effects(self):
        registry = ToolRegistry(self.tools)
        self.assertFalse(registry.available('moneyprinter'))
        self.assertEqual(len(registry.definitions({'files'})), 4)
        with self.assertRaises(ToolError):
            await registry.call('alice', 'workspace_write', dict(path='blocked.md', content='x'), {'web'})
        with self.assertRaises(ToolError):
            registry.definitions({'terminal'})
        self.assertFalse(self.tools.user_root('alice').exists())
        saved = await registry.call('alice', 'workspace_write', dict(path='allowed.md', content='x'), {'files'})
        self.assertEqual(saved['path'], 'allowed.md')
        self.assertTrue(all('title' in tool for tool in registry.catalog()['tools']))

    async def test_github_readme_decodes_limits_and_rejects_invalid_payloads(self):
        content = '# 项目介绍\n' + 'WebRTC ' * 2000
        payload = dict(encoding='base64', content=base64.b64encode(content.encode()).decode(), html_url='https://github.com/example/repo/blob/main/README.md', path='README.md')
        with patch.object(self.tools.web, 'fetch', AsyncMock(return_value=dict(content=json.dumps(payload), truncated=False))) as fetch:
            result = await self.tools.call('alice', 'github_readme', dict(repository='example/repo'))
            self.assertIn('项目介绍', result['content'])
            self.assertEqual(len(result['content']), 12000)
            self.assertTrue(result['truncated'])
            self.assertEqual(fetch.call_args.args[0], 'https://api.github.com/repos/example/repo/readme')
        with patch.object(self.tools.web, 'fetch', AsyncMock(return_value=dict(content='{}', truncated=False))):
            with self.assertRaises(ToolError):
                await self.tools.call('alice', 'github_readme', dict(repository='example/repo'))

    async def test_public_reader_blocks_private_resolution_and_strips_scripts(self):
        for url in ['file:///etc/passwd', 'http://127.0.0.1', 'http://[::1]', 'http://169.254.169.254', 'http://localhost', 'https://x.local', 'https://user:pass@example.com', 'https://example.com:8011']:
            with self.assertRaises(ToolError):
                public_url(url)
        self.assertEqual(public_url('https://example.com/docs'), 'https://example.com/docs')
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'getaddrinfo', AsyncMock(return_value=[(socket.AF_INET, 1, 6, '', ('10.0.0.1', 80))])):
            with self.assertRaises(ToolError):
                await PublicResolver().resolve('public.example', 80)
        parser = PageText()
        parser.feed('<h1>标题</h1><script>evil()</script><style>hide</style><p>正文 &amp; 内容</p>')
        text = ''.join(parser.parts)
        self.assertIn('正文 & 内容', text)
        self.assertNotIn('evil', text)

    async def test_routes_require_selection_and_downloads_require_owner(self):
        app = create_app(replace(Settings(), provider='demo', database=self.root / 'app.db'))
        app[SERVICE].assistant_tools = self.tools
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
            await client.get('/api/health')
            headers = {'X-Shaniu-Client': 'web'}
            url = '/api/agent/tools/workspace_write'
            args = dict(path='note.md', content='你好')
            self.assertEqual((await client.post(url, json=args, headers=headers)).status, 403)
            response = await client.post(url, json=dict(args, enabled_tool_groups=['files']), headers=headers)
            self.assertEqual(response.status, 200)
            artifact = (await response.json())['artifacts'][0]['url']
            response = await client.get(artifact)
            self.assertEqual(await response.text(), '你好')
            self.assertEqual(response.headers['Content-Disposition'], 'attachment')
            client.session.cookie_jar.clear()
            self.assertEqual((await client.get(artifact)).status, 404)

    async def test_native_tool_loop_exposes_only_selected_groups(self):
        requests = []

        async def model(request):
            payload = await request.json()
            requests.append(payload)
            if 'tools' not in payload:
                message = dict(content='请先启用工具。')
            elif payload['messages'][-1]['role'] == 'tool':
                message = dict(content='文件已保存。')
            else:
                names = {t['function']['name'] for t in payload['tools']}
                self.assertEqual(names, {'workspace_list', 'workspace_read', 'workspace_write', 'workspace_search'})
                message = dict(content=None, tool_calls=[dict(id='write-1', type='function', function=dict(name='workspace_write', arguments='{"path":"plan.md","content":"Study WebRTC"}'))])
            return web.json_response(dict(choices=[dict(message=message, finish_reason='stop')]))

        upstream = web.Application()
        upstream.router.add_post('/chat/completions', model)
        async with TestServer(upstream) as model_server:
            settings = replace(Settings(), provider='compatible', api_key='test', api_base=str(model_server.make_url('')).rstrip('/'), database=self.root / 'model.db', stream=False)
            app = create_app(settings)
            app[SERVICE].assistant_tools = self.tools
            async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as client:
                await client.get('/api/health')
                headers = {'X-Shaniu-Client': 'web'}
                cid = (await (await client.post('/api/conversations', json={}, headers=headers)).json())['id']
                for groups in [[], ['files'], []]:
                    response = await client.post(f'/api/conversations/{cid}/turns', json=dict(text='保存学习计划', enabled_tool_groups=groups), headers=headers)
                    self.assertEqual(response.status, 202)
                    await asyncio.wait_for(app[SERVICE].tasks[cid], 3)
                    self.assertEqual(app[SERVICE].store.messages(cid)[-1]['status'], 'done')
                self.assertNotIn('tools', requests[0])
                self.assertIn('tools', requests[1])
                self.assertNotIn('tools', requests[-1])
                self.assertEqual(len(list((self.root / 'workspace').rglob('plan.md'))), 1)
