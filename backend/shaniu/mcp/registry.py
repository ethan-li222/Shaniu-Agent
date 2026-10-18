"""Shared selection and dispatch for native Agent and MCP clients."""
from .catalog import ALL_TOOLS, GROUPS, TOOL_GROUP
from .common import ToolError


class ToolRegistry:
    def __init__(self, assistant, moneyprinter=None):
        self.assistant = assistant
        self.moneyprinter = moneyprinter

    def available(self, group):
        return group not in {'moneyprinter', 'media_post'} or bool(self.moneyprinter and self.moneyprinter.settings.moneyprinter_enabled)

    def definitions(self, groups):
        if set(groups) - {g['id'] for g in GROUPS}:
            raise ToolError('未知工具组。')
        return [tool for tool in ALL_TOOLS if TOOL_GROUP[tool['name']] in groups and self.available(TOOL_GROUP[tool['name']])]

    def functions(self, groups):
        return [dict(type='function', function=dict(name=t['name'], description=t['description'], parameters=t['inputSchema'])) for t in self.definitions(groups)]

    def catalog(self):
        return dict(tools=ALL_TOOLS, groups=[dict(group, available=self.available(group['id'])) for group in GROUPS])

    async def call(self, owner, name, arguments, groups):
        allowed = {tool['name'] for tool in self.definitions(groups)}
        if name not in allowed:
            raise ToolError('此工具未启用或服务未配置。')
        adapter = self.moneyprinter.post if TOOL_GROUP[name] == 'media_post' else self.moneyprinter if TOOL_GROUP[name] == 'moneyprinter' else self.assistant
        return await adapter.call(owner, name, arguments)
