import asyncio
import json
import logging
import re
import secrets
import time
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiohttp import web, WSMsgType
import aiohttp

from .avatar import AvatarEngine, capabilities
from .avatar_catalog import AVATARS, avatar_ready, avatar_preview, SHANIU_AVATAR, SHANIU_OUTFITS
from .config import ROOT, Settings
from .emotions import BY_ID, EMOTIONS
from .providers import CompatibleProvider, DemoProvider
from .storage import Store
from .realtime import RealtimeBridge, SentenceBuffer
from .text import speech_text, MarkdownSpeechBuffer
from .moneyprinter import MoneyPrinterTools, ToolError
from .mcp.assistant import AssistantTools
from .mcp.catalog import TOOL_GROUP, selected_groups
from .mcp.registry import ToolRegistry
from .media_tasks import MediaTasks
from .capabilities import Capabilities

log = logging.getLogger(__name__)
SERVICE = web.AppKey('service', object)


class Dialogue:
    def __init__(self, settings, provider=None):
        self.settings = settings
        self.store = Store(settings.database)
        self.provider = provider or (DemoProvider() if settings.provider == 'demo' else CompatibleProvider(settings))
        self.tasks = {}
        self.locks = {}
        self.sockets = {}
        self.seq = {}
        self.turns = {}
        self.partial = {}
        self.avatar = AvatarEngine(settings)
        self.realtime = RealtimeBridge(settings)
        self.moneyprinter = MoneyPrinterTools(settings, self.store)
        self.moneyprinter.motion  # Initialize persistence and restart recovery once at startup.
        self.moneyprinter.post
        self.media_tasks = MediaTasks(self.store, self.moneyprinter)
        self.assistant_tools = AssistantTools()
        self.capabilities = Capabilities(self.store)

    @property
    def tools(self):
        return ToolRegistry(self.assistant_tools, self.moneyprinter)

    def lock(self, cid):
        return self.locks.setdefault(cid, asyncio.Lock())

    async def emit(self, cid, event, tid=None, **payload):
        self.seq[cid] = self.seq.get(cid, 0) + 1
        packet = dict(type=event, conversation_id=cid, turn_id=tid, seq=self.seq[cid], payload=payload)
        for socket in list(self.sockets.get(cid, set())):
            try:
                await asyncio.wait_for(socket.send_json(packet), timeout=2)
            except (ConnectionError, RuntimeError, asyncio.TimeoutError):
                self.sockets[cid].discard(socket)

    async def cancel(self, cid):
        self.turns.pop(cid, None)
        task = self.tasks.pop(cid, None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.avatar.cancel(cid)
        await self.realtime.interrupt(cid)

    async def generate(self, cid, owner, tid, emotion, realtime=False, subtitles=True, moneyprinter_allowed=False, enabled_groups=None, bindings=None):
        allowed_groups = set(enabled_groups or [])
        if moneyprinter_allowed:
            allowed_groups.add('moneyprinter')
        if not self.settings.moneyprinter_enabled:
            allowed_groups.discard('moneyprinter')
        bindings = bindings or dict(servers=[], skills=[])
        external_definitions, external_routes = self.capabilities.functions(bindings)
        skill_options = dict(skill_instructions=self.capabilities.instructions(bindings)) if bindings['skills'] else {}
        text = ''
        started = time.perf_counter()
        first_text = None
        mid = self.store.add_message(cid, tid, 'assistant', '', 'streaming')
        sentences = SentenceBuffer()
        markdown = MarkdownSpeechBuffer()
        async def speech(delta='', final=False):
            nonlocal realtime
            if not realtime:
                return
            try:
                plain = '\n'.join(markdown.feed(delta, final))
                for sentence in sentences.feed(plain, final):
                    if self.turns.get(cid) != tid:
                        raise asyncio.CancelledError()
                    await self.realtime.request('sentence', dict(turn_id=tid, text=sentence))
                if final:
                    await self.realtime.request('finish', dict(turn_id=tid))
            except RuntimeError as error:
                realtime = False
                await self.realtime.interrupt(cid)
                await self.emit(cid, 'media.error', tid, message=str(error))
        try:
            await self.emit(cid, 'turn.started', tid, emotion=emotion)
            history = self.store.messages(cid)
            question = next(m['content'] for m in reversed(history) if m['role'] == 'user')
            if re.fullmatch(r'(?:请问|告诉我)?\s*(?:现在|今天)?\s*(?:几点了?|什么时间|的?日期|星期几)[？?。！!\s]*', question):
                await self.emit(cid, 'tool.started', tid, name='current_time')
                value = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y年%m月%d日 %H:%M:%S')
                await self.emit(cid, 'tool.finished', tid, name='current_time', result=value, timezone='Asia/Shanghai')
                text = f'现在是北京时间 {value}。'
                await self.emit(cid, 'assistant.delta', tid, text=text)
                await speech(text)
            else:
                async def execute(name, arguments):
                    if self.turns.get(cid) != tid:
                        raise asyncio.CancelledError()
                    await self.emit(cid, 'tool.started', tid, name=name)
                    try:
                        if name not in external_routes and TOOL_GROUP.get(name) not in allowed_groups:
                            raise ToolError('请先勾选对应工具组，再发送请求。')
                        if name == 'moneyprinter_create' and isinstance(arguments, dict) and arguments.get('kind') == 'video':
                            arguments.setdefault('subtitles', subtitles)
                        if name in external_routes:
                            server, original_name = external_routes[name]
                            result = await self.capabilities.run(server, original_name, arguments)
                        else:
                            result = await self.tools.call(owner, name, arguments, allowed_groups)
                        if name in {'moneyprinter_create', 'moneyprinter_status', 'moneyprinter_enhance', 'media_postprocess', 'media_post_status'} and result.get('task_id'):
                            self.store.link_external_task(owner, result['task_id'], mid)
                            if name == 'moneyprinter_status':
                                self.store.cache_external_task(result['task_id'], result)
                    except ToolError as error:
                        result = dict(error=str(error), code=error.code)
                    await self.emit(cid, 'tool.finished', tid, name=name, result=result)
                    return result
                arguments = ([m for m in history if m['id'] != mid], emotion, self.store.memories(owner))
                if (allowed_groups or external_definitions) and isinstance(self.provider, CompatibleProvider):
                    definitions = self.tools.functions(allowed_groups) + external_definitions
                    stream = self.provider.stream_with_tools(*arguments, definitions, execute, subtitle_default=subtitles, verified_tasks=self.store.external_tasks(owner) if 'moneyprinter' in allowed_groups else [], **skill_options)
                else:
                    stream = self.provider.stream(*arguments, **skill_options)
                async for delta in stream:
                    if self.turns.get(cid) != tid:
                        raise asyncio.CancelledError()
                    text += delta
                    if first_text is None:
                        first_text = time.perf_counter() - started
                    self.partial[tid] = text
                    if len(text) > 24000:
                        raise RuntimeError('回答超过长度限制，请缩小问题范围。')
                    await self.emit(cid, 'assistant.delta', tid, text=delta)
                    await speech(delta)
            if not text.strip():
                raise RuntimeError('模型没有返回可显示的文本，请重试或检查模型配置。')
            self.store.finish(mid, text, 'done')
            await speech(final=True)
            await self.emit(cid, 'assistant.done', tid, text=text, speech_text=speech_text(text), message_id=mid, metrics={
                'first_text_seconds': round(first_text, 2) if first_text is not None else None,
                'total_seconds': round(time.perf_counter() - started, 2),
                'model': self.settings.model if self.settings.provider != 'demo' else 'offline-demo',
            })
        except asyncio.CancelledError:
            self.store.finish(mid, text, 'cancelled')
            await self.emit(cid, 'turn.cancelled', tid)
            raise
        except Exception as error:
            if realtime:
                await self.realtime.interrupt(cid)
            self.store.finish(mid, text, 'error')
            log.warning('Generation failed (%s)', type(error).__name__)
            message = str(error) if isinstance(error, RuntimeError) else '模型连接失败或超时，请检查配置后重试。'
            await self.emit(cid, 'error', tid, message=message)
        finally:
            if self.turns.get(cid) == tid:
                self.turns.pop(cid, None)
            self.partial.pop(tid, None)


@web.middleware
async def boundary(request, handler):
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        return web.json_response({'error': '不允许跨站请求'}, status=403)
    if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.headers.get('X-Shaniu-Client') != 'web':
        return web.json_response({'error': '缺少客户端请求标识'}, status=403)
    owner = request.cookies.get('shaniu_owner')
    fresh = not owner or not re.fullmatch(r'[A-Za-z0-9_-]{43}', owner)
    request['owner'] = secrets.token_urlsafe(32) if fresh else owner
    try:
        response = await handler(request)
    except web.HTTPException as error:
        response = web.json_response({'error': error.reason}, status=error.status)
    except (ValueError, json.JSONDecodeError, TypeError):
        response = web.json_response({'error': '请求格式不正确'}, status=400)
    except Exception:
        log.exception('Request failed')
        response = web.json_response({'error': '服务暂时不可用'}, status=500)
    if fresh and not response.prepared:
        response.set_cookie('shaniu_owner', request['owner'], httponly=True, samesite='Strict', secure=request.secure, max_age=365 * 86400)
    return response


def get_conversation(request):
    service = request.app[SERVICE]
    cid = request.match_info['cid']
    record = service.store.conversation(cid, request['owner'])
    if not record:
        raise web.HTTPNotFound(reason='会话不存在')
    return service, cid, record


async def body(request):
    value = await request.json()
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(reason='需要 JSON 对象')
    return value


def required_text(data, field, limit):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise web.HTTPBadRequest(reason=f'{field} 必须为 1–{limit} 字符')
    return value.strip()


async def health(request):
    service = request.app[SERVICE]
    return web.json_response(dict(status='ok', service='shaniu', provider=service.settings.provider, model=service.settings.model if service.settings.provider != 'demo' else 'offline-demo', avatar=capabilities(service.settings), speech='browser_optional', tts=service.settings.speech_config))


async def emotions(request):
    return web.json_response(EMOTIONS)


async def avatars(request):
    return web.json_response([dict(**item, ready=avatar_ready(item['id']), preview_url=f'/api/avatars/{item["id"]}/preview', outfits=SHANIU_OUTFITS if item['id'] == SHANIU_AVATAR else {}) for item in AVATARS])


async def preview_avatar(request):
    avatar_id = request.match_info['avatar_id']
    try:
        if not avatar_ready(avatar_id):
            raise web.HTTPNotFound(reason='人物素材尚未准备好')
        path = avatar_preview(avatar_id, request.query.get('emotion', 'cool'))
    except ValueError:
        raise web.HTTPNotFound(reason='未知人物形象') from None
    if not path:
        raise web.HTTPNotFound(reason='没有人物预览')
    return web.FileResponse(path)


async def select_avatar(request):
    service, cid, _ = get_conversation(request)
    avatar_id = required_text(await body(request), 'avatar_id', 100)
    if not avatar_ready(avatar_id):
        raise web.HTTPBadRequest(reason='人物素材尚未准备好')
    async with service.lock(cid):
        get_conversation(request)
        await service.cancel(cid)
        await service.realtime.close(cid)
        service.store.avatar(cid, avatar_id)
        await service.emit(cid, 'avatar.changed', avatar_id=avatar_id)
    return web.json_response(dict(avatar_id=avatar_id))


async def agent_tools(request):
    service = request.app[SERVICE]
    return web.json_response(dict(enabled=service.settings.moneyprinter_enabled, **service.tools.catalog()))


async def agent_bindings(request):
    capabilities = request.app[SERVICE].capabilities
    try:
        if request.method == 'GET':
            return web.json_response(capabilities.catalog(request['owner']), headers={'Cache-Control': 'no-store'})
        binding_id = capabilities.save(request['owner'], await body(request))
        return web.json_response(dict(id=binding_id), status=201)
    except ToolError as error:
        raise web.HTTPBadRequest(reason=str(error)) from None


async def agent_binding_action(request):
    capabilities = request.app[SERVICE].capabilities
    try:
        if request.method == 'DELETE':
            if not capabilities.delete(request['owner'], request.match_info['binding_id']):
                raise web.HTTPNotFound(reason='绑定不存在')
            return web.json_response(dict(deleted=True))
        return web.json_response(await capabilities.discover(request['owner'], request.match_info['binding_id']))
    except ToolError as error:
        raise web.HTTPBadRequest(reason=str(error)) from None


async def call_agent_tool(request):
    service = request.app[SERVICE]
    data = await body(request)
    if request.match_info['name'] == 'moneyprinter_create' and data.get('moneyprinter_enabled') is not True and not (isinstance(data.get('enabled_tool_groups'), list) and 'moneyprinter' in data['enabled_tool_groups']):
        raise web.HTTPForbidden(reason='请先勾选 MoneyPrinter 视频工具，再提交生成请求。')
    try:
        groups = selected_groups(data)
    except ToolError as error:
        raise web.HTTPBadRequest(reason=str(error)) from None
    data.pop('enabled_tool_groups', None)
    allowed = data.pop('moneyprinter_enabled', False)
    name = request.match_info['name']
    if name == 'moneyprinter_enhance' and 'moneyprinter' not in groups:
        raise web.HTTPForbidden(reason='请先勾选 MoneyPrinter 工具。')
    if name in TOOL_GROUP and TOOL_GROUP[name] != 'moneyprinter' and TOOL_GROUP[name] not in groups:
        raise web.HTTPForbidden(reason='请先勾选对应工具组，再发送请求。')
    if 'moneyprinter' in groups:
        allowed = True
    if request.match_info['name'] == 'moneyprinter_create' and allowed is not True:
        raise web.HTTPForbidden(reason='请先勾选 MoneyPrinter 视频工具，再提交生成请求。')
    try:
        # Preserve the existing read-only MoneyPrinter HTTP API contract.
        if name in {'moneyprinter_health', 'moneyprinter_tasks', 'moneyprinter_status'}:
            result = await service.moneyprinter.call(request['owner'], name, data)
        else:
            result = await service.tools.call(request['owner'], name, data, groups)
    except ToolError as error:
        raise web.HTTPBadRequest(reason=str(error)) from None
    return web.json_response(result)


async def workspace_file(request):
    service = request.app[SERVICE]
    try:
        path = service.assistant_tools.path(request['owner'], request.query.get('path', ''))
        service.assistant_tools.read(path)
    except ToolError as error:
        raise web.HTTPNotFound(reason=str(error)) from None
    return web.FileResponse(path, headers={'Content-Type': 'text/plain; charset=utf-8', 'Content-Disposition': 'attachment', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


async def conversations(request):
    service = request.app[SERVICE]
    if request.method == 'GET':
        return web.json_response(service.store.conversations(request['owner']))
    data = await body(request)
    emotion = data.get('emotion', 'cool')
    if emotion not in BY_ID:
        raise web.HTTPBadRequest(reason='未知情绪')
    return web.json_response(service.store.create(request['owner'], emotion), status=201)


async def messages(request):
    service, cid, record = get_conversation(request)
    return web.json_response(dict(conversation=record, messages=replay_messages(service, cid), active_turn=service.turns.get(cid)))


async def conversation_media_tasks(request):
    service, cid, _ = get_conversation(request)
    records = await service.media_tasks.list(request['owner'], cid)
    return web.json_response(dict(tasks=service.media_tasks.public(cid, records)), headers={'Cache-Control': 'private, no-store'})


async def media_task_file(request):
    service, cid, _ = get_conversation(request)
    tid, filename = request.match_info['taskid'], request.match_info['filename']
    record = next((r for r in service.store.conversation_external_tasks(request['owner'], cid) if r['task_id'] == tid), None)
    if not record:
        raise web.HTTPNotFound(reason='任务不存在或不属于当前会话')
    result = await service.media_tasks.status(request['owner'], record)
    file = next((f for f in result.get('artifacts', []) if f['filename'] == filename), None)
    if result.get('status') != 'completed' or not file:
        raise web.HTTPNotFound(reason='文件尚未生成或不存在')
    # Reconstruct the allowed local URL; never follow an upstream-provided host.
    allowed = service.moneyprinter.artifact(tid, file['url'])
    if not allowed:
        raise web.HTTPNotFound(reason='文件地址无效')
    headers = {'Range': request.headers['Range']} if 'Range' in request.headers else {}
    response = None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120, connect=3)) as client:
            async with client.get(allowed['url'], headers=headers, allow_redirects=False) as upstream:
                if upstream.status not in {200, 206, 416}:
                    raise web.HTTPBadGateway(reason='视频文件暂时无法读取，请稍后重试')
                response_headers = {k: upstream.headers[k] for k in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'] if k in upstream.headers}
                response_headers.update({'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
                if request.query.get('download') == '1':
                    response_headers['Content-Disposition'] = f'attachment; filename="{filename}"'
                response = web.StreamResponse(status=upstream.status, headers=response_headers)
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError):
        if response is not None and response.prepared:
            response.force_close()
            return response
        raise web.HTTPBadGateway(reason='视频服务暂时无法连接，请稍后重试') from None


async def motion_file(request):
    service, cid, _ = get_conversation(request)
    tid = request.match_info['taskid']
    if not any(r['task_id'] == tid for r in service.store.conversation_external_tasks(request['owner'], cid)):
        raise web.HTTPNotFound(reason='任务不存在或不属于当前会话')
    try:
        path = service.moneyprinter.motion.file(request['owner'], tid, request.match_info['jobid'], request.match_info['filename'])
    except ToolError as error:
        raise web.HTTPNotFound(reason=str(error)) from None
    headers = {'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'}
    if request.query.get('download') == '1':
        headers['Content-Disposition'] = f'attachment; filename="{path.name}"'
    return web.FileResponse(path, headers=headers)


async def post_file(request):
    service, cid, _ = get_conversation(request)
    tid = request.match_info['taskid']
    if not any(r['task_id'] == tid for r in service.store.conversation_external_tasks(request['owner'], cid)):
        raise web.HTTPNotFound(reason='任务不存在或不属于当前会话')
    try:
        path = service.moneyprinter.post.file(request['owner'], tid, request.match_info['jobid'], request.match_info['filename'])
    except ToolError as error:
        raise web.HTTPNotFound(reason=str(error)) from None
    headers = {'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'}
    if request.query.get('download') == '1':
        headers['Content-Disposition'] = f'attachment; filename="{path.name}"'
    return web.FileResponse(path, headers=headers)


async def upload_motion_music(request):
    motion = request.app[SERVICE].moneyprinter.motion
    directory = motion.music_directory(request['owner'])
    if len(list(directory.iterdir())) >= 20:
        raise web.HTTPBadRequest(reason='本用户配乐文件最多保存 20 个。')
    reader = await request.multipart()
    field = await reader.next()
    if not field or not field.filename or not field.filename.lower().endswith(('.mp3','.wav','.m4a')):
        raise web.HTTPBadRequest(reason='请选择 MP3、WAV 或 M4A 配乐。')
    music_id = uuid4().hex
    path = directory/(music_id+'.audio')
    try:
        size = 0
        with path.open('xb') as output:
            while chunk := await field.read_chunk():
                size += len(chunk)
                if size > 20*1024*1024:
                    raise web.HTTPBadRequest(reason='配乐最多 20 MB。')
                output.write(chunk)
        if not size:
            raise web.HTTPBadRequest(reason='配乐文件为空。')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return web.json_response(dict(music_id=music_id))


def replay_messages(service, cid):
    records = service.store.messages(cid)
    for message in records:
        if message['role'] == 'assistant':
            message['speech_text'] = speech_text(message['content'])
        if message['role'] == 'assistant' and message['status'] == 'done':
            path = ROOT / 'data/replays' / (message['turn_id'] + '.mp4')
            if path.is_file():
                message['replay_url'] = f'/api/conversations/{cid}/messages/{message["id"]}/replay'
            elif path.with_suffix('.pending').is_file():
                message['replay_pending'] = True
    return records


async def replay_file(request):
    service, cid, _ = get_conversation(request)
    message = next((m for m in service.store.messages(cid) if m['id'] == request.match_info['mid'] and m['role'] == 'assistant' and m['status'] == 'done'), None)
    if not message:
        raise web.HTTPNotFound(reason='没有可重播的回答')
    path = ROOT / 'data/replays' / (message['turn_id'] + '.mp4')
    if not path.is_file():
        raise web.HTTPNotFound(reason='回放尚未保存或本轮未启用实时播放')
    return web.FileResponse(path, headers={'Cache-Control': 'private, no-store'})


async def send_turn(request):
    service, cid, _ = get_conversation(request)
    data = await body(request)
    try:
        groups = selected_groups(data)
        bindings = service.capabilities.snapshot(request['owner'], data)
        if (bindings['servers'] or bindings['skills']) and not isinstance(service.provider, CompatibleProvider):
            raise ToolError('MCP 与 Skill 需要真实大模型，请先配置兼容模型服务。')
    except ToolError as error:
        raise web.HTTPBadRequest(reason=str(error)) from None
    text = required_text(data, 'text', 4000)
    use_realtime = data.get('playback') == 'realtime'
    strength = data.get('expression_strength', 1)
    if not isinstance(strength, (int, float)) or not 0 <= strength <= 1.5:
        raise web.HTTPBadRequest(reason='表情幅度必须在 0–1.5 之间')
    if not isinstance(data.get('subtitles', True), bool):
        raise web.HTTPBadRequest(reason='字幕选项必须为布尔值')
    if not isinstance(data.get('moneyprinter_enabled', False), bool):
        raise web.HTTPBadRequest(reason='MoneyPrinter 工具选项必须为布尔值')
    if use_realtime and not service.realtime.connected(cid, data.get('connection_id', '')):
        raise web.HTTPConflict(reason='请先连接实时数字人')
    async with service.lock(cid):
        # Recheck after acquiring the lock in case another request deleted the conversation.
        _, _, record = get_conversation(request)
        await service.cancel(cid)
        tid = str(uuid4())
        if use_realtime:
            try:
                await service.realtime.request('begin', dict(turn_id=tid, emotion=record['emotion'], strength=strength, subtitles=data.get('subtitles', True)))
            except RuntimeError as error:
                raise web.HTTPBadRequest(reason=str(error)) from None
        service.store.add_message(cid, tid, 'user', text)
        service.turns[cid] = tid
        service.tasks[cid] = asyncio.create_task(service.generate(cid, request['owner'], tid, record['emotion'], use_realtime, data.get('subtitles', True), data.get('moneyprinter_enabled', False), groups, bindings))
    return web.json_response({'turn_id': tid}, status=202)


async def interrupt(request):
    service, cid, _ = get_conversation(request)
    async with service.lock(cid):
        await service.cancel(cid)
    return web.json_response({'status': 'cancelled'})


async def emotion(request):
    service, cid, _ = get_conversation(request)
    value = required_text(await body(request), 'emotion', 30)
    if value not in BY_ID:
        raise web.HTTPBadRequest(reason='该情绪尚未开放')
    async with service.lock(cid):
        get_conversation(request)
        await service.cancel(cid)
        service.store.emotion(cid, value)
        if service.realtime.connected(cid):
            await service.realtime.request('emotion', dict(emotion=value))
        await service.emit(cid, 'emotion.changed', emotion=value)
    return web.json_response({'emotion': value})


async def delete_conversation(request):
    service, cid, _ = get_conversation(request)
    async with service.lock(cid):
        await service.cancel(cid)
        await service.realtime.close(cid)
        for message in service.store.messages(cid):
            for suffix in ('.mp4', '.pending', '.part.mp4'):
                (ROOT / 'data/replays' / (message['turn_id'] + suffix)).unlink(missing_ok=True)
        service.store.delete(cid)
        for socket in list(service.sockets.get(cid, set())):
            await socket.close()
    return web.json_response({'deleted': True})


async def events(request):
    service, cid, _ = get_conversation(request)
    socket = web.WebSocketResponse(heartbeat=20)
    await socket.prepare(request)
    service.sockets.setdefault(cid, set()).add(socket)
    snapshot = replay_messages(service, cid)
    for message in snapshot:
        if message['turn_id'] in service.partial and message['role'] == 'assistant':
            message['content'] = service.partial[message['turn_id']]
    await socket.send_json({'type': 'snapshot', 'payload': {'messages': snapshot, 'active_turn': service.turns.get(cid)}})
    try:
        async for message in socket:
            if message.type == WSMsgType.ERROR:
                break
    finally:
        service.sockets.get(cid, set()).discard(socket)
    return socket


async def memories(request):
    store = request.app[SERVICE].store
    if request.method == 'GET':
        return web.json_response(store.memories(request['owner']))
    data = await body(request)
    if data.get('confirmed') is not True:
        raise web.HTTPBadRequest(reason='保存记忆需要明确确认')
    text = required_text(data, 'content', 500)
    if len(store.memories(request['owner'])) >= 30:
        raise web.HTTPBadRequest(reason='最多保存 30 条记忆，请先删除旧记忆')
    return web.json_response(store.remember(request['owner'], text), status=201)


async def render_media(request):
    service, cid, record = get_conversation(request)
    data = await body(request)
    if not isinstance(data.get('subtitles', True), bool):
        raise web.HTTPBadRequest(reason='字幕选项必须为布尔值')
    await service.realtime.interrupt(cid)
    async with service.lock(cid):
        get_conversation(request)
        if cid in service.turns or service.avatar.busy:
            raise web.HTTPConflict(reason='请等待当前回答或视频完成')
        message = next((m for m in service.store.messages(cid) if m['id'] == data.get('message_id') and m['role'] == 'assistant' and m['status'] == 'done'), None)
        if not message:
            raise web.HTTPBadRequest(reason='请选择一条已完成的傻妞回答')
        script = speech_text(message['content'])
        if not script:
            raise web.HTTPBadRequest(reason='这条回答没有可朗读的内容')
        if not 1 <= len(script) <= 240:
            raise web.HTTPBadRequest(reason='口型视频暂支持 240 字以内的回答，请让傻妞简短回答')
        await service.avatar.cancel(cid)
        task = asyncio.create_task(service.avatar.render(cid, script, record['emotion'], data.get('subtitles', True), record['avatar_id']))
        service.avatar.jobs[cid] = task
        await asyncio.sleep(0)
    try:
        return web.json_response(await asyncio.shield(task))
    except asyncio.CancelledError:
        if task.cancelled():
            return web.json_response({'error': '视频生成已取消'}, status=409)
        raise
    except RuntimeError as error:
        raise web.HTTPBadRequest(reason=str(error)) from None


async def media_file(request):
    service, cid, _ = get_conversation(request)
    record = service.avatar.files.get(request.match_info['jobid'])
    if not record or record[0] != cid:
        raise web.HTTPNotFound(reason='视频已失效，请重新生成')
    kind = request.match_info.get('kind')
    suffix = {'audio': '.wav', 'srt': '.srt', 'vtt': '.vtt'}.get(kind)
    if kind and not suffix:
        raise web.HTTPNotFound(reason='资源不存在')
    path = record[1].with_suffix(suffix) if suffix else record[1]
    if not path.is_file():
        raise web.HTTPNotFound(reason='资源已失效，请重新生成')
    headers = {'Cache-Control': 'private, no-store'}
    if kind:
        headers['Content-Disposition'] = f'attachment; filename="shaniu{suffix}"'
    return web.FileResponse(path, headers=headers)


async def media_offer(request):
    service, cid, record = get_conversation(request)
    data = await body(request)
    sdp = required_text(data, 'sdp', 60000)
    if data.get('type') != 'offer':
        raise web.HTTPBadRequest(reason='需要 WebRTC offer')
    async with service.lock(cid):
        get_conversation(request)
        await service.cancel(cid)
        try:
            return web.json_response(await service.realtime.offer(cid, dict(sdp=sdp, type='offer', emotion=record['emotion'], avatar_id=record['avatar_id'])))
        except RuntimeError as error:
            raise web.HTTPBadRequest(reason=str(error)) from None


async def media_connection(request):
    service, cid, _ = get_conversation(request)
    sid = request.match_info['sid']
    if not service.realtime.connected(cid, sid):
        raise web.HTTPNotFound(reason='实时连接已失效')
    if request.method == 'DELETE':
        async with service.lock(cid):
            await service.realtime.close(cid, sid)
        return web.json_response({'closed': True})
    try:
        return web.json_response(await service.realtime.request('status'))
    except RuntimeError as error:
        raise web.HTTPBadRequest(reason=str(error)) from None


async def forget(request):
    if not request.app[SERVICE].store.forget(request['owner'], request.match_info['mid']):
        raise web.HTTPNotFound(reason='记忆不存在')
    return web.json_response({'deleted': True})


async def index(request):
    path = ROOT / 'frontend/dist/index.html'
    if not path.is_file():
        return web.Response(text='请先在 frontend 运行 npm run build，或使用 Vite 开发服务器。', status=503)
    return web.FileResponse(path)


def create_app(settings=None, provider=None):
    service = Dialogue(settings or Settings.from_env(), provider)
    app = web.Application(middlewares=[boundary], client_max_size=65536)
    app[SERVICE] = service
    app.add_routes([
        web.get('/api/health', health), web.get('/api/emotions', emotions),
        web.get('/api/avatars', avatars), web.get('/api/avatars/{avatar_id}/preview', preview_avatar),
        web.patch('/api/conversations/{cid}/avatar', select_avatar),
        web.get('/api/agent/tools', agent_tools),
        web.get('/api/agent/bindings', agent_bindings),
        web.post('/api/agent/bindings', agent_bindings),
        web.delete('/api/agent/bindings/{binding_id}', agent_binding_action),
        web.post('/api/agent/bindings/{binding_id}/test', agent_binding_action),
        web.get('/api/workspace/file', workspace_file),
        web.post('/api/agent/tools/{name}', call_agent_tool),
        web.get('/api/conversations', conversations), web.post('/api/conversations', conversations),
        web.get('/api/conversations/{cid}/messages', messages),
        web.get('/api/conversations/{cid}/media-tasks', conversation_media_tasks),
        web.get('/api/conversations/{cid}/media-tasks/{taskid}/files/{filename}', media_task_file),
        web.get('/api/conversations/{cid}/media-tasks/{taskid}/motion/{jobid}/{filename}', motion_file),
        web.get('/api/conversations/{cid}/media-tasks/{taskid}/post/{jobid}/{filename}', post_file),
        web.post('/api/motion/music', upload_motion_music),
        web.get('/api/conversations/{cid}/messages/{mid}/replay', replay_file),
        web.post('/api/conversations/{cid}/turns', send_turn),
        web.post('/api/conversations/{cid}/interrupt', interrupt),
        web.post('/api/conversations/{cid}/media', render_media),
        web.post('/api/conversations/{cid}/media/offer', media_offer),
        web.get('/api/conversations/{cid}/media/connection/{sid}', media_connection),
        web.delete('/api/conversations/{cid}/media/connection/{sid}', media_connection),
        web.get('/api/conversations/{cid}/media/{jobid}', media_file),
        web.get('/api/conversations/{cid}/media/{jobid}/{kind}', media_file),
        web.patch('/api/conversations/{cid}/emotion', emotion),
        web.delete('/api/conversations/{cid}', delete_conversation),
        web.get('/api/conversations/{cid}/events', events),
        web.get('/api/memories', memories), web.post('/api/memories', memories),
        web.delete('/api/memories/{mid}', forget), web.get('/', index),
    ])
    assets = ROOT / 'frontend/dist/assets'
    if assets.is_dir():
        app.router.add_static('/assets', assets)

    async def shutdown(app):
        await service.moneyprinter.motion.shutdown()
        await service.moneyprinter.post.shutdown()
        await asyncio.gather(*(service.avatar.cancel(cid) for cid in set(service.avatar.jobs) | {cid for cid, _ in service.avatar.files.values()}))
        await asyncio.gather(*(service.cancel(cid) for cid in list(service.tasks)))
        await service.realtime.shutdown()
        await asyncio.gather(*(ws.close() for sockets in list(service.sockets.values()) for ws in list(sockets)))
    app.on_shutdown.append(shutdown)
    return app


def main():
    from .launcher import main as launch
    return launch()


if __name__ == '__main__':
    raise SystemExit(main())
