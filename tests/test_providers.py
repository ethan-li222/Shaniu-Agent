import asyncio
from dataclasses import replace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from types import SimpleNamespace

import aiohttp

from aiohttp import web
from aiohttp.test_utils import TestServer

from shaniu.config import Settings
from shaniu.providers import CompatibleProvider, ProviderError


class ProviderTests(IsolatedAsyncioTestCase):
    async def invoke(self, handler, *, stream=True, key='test-secret'):
        app = web.Application()
        app.router.add_post('/chat/completions', handler)
        server = TestServer(app)
        await server.start_server()
        settings = replace(Settings(), api_base=str(server.make_url('')).rstrip('/'), api_key=key, stream=stream, max_tokens=512)
        try:
            return [chunk async for chunk in CompatibleProvider(settings).stream(
                [{'role': 'user', 'content': '你好', 'status': 'done'}], 'happy', [{'content': '称呼小千'}])]
        finally:
            await server.close()

    async def test_nonstream_body_auth_and_personality(self):
        async def handler(request):
            data = await request.json()
            self.assertFalse(data['stream'])
            self.assertEqual(data['max_tokens'], 512)
            self.assertEqual(request.headers['Authorization'], 'Bearer test-secret')
            self.assertIn('温暖积极', data['messages'][0]['content'])
            self.assertIn('称呼小千', data['messages'][0]['content'])
            return web.json_response({'choices': [{'message': {'content': '小千，你好！'}, 'finish_reason': 'stop'}]})
        self.assertEqual(await self.invoke(handler, stream=False), ['小千，你好！'])

    async def test_stream_request_can_receive_json_response(self):
        async def handler(request):
            return web.json_response({'choices': [{'message': {'content': '你好'}, 'finish_reason': 'stop'}]})
        self.assertEqual(await self.invoke(handler), ['你好'])

    async def test_sse_multiline_reasoning_and_usage(self):
        async def handler(request):
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            await response.write(b': heartbeat\n\ndata: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}\n\n')
            await response.write('data: {"choices":\r\ndata: [{"delta":{"content":"你好"}}]}\r\n\r\n'.encode())
            await response.write(b'data: {"choices":[],"usage":{"total_tokens":42}}\n\ndata: [DONE]\n\n')
            return response
        self.assertEqual(await self.invoke(handler), ['你好'])

    async def test_http_error_does_not_leak_provider_body(self):
        async def handler(request):
            return web.json_response({'error': {'message': 'leaked-secret test-secret'}}, status=401)
        with self.assertRaises(ProviderError) as caught:
            await self.invoke(handler)
        self.assertIn('401', str(caught.exception))
        self.assertNotIn('test-secret', str(caught.exception))

    async def test_length_limit_is_reported_as_incomplete(self):
        async def handler(request):
            return web.Response(text='data: {"choices":[{"delta":{"content":"部分"},"finish_reason":"length"}]}\n\n', content_type='text/event-stream')
        with self.assertRaises(ProviderError) as caught:
            await self.invoke(handler)
        self.assertIn('上限', str(caught.exception))

    async def test_json_without_answer_is_not_silent_success(self):
        async def handler(request):
            return web.json_response({'choices': [{'message': {'reasoning_content': 'hidden'}}]})
        with self.assertRaises(ProviderError):
            await self.invoke(handler, stream=False)

    async def test_settings_repr_hides_api_key(self):
        self.assertNotIn('super-secret', repr(Settings(api_key='super-secret')))

    async def test_network_permission_error_is_safe_and_specific_in_both_paths(self):
        connection = SimpleNamespace(host='private-host', port=443, is_ssl=True)
        error = aiohttp.ClientConnectorError(connection, PermissionError(13, 'private-body test-secret'))
        provider = CompatibleProvider(replace(Settings(), api_key='test-secret'))
        for path in ['text', 'tools']:
            with self.subTest(path=path), patch('shaniu.providers.aiohttp.ClientSession._request', side_effect=error):
                stream = provider.stream([dict(role='user', content='hi', status='done')], 'cool', []) if path == 'text' else provider.events(dict(stream=True))
                with self.assertRaises(ProviderError) as caught:
                    _ = [chunk async for chunk in stream]
                self.assertIn('网络权限', str(caught.exception))
                self.assertNotIn('test-secret', str(caught.exception))
                self.assertNotIn('private-body', str(caught.exception))

    async def test_model_timeout_is_distinguished_from_realtime_connection(self):
        provider = CompatibleProvider(Settings())
        with patch('shaniu.providers.aiohttp.ClientSession._request', side_effect=asyncio.TimeoutError):
            with self.assertRaises(ProviderError) as caught:
                _ = [chunk async for chunk in provider.events(dict(stream=True))]
        self.assertIn('模型响应超时', str(caught.exception))
        self.assertIn('无需重新连接实时数字人', str(caught.exception))


class AgentProviderTests(IsolatedAsyncioTestCase):
    async def test_truncated_planning_is_retried_before_any_tool_executes(self):
        import json
        requests, executed = [], []
        tid = '00000000-0000-4000-8000-000000000001'
        async def handler(request):
            payload = await request.json()
            requests.append(payload)
            if len(requests) == 1:
                # Even a complete-looking call must not execute when the model
                # declares the entire output truncated.
                return web.json_response(dict(choices=[dict(message=dict(content=None, tool_calls=[dict(
                    id='partial', type='function', function=dict(name='moneyprinter_create', arguments='{"kind":"video","subject":"wrong"}'))]), finish_reason='length')]))
            self.assertEqual(executed, [])
            self.assertEqual(payload['max_tokens'], 8192)
            self.assertIn('未执行任何工具', payload['messages'][-1]['content'])
            return web.json_response(dict(choices=[dict(message=dict(tool_calls=[dict(id='real', type='function', function=dict(
                name='moneyprinter_create', arguments=json.dumps(dict(kind='video', subject='雪豹', aspect='16:9', subtitles=True))))]), finish_reason='tool_calls')]))
        async def execute(name, args):
            executed.append((name, args))
            return dict(task_id=tid, status='submitted', subtitles=True)
        answer = await self.run_agent(handler, execute, stream=False)
        self.assertEqual(requests[0]['max_tokens'], 4096)
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][1]['subject'], '雪豹')
        self.assertIn(tid, answer)
        self.assertIn('16:9 横屏，带字幕', answer)
        self.assertEqual(len(requests), 2)  # Receipt does not call the model again.

    async def test_reasoning_only_length_failure_is_bounded_without_submission(self):
        requests = 0
        async def handler(request):
            nonlocal requests
            requests += 1
            return web.json_response(dict(choices=[dict(message=dict(reasoning_content='hidden'), finish_reason='length')]))
        async def execute(name, args):
            self.fail('Truncated planning must not submit')
        with self.assertRaises(ProviderError):
            await self.run_agent(handler, execute, stream=False)
        self.assertEqual(requests, 2)

    async def test_streaming_length_failure_never_executes_partial_arguments(self):
        requests = 0
        async def handler(request):
            nonlocal requests
            requests += 1
            return web.Response(text='data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"partial","function":{"name":"moneyprinter_create","arguments":"{\\\"kind\\\":"}}]},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n', content_type='text/event-stream')
        async def execute(name, args):
            self.fail('Partial tool arguments must never execute')
        with self.assertRaises(ProviderError):
            await self.run_agent(handler, execute)
        self.assertEqual(requests, 2)

    async def test_fabricated_submission_is_hidden_and_repaired_with_real_call(self):
        requests, executed = 0, []
        real_id = '00000000-0000-4000-8000-000000000001'
        fake_id = '2d18c5f9-3e11-47cf-a7a4-5832ce8a59e0'
        async def handler(request):
            nonlocal requests
            requests += 1
            if requests == 1:
                message = dict(content='已提交，任务 ID：'+fake_id)
            elif requests == 2:
                message = dict(content=None, tool_calls=[dict(id='real-call', type='function', function=dict(name='moneyprinter_create', arguments='{}'))])
            else:
                message = dict(content='任务已提交：'+real_id)
            return web.json_response(dict(choices=[dict(message=message, finish_reason='stop')]))
        async def execute(name, args):
            executed.append(name)
            return dict(task_id=real_id, status='submitted')
        answer = await self.run_agent(handler, execute, stream=False)
        self.assertNotIn(fake_id, answer)
        self.assertIn(real_id, answer)
        self.assertEqual(executed, ['moneyprinter_create'])

    async def test_failed_submission_is_not_automatically_repeated(self):
        requests, executions = 0, []
        async def handler(request):
            nonlocal requests
            requests += 1
            if requests < 3:
                message = dict(content=None, tool_calls=[dict(id='call-'+str(requests), type='function', function=dict(name='moneyprinter_create', arguments='{}'))])
            else:
                message = dict(content='创建失败，未再次提交。')
            return web.json_response(dict(choices=[dict(message=message, finish_reason='stop')]))
        async def execute(name, args):
            executions.append(name)
            return dict(error='提交结果未知，请查询任务记录。')
        self.assertEqual(await self.run_agent(handler, execute, stream=False), '创建失败，未再次提交。')
        self.assertEqual(len(executions), 1)

    async def test_model_cannot_execute_tools_outside_provided_definitions(self):
        requests = 0
        async def handler(request):
            nonlocal requests
            requests += 1
            payload = await request.json()
            if requests == 1:
                message = dict(content=None, tool_calls=[dict(id='forged-1', type='function', function=dict(name='workspace_write', arguments='{}'))])
            else:
                self.assertIn('未启用', payload['messages'][-1]['content'])
                message = dict(content='此工具未启用。')
            return web.json_response(dict(choices=[dict(message=message, finish_reason='stop')]))
        async def execute(name, args):
            self.fail('Unselected tools must not execute')
        self.assertEqual(await self.run_agent(handler, execute, stream=False), '此工具未启用。')

    async def run_agent(self, handler, execute, stream=True):
        app = web.Application()
        app.router.add_post('/chat/completions', handler)
        async with TestServer(app) as server:
            settings = replace(Settings(), api_base=str(server.make_url('')).rstrip('/'), stream=stream)
            provider = CompatibleProvider(settings)
            return ''.join([part async for part in provider.stream_with_tools(
                [dict(role='user', content='检查视频服务', status='done')], 'cool', [],
                [dict(type='function', function=dict(name=name, parameters=dict(type='object', properties={}))) for name in ('moneyprinter_health', 'moneyprinter_create')], execute)])

    async def test_fragmented_sse_tool_arguments_and_returned_result(self):
        received = []
        async def handler(request):
            payload = await request.json()
            received.append(payload)
            if len(received) == 1:
                events = [dict(choices=[dict(delta=dict(tool_calls=[dict(index=0, id='call-1', function=dict(name='moneyprinter_health', arguments='{'))]))]),
                          dict(choices=[dict(delta=dict(tool_calls=[dict(index=0, function=dict(arguments='}'))]), finish_reason='tool_calls')])]
            else:
                self.assertEqual(payload['messages'][-1]['role'], 'tool')
                self.assertEqual(payload['messages'][-1]['tool_call_id'], 'call-1')
                self.assertIn('ready', payload['messages'][-1]['content'])
                events = [dict(choices=[dict(delta=dict(content='服务已连接。'), finish_reason='stop')])]
            import json
            return web.Response(text=''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n', content_type='text/event-stream')
        calls = []
        async def execute(name, args):
            calls.append((name, args))
            return dict(ready=True)
        self.assertEqual(await self.run_agent(handler, execute), '服务已连接。')
        self.assertEqual(calls, [('moneyprinter_health', {})])

    async def test_json_tool_loop_does_not_repeat_submission(self):
        requests = 0
        async def handler(request):
            nonlocal requests
            requests += 1
            if requests < 3:
                message = dict(content=None, tool_calls=[dict(id=f'call-{requests}', type='function', function=dict(name='moneyprinter_create', arguments='{"kind":"video","subject":"test"}'))])
            else:
                payload = await request.json()
                self.assertIn('勿重复', payload['messages'][-1]['content'])
                message = dict(content='任务已经提交。')
            return web.json_response(dict(choices=[dict(message=message, finish_reason='stop')]))
        calls = []
        async def execute(name, args):
            calls.append(name)
            return dict(task_id='test-id', status='submitted')
        answer = await self.run_agent(handler, execute, stream=False)
        self.assertIn('任务已提交', answer)
        self.assertIn('test-id', answer)
        self.assertEqual(requests, 1)
        self.assertEqual(calls, ['moneyprinter_create'])

    async def test_unsupported_function_calling_is_explicit_error(self):
        async def handler(request):
            return web.json_response(dict(error='secret upstream body'), status=400)
        async def execute(name, args):
            self.fail('Unsupported model must not execute tools')
        with self.assertRaises(ProviderError) as caught:
            await self.run_agent(handler, execute)
        self.assertIn('function calling', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))
