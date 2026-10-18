"""Compatibility facade over independently maintained capability modules."""
from .catalog import TOOL_GROUP
from .workspace import WorkspaceTools
from .web import WebTools


class AssistantTools(WorkspaceTools):
    def __init__(self, workspace=None, documents=None):
        super().__init__(workspace, documents)
        self.web = WebTools()

    async def fetch(self, url, limit=12000):
        return await self.web.fetch(url, limit)

    async def call(self, owner, name, arguments):
        if TOOL_GROUP.get(name) == 'web':
            return await self.web.call(owner, name, arguments)
        return await super().call(owner, name, arguments)
