"""Bounded public HTTP reader with DNS and redirect checks."""
import asyncio
import base64
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import aiohttp
from aiohttp.abc import AbstractResolver
from .common import ToolError
from .catalog import LOCAL_TOOLS, TOOL_GROUP


def public_url(url):
    if not isinstance(url, str) or len(url) > 2000 or any(c.isspace() for c in url):
        raise ToolError('请输入有效的公开网页地址。')
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ToolError('网页地址格式不正确。') from None
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or port not in {None, 80, 443}:
        raise ToolError('仅支持公开 HTTP/HTTPS 网页，不支持自定义端口或账号信息。')
    host = parts.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local')):
        raise ToolError('不能读取本机或内网地址。')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ToolError('不能读取本机或内网地址。')
    return url


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM, family=family)
        results = []
        for af, _, proto, _, address in addresses:
            if not ipaddress.ip_address(address[0]).is_global:
                raise ToolError('网页域名指向本机或内网，无法读取。')
            results.append(dict(hostname=host, host=address[0], port=port, family=af, proto=proto, flags=socket.AI_NUMERICHOST))
        return results

    async def close(self):
        pass


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden += 1
        if not self.hidden and tag in {'p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'tr'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden = max(0, self.hidden-1)

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


class WebTools:
    async def fetch(self, url, limit=12000):
        headers = {'User-Agent': 'Shaniu-Reader/0.2', 'Accept': 'text/html,text/plain,application/json'}
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=20, connect=5), trust_env=False) as client:
                for _ in range(4):
                    public_url(url)
                    async with client.get(url, headers=headers, allow_redirects=False) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            url = urljoin(url, response.headers.get('Location', ''))
                            continue
                        if response.status != 200:
                            raise ToolError(f'网页 HTTP {response.status}，可能不存在、需要登录或触发访问限制。')
                        if response.content_type not in {'text/html', 'text/plain', 'application/json', 'text/markdown'}:
                            raise ToolError('只支持文本网页或 JSON，不下载二进制文件。')
                        body = bytearray()
                        async for chunk in response.content.iter_chunked(32768):
                            body.extend(chunk)
                            if len(body) > 2_000_000:
                                raise ToolError('网页正文超过 2MB，请使用更具体的页面。')
                        text = body.decode(response.charset or 'utf-8', errors='replace')
                        if response.content_type == 'text/html':
                            parser = PageText()
                            parser.feed(text)
                            text = re.sub(r'[ \t]+', ' ', ''.join(parser.parts))
                            text = re.sub(r'\n\s*\n+', '\n\n', text).strip()
                        return dict(url=url, content=text[:limit], truncated=len(text) > limit)
                raise ToolError('网页跳转次数过多。')
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, LookupError):
            raise ToolError('网页连接失败或超时，请检查地址和网络。') from None

    async def call(self, owner, name, arguments):
        tool = next((t for t in LOCAL_TOOLS if t['name'] == name and TOOL_GROUP[name] == 'web'), None)
        if not tool or not isinstance(arguments, dict) or set(arguments)-set(tool['inputSchema']['properties']) or any(k not in arguments for k in tool['inputSchema']['required']):
            raise ToolError('网页工具名称或参数不正确。')
        try:
            if name == 'web_read':
                return await self.fetch(public_url(arguments['url']))
            repository = arguments['repository']
            if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,70}/[A-Za-z0-9_.-]{1,70}', repository) or any(part.startswith('.') for part in repository.split('/')):
                raise ToolError('仓库格式应为 owner/repo。')
            if name == 'github_readme':
                result = await self.fetch('https://api.github.com/repos/'+repository+'/readme', limit=1_000_000)
                if result['truncated']:
                    raise ToolError('README 太大，请提供具体文档链接。')
                data = json.loads(result['content'])
                if not isinstance(data, dict) or data.get('encoding') != 'base64' or not isinstance(data.get('content'), str):
                    raise ToolError('README 返回格式不受支持。')
                content = base64.b64decode(data['content'].replace('\n', ''), validate=True).decode('utf-8', errors='replace')
                return dict(repository=repository, url=data.get('html_url'), path=data.get('path'), content=content[:12000], truncated=len(content)>12000)
            data = json.loads((await self.fetch('https://api.github.com/repos/'+repository, limit=65536))['content'])
            return {key: data.get(key) for key in ('full_name', 'description', 'html_url', 'language', 'stargazers_count', 'forks_count', 'default_branch', 'license', 'updated_at')}
        except (OSError, ValueError):
            raise ToolError('网页或仓库资料无法读取，请检查参数。') from None
