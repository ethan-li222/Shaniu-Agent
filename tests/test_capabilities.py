import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from shaniu.capabilities import Capabilities
from shaniu.config import Settings
from shaniu.mcp.common import ToolError
from shaniu.providers import CompatibleProvider, context_messages
from shaniu.server import SERVICE, create_app
from shaniu.storage import Store


class CapabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.caps = Capabilities(Store(self.root / 'db.sqlite3'))

    def tearDown(self):
        self.temp.cleanup()

    def test_owner_snapshot_and_secret_redaction(self):
        binding = self.caps.save('alice', dict(kind='mcp', name='Echo', command=sys.executable, args=[], env={'TOKEN': 'do-not-return'}))
        self.assertNotIn('do-not-return', json.dumps(self.caps.catalog('alice')))
        self.assertEqual(self.caps.catalog('alice')[0]['env_keys'], ['TOKEN'])
        self.assertEqual(self.caps.catalog('bob'), [])
        self.assertFalse(self.caps.delete('bob', binding))
        with self.assertRaises(ToolError):
            self.caps.snapshot('bob', dict(enabled_mcp_servers=[binding]))
        with self.assertRaises(ToolError):
            self.caps.snapshot('alice', dict(enabled_mcp_servers=[binding]))
        self.caps.save('alice', dict(id=binding, kind='mcp', name='Renamed', command=sys.executable, args=[]))
        self.assertEqual(self.caps.records('alice')[0]['env']['TOKEN'], 'do-not-return')
        self.caps.save('alice', dict(id=binding, kind='mcp', name='Renamed', command=sys.executable, args=[], env={}))
        self.assertEqual(self.caps.records('alice')[0]['env'], {})
        skill = self.caps.save('alice', dict(kind='skill', name='Evidence', instructions='Only report verified results.'))
        snapshot = self.caps.snapshot('alice', dict(enabled_skills=[skill]))
        self.caps.delete('alice', skill)
        self.assertIn('Only report verified results.', self.caps.instructions(snapshot))
        self.assertEqual(self.caps.functions(snapshot), ([], {}))
        messages = context_messages([], 'cool', [], skill_instructions=self.caps.instructions(snapshot))
        self.assertIn('Only report verified results.', messages[0]['content'])

    def test_invalid_configuration_and_selection(self):
        for data in [dict(kind='skill', name='Bad', instructions=''), dict(kind='mcp', name='Bad', command='python', args='script.py'), dict(kind='mcp', name='Bad', command='python', env={'BAD-NAME': 'x'})]:
            with self.assertRaises(ToolError):
                self.caps.save('alice', data)
        for data in [dict(enabled_skills='x'), dict(enabled_skills=[{}]), dict(enabled_skills=['x', 'x'])]:
            with self.assertRaises(ToolError):
                self.caps.snapshot('alice', data)

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK is not installed in this test interpreter')
    async def test_actual_stdio_discovery_execution_and_invalidation(self):
        fixture = self.root / 'echo_server.py'
        fixture.write_text('import asyncio\nfrom pathlib import Path\nfrom mcp.server.fastmcp import FastMCP\nmcp = FastMCP("fixture")\n@mcp.tool()\nasync def echo(text: str) -> str:\n    if text == "wait":\n        Path(__file__).with_suffix(".started").touch()\n        await asyncio.sleep(120)\n    return "verified:" + text\nif __name__ == "__main__":\n    mcp.run(transport="stdio")\n', encoding='utf-8')
        binding = self.caps.save('alice', dict(kind='mcp', name='Echo', command=sys.executable, args=[str(fixture)]))
        self.assertEqual((await self.caps.discover('alice', binding))['count'], 1)
        snapshot = self.caps.snapshot('alice', dict(enabled_mcp_servers=[binding]))
        definitions, routes = self.caps.functions(snapshot)
        server, tool = routes[definitions[0]['function']['name']]
        self.assertEqual(tool, 'echo')
        result = await self.caps.run(server, tool, dict(text='hello'))
        self.assertFalse(result['isError'])
        self.assertIn('verified:hello', json.dumps(result))
        pending = asyncio.create_task(self.caps.run(server, tool, dict(text='wait')))
        try:
            for _ in range(100):
                if fixture.with_suffix('.started').exists():
                    break
                await asyncio.sleep(.05)
            self.assertTrue(fixture.with_suffix('.started').exists())
        finally:
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 5)
        self.caps.save('alice', dict(id=binding, kind='mcp', name='Echo', command=sys.executable, args=[str(fixture), '--changed']))
        with self.assertRaises(ToolError):
            self.caps.snapshot('alice', dict(enabled_mcp_servers=[binding]))
        # The active turn keeps its original, authorized configuration.
        self.assertEqual(server['args'], [str(fixture)])


class RecordingProvider(CompatibleProvider):
    async def stream(self, history, emotion, memories, skill_instructions=''):
        self.received_skills = skill_instructions
        yield 'Skill applied.'

    async def stream_with_tools(self, history, emotion, memories, tools, execute, **kwargs):
        self.received_skills = kwargs.get('skill_instructions', '')
        self.received_tools = tools
        self.tool_result = await execute(tools[0]['function']['name'], dict(text='hello'))
        self.denied = await execute('mcp_not_selected_0', {})
        yield 'Tool applied.'


class BindingAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings(database=Path(self.temp.name) / 'db.sqlite3')
        self.provider = RecordingProvider(settings)
        self.client = TestClient(TestServer(create_app(settings, self.provider)), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        await self.client.get('/api/health')
        self.headers = {'X-Shaniu-Client': 'web'}

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_binding_api_real_turn_snapshot_and_whitelist(self):
        response = await self.client.post('/api/agent/bindings', json=dict(kind='skill', name='Research', instructions='Cite actual evidence.'), headers=self.headers)
        self.assertEqual(response.status, 201)
        skill = (await response.json())['id']
        binding_response = await self.client.post('/api/agent/bindings', json=dict(kind='mcp', name='Echo', command='python', env={'TOKEN': 'secret'}), headers=self.headers)
        binding = (await binding_response.json())['id']
        caps = self.client.app[SERVICE].capabilities
        async def fake_run(record, tool=None, arguments=None):
            if tool:
                return dict(content=[dict(type='text', text='verified:hello')])
            return [dict(name='echo', description='Echo', inputSchema=dict(type='object', properties=dict(text=dict(type='string')), required=['text']))]
        caps.run = fake_run
        response = await self.client.post(f'/api/agent/bindings/{binding}/test', json={}, headers=self.headers)
        self.assertEqual(response.status, 200)
        response = await self.client.get('/api/agent/bindings')
        self.assertNotIn('secret', await response.text())
        cid = (await (await self.client.post('/api/conversations', json={}, headers=self.headers)).json())['id']
        response = await self.client.post(f'/api/conversations/{cid}/turns', json=dict(text='Research', enabled_mcp_servers=[binding], enabled_skills=[skill]), headers=self.headers)
        self.assertEqual(response.status, 202)
        await self.client.app[SERVICE].tasks[cid]
        self.assertIn('Cite actual evidence.', self.provider.received_skills)
        self.assertEqual(len(self.provider.received_tools), 1)
        self.assertIn('verified:hello', json.dumps(self.provider.tool_result))
        self.assertIn('error', self.provider.denied)
        # No selections means no tool definitions or skill instructions on the next turn.
        response = await self.client.post(f'/api/conversations/{cid}/turns', json=dict(text='Plain'), headers=self.headers)
        self.assertEqual(response.status, 202)
        await self.client.app[SERVICE].tasks[cid]
        self.assertEqual(self.provider.received_skills, '')
        response = await self.client.post(f'/api/conversations/{cid}/turns', json=dict(text='Bad', enabled_skills=['foreign']), headers=self.headers)
        self.assertEqual(response.status, 400)
        # Browser mutation header / origin boundary also covers binding commands.
        response = await self.client.post(f'/api/agent/bindings/{binding}/test', json={})
        self.assertEqual(response.status, 403)
