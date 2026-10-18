"""External HTTP adapter. Never imports or edits the MoneyPrinterTurbo checkout."""
import asyncio
import json
import math
import re
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

import aiohttp


from .mcp.common import ToolError, schema


TOOLS = [
    dict(name='moneyprinter_enhance', description='用户明确要求动效增强时，对本用户已完成的视频提交增强任务。保留普通版，复用同一旁白，添加标题/句级要点动画、可选字幕与音效。用 status 查看 enhancement 状态，不重复提交。', inputSchema=schema({
        'task_id': dict(type='string'), 'style': dict(type='string', enum=['clean', 'ink'], default='clean'),
        'subtitles': dict(type='boolean', default=True), 'sound_effects': dict(type='boolean', default=True),
        'music_id': dict(type='string', description='可选，用户通过界面上传的配乐 ID，不能填写本机路径'),
    }, ['task_id'])),
    dict(name='moneyprinter_health', description='检查本地 MoneyPrinterTurbo 服务是否已启动及接口是否兼容。', inputSchema=schema({})),
    dict(name='moneyprinter_create', description='用户明确要求制作视频、音频或字幕时提交异步任务。返回任务 ID，不代表生成完成。不要自动重复提交失败或超时的请求。缺少主题/正文时先问用户。', inputSchema=schema({
        'kind': dict(type='string', enum=['video', 'audio', 'subtitle']),
        'subject': dict(type='string', minLength=1, maxLength=200, description='视频主题；音频或字幕任务的名称'),
        'script': dict(type='string', maxLength=10000, description='视频默认省略，由 MoneyPrinter 自己调用配置的模型生成文案；仅用户明确指定旁白时传入。音频和字幕任务必填。'),
        'terms': dict(type='string', maxLength=500, description='视频默认省略，由 MoneyPrinter 生成英文素材搜索词；仅用户明确指定时传入，逗号分隔。'),
        'aspect': dict(type='string', enum=['9:16', '16:9', '1:1'], default='9:16'),
        'subtitles': dict(type='boolean', default=True),
        'voice': dict(type='string', maxLength=100, default='zh-CN-XiaoxiaoNeural-Female'),
        'voice_rate': dict(type='number', minimum=0.5, maximum=2, default=1),
    }, ['kind', 'subject'])),
    dict(name='moneyprinter_status', description='查询本用户提交的任务，返回进度、真实成功/失败状态及完成后的文件链接。只查询一次，不循环等待。', inputSchema=schema({'task_id': dict(type='string')}, ['task_id'])),
    dict(name='moneyprinter_tasks', description='列出本用户最近提交的任务 ID；随后可查询具体任务。', inputSchema=schema({})),
]


def task_id(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ToolError('任务 ID 必须是 UUID。') from None


class MoneyPrinterTools:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self._motion = None
        self._post = None
        parsed = urlsplit(settings.moneyprinter_url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise ValueError('SHANIU_MPT_URL 必须是本机 HTTP 服务根地址，例如 http://127.0.0.1:8080')
        self.url = settings.moneyprinter_url.rstrip('/')

    @property
    def post(self):
        if self._post is None:
            from .media_post import PostTasks
            self._post = PostTasks(self.settings, self.store, self)
        return self._post

    @property
    def motion(self):
        if self._motion is None:
            from .motion import MotionTasks
            self._motion = MotionTasks(self.settings, self.store, self)
        return self._motion

    async def request(self, method, path, payload=None):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=3)) as client:
                async with client.request(method, self.url + path, json=payload, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ToolError(f'MoneyPrinterTurbo HTTP {response.status}；请检查服务配置。')
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise ToolError('MoneyPrinterTurbo 响应过大。')
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ToolError('MoneyPrinterTurbo 响应格式不正确。')
                    return value
        except (aiohttp.ClientError, asyncio.TimeoutError):
            hint = '提交结果未知，请先检查 MoneyPrinterTurbo 的任务记录，避免重复提交。' if method == 'POST' else '请先启动 MoneyPrinterTurbo API 服务。'
            raise ToolError('无法连接 MoneyPrinterTurbo，或请求超时。' + hint) from None
        except (ValueError, UnicodeError):
            raise ToolError('MoneyPrinterTurbo 未返回有效 JSON。') from None

    async def data(self, method, path, payload=None):
        value = await self.request(method, path, payload)
        if value.get('status') != 200 or not isinstance(value.get('data'), dict):
            raise ToolError('MoneyPrinterTurbo 拒绝请求或响应格式不兼容。')
        return value['data']

    async def call(self, owner, name, arguments):
        if not self.settings.moneyprinter_enabled:
            raise ToolError('MoneyPrinterTurbo 工具未启用，请设置 SHANIU_MPT_ENABLED=true 后重启 Shaniu。')
        definition = next((tool for tool in TOOLS if tool['name'] == name), None)
        if not definition:
            raise ToolError('未知工具。')
        if not isinstance(arguments, dict) or set(arguments) - set(definition['inputSchema']['properties']):
            raise ToolError('工具参数不正确或含不支持的字段。')
        if name == 'moneyprinter_health':
            spec = await self.request('GET', '/openapi.json')
            required = ['/api/v1/videos', '/api/v1/audio', '/api/v1/subtitle', '/api/v1/tasks/{task_id}']
            ready = all(path in spec.get('paths', {}) for path in required)
            return dict(ready=ready, message='MoneyPrinterTurbo 已连接。' if ready else '服务接口不兼容。')
        if name == 'moneyprinter_tasks':
            return dict(tasks=self.store.external_tasks(owner))
        if name == 'moneyprinter_create':
            return await self.create(owner, arguments)
        if name == 'moneyprinter_enhance':
            tid = task_id(arguments.get('task_id'))
            return await self.motion.submit(owner, tid, arguments)
        tid = task_id(arguments.get('task_id'))
        if not self.store.external_task(owner, tid):
            raise ToolError('任务不存在或不属于当前用户。')
        data = await self.data('GET', '/api/v1/tasks/' + tid)
        state = data.get('state')
        artifacts = []
        if state == 1:
            for field in ['videos', 'combined_videos', 'audio_file', 'subtitle_path']:
                values = data.get(field, [])
                if isinstance(values, str):
                    values = [values]
                if isinstance(values, list):
                    for value in values[:10]:
                        artifact = self.artifact(tid, value)
                        if artifact:
                            artifacts.append(dict(kind=field, **artifact))
        result = dict(task_id=tid, state=state, status={1: 'completed', -1: 'failed', 4: 'processing'}.get(state, 'pending'), progress=data.get('progress', 0), artifacts=artifacts)
        stages = {'script': '生成文案', 'terms': '生成搜索词', 'audio': '合成语音', 'subtitle': '生成字幕', 'materials': '获取素材', 'render': '合成视频'}
        stage = data.get('shaniu_stage')
        if stage in stages:
            result['stage'] = stages[stage]
        reasons = {'script_failed': '文案生成失败，请检查外部模型配置，或提供完整旁白。', 'upstream_llm_auth': '文案模型认证失败，请检查 MoneyPrinter 的模型密钥；此任务尚未开始素材搜索。', 'terms_failed': '搜索词生成失败。', 'audio_failed': '语音合成失败，请检查在线语音连接。', 'subtitle_failed': '字幕生成失败。', 'materials_failed': '素材获取失败，请检查搜索结果、素材服务密钥和网络。', 'render_failed': '视频合成失败，请检查媒体环境。', 'service_restarted': '生成期间服务重启，任务已中断，请确认后重新提交。', 'generation_failed': '生成异常，请查看本机服务日志。'}
        if state == -1:
            result['failure_reason'] = reasons.get(data.get('failure_reason'), '服务只报告生成失败，没有提供具体原因；不能据此推断是素材或网络问题。')
            if data.get('failure_reason') == 'audio_fallback_failed':
                result['failure_reason'] = '配音失败：在线语音不可用，本地中文语音回退也未成功。请检查 Windows 中文桌面语音安装和媒体环境。'
            elif data.get('failure_reason') == 'audio_online_failed':
                result['failure_reason'] = '在线配音不可用，本地回退未启用或当前语言、系统不支持。请检查语音连接或配置回退。'
        if data.get('speech_provider') in {'edge', 'windows', 'upstream'}:
            result['speech_provider'] = data['speech_provider']
        if data.get('speech_warning'):
            result['speech_warning'] = '在线配音不可用，已使用 Windows 本地中文语音，音色与在线配音不同。'
        if data.get('subtitle_alignment') == 'sentence_fallback':
            result['speech_warning'] = result.get('speech_warning', '') + '字幕使用整段时间，未获得可靠的逐词对齐。'
        enhancement = self.motion.latest(owner, tid)
        if enhancement:
            result['enhancement'] = enhancement
        result['postprocess'] = self.post.list(owner, tid)
        return result

    def artifact(self, tid, value):
        # Upstream returns videos as URLs, audio/subtitles as absolute local paths.
        # Expose only direct files inside this specific task, never arbitrary paths/hosts.
        if not isinstance(value, str):
            return None
        path = unquote(urlsplit(value).path if '://' in value else value).replace('\\', '/')
        parts = path.split('/')
        if len(parts) < 2 or parts[-2] != tid or not re.fullmatch(r'[A-Za-z0-9_.-]+\.(mp4|mp3|wav|srt|vtt)', parts[-1], re.I):
            return None
        filename = parts[-1]
        return dict(filename=filename, url=f'{self.url}/tasks/{tid}/{quote(filename)}')

    async def create(self, owner, args):
        kind, subject = args.get('kind'), args.get('subject')
        script, terms = args.get('script', ''), args.get('terms', '')
        voice = args.get('voice', 'zh-CN-XiaoxiaoNeural-Female')
        if kind not in {'video', 'audio', 'subtitle'}:
            raise ToolError('kind 必须是 video、audio 或 subtitle。')
        for value, limit in [(subject, 200), (script, 10000), (terms, 500), (voice, 100)]:
            if not isinstance(value, str) or len(value) > limit:
                raise ToolError('主题、文案、搜索词或语音参数不正确。')
        from .text import speech_text
        original_script = script
        script = speech_text(script)
        if original_script.strip() and not script:
            raise ToolError('文案没有可朗读的内容。')
        if not subject.strip() or (kind != 'video' and not script.strip()):
            raise ToolError('需要主题；音频和字幕任务还需要完整文案。')
        subtitles = args.get('subtitles', True)
        rate, aspect = args.get('voice_rate', 1), args.get('aspect', '9:16')
        if not isinstance(subtitles, bool) or isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not .5 <= rate <= 2 or aspect not in {'9:16', '16:9', '1:1'}:
            raise ToolError('字幕、语速或画面比例参数不正确。')
        if kind == 'subtitle' and not subtitles:
            raise ToolError('字幕任务不能关闭字幕，请改用 audio 任务。')
        payload = dict(video_script=script, voice_name=voice, voice_rate=rate, bgm_type='', video_language='zh-CN')
        if kind == 'video':
            payload.update(video_subject=subject.strip(), video_terms=terms or None, video_aspect=aspect, subtitle_enabled=subtitles, video_count=1, paragraph_number=1)
        elif kind == 'subtitle':
            # This checkout declares SubtitleRequest.subtitle_enabled as str.
            payload['subtitle_enabled'] = 'true'
        async def submit():
            data = await self.data('POST', '/api/v1/' + {'video': 'videos', 'audio': 'audio', 'subtitle': 'subtitle'}[kind], payload)
            tid = task_id(data.get('task_id'))
            self.store.save_external_task(owner, tid, kind, subject.strip())
            return dict(task_id=tid, status='submitted', message='任务已提交，请稍后查询进度。', subtitles=subtitles)
        task = asyncio.create_task(submit())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Dialogue interruption cannot undo an accepted upstream task. Finish
            # recording its ID (bounded by HTTP timeout) so it remains discoverable.
            await asyncio.gather(task, return_exceptions=True)
            raise
