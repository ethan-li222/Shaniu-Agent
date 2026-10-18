"""Optional MCP transport; core capabilities do not require the MCP SDK."""
import asyncio
import json
import os

from .assistant import AssistantTools
from .common import ToolError
from .registry import ToolRegistry


def create_server(groups=None, assistant=None, moneyprinter=None):
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    if groups is None:
        groups = {v.strip() for v in os.getenv('SHANIU_MCP_GROUPS', 'files,knowledge').split(',') if v.strip()}
    groups = frozenset(groups)
    registry = ToolRegistry(assistant or AssistantTools(), moneyprinter)
    definitions = registry.definitions(groups)
    if any(not registry.available(group) for group in groups):
        raise ToolError('请求的工具组尚未配置。')
    server = Server('Shaniu assistant tools')

    @server.list_tools()
    async def list_tools():
        return [Tool(**tool) for tool in definitions]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            result = await registry.call('mcp-local', name, arguments, groups)
        except ToolError as error:
            raise ValueError(str(error)) from None
        result.pop('artifacts', None)
        return [TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]

    return server


async def run():
    from mcp.server.stdio import stdio_server
    groups = {v.strip() for v in os.getenv('SHANIU_MCP_GROUPS', 'files,knowledge').split(',') if v.strip()}
    moneyprinter = None
    if {'moneyprinter', 'media_post'} & groups:
        from dotenv import load_dotenv
        from ..config import ROOT, Settings
        from ..moneyprinter import MoneyPrinterTools
        from ..storage import Store
        load_dotenv(ROOT / '.env')
        settings = Settings(moneyprinter_enabled=True, moneyprinter_url=os.getenv('SHANIU_MPT_URL', 'http://127.0.0.1:8080'))
        moneyprinter = MoneyPrinterTools(settings, Store(ROOT / 'data/assistant-mcp.sqlite3'))
    server = create_server(groups=groups, moneyprinter=moneyprinter)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    asyncio.run(run())
