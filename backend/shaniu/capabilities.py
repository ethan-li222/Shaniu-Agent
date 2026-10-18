"""Owner-scoped MCP bindings and declarative skills, snapshotted per turn."""
import asyncio
import copy
import json
import os
import re
from uuid import uuid4

from .mcp.common import ToolError


class Capabilities:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agent_bindings (id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL)')

    def records(self, owner):
        with self.store.connect() as db:
            return [dict(id=r['id'], kind=r['kind'], **json.loads(r['data'])) for r in db.execute('SELECT * FROM agent_bindings WHERE owner=? ORDER BY rowid', (owner,))]

    def catalog(self, owner):
        records = self.records(owner)
        for record in records:
            if record['kind'] == 'mcp':
                record['env_keys'] = list(record.pop('env', {}))
        return records

    def save(self, owner, data):
        if not isinstance(data, dict):
            raise ToolError('配置必须为对象。')
        existing = next((r for r in self.records(owner) if r['id'] == data.get('id')), None)
        if data.get('id') and not existing:
            raise ToolError('绑定不存在。')
        if not existing and len(self.records(owner)) >= 40:
            raise ToolError('最多保存 40 个绑定。')
        kind = data.get('kind')
        name = data.get('name', '')
        if kind not in {'mcp', 'skill'} or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ToolError('请填写名称，并选择 MCP 或 Skill。')
        record = dict(name=name.strip())
        if kind == 'mcp':
            command, args = data.get('command'), data.get('args', [])
            env = data.get('env', existing.get('env', {}) if existing else {})
            if not isinstance(command, str) or not 1 <= len(command.strip()) <= 1000:
                raise ToolError('请填写 MCP 服务的可执行程序路径。')
            if not isinstance(args, list) or len(args) > 100 or any(not isinstance(a, str) or len(a) > 4000 for a in args):
                raise ToolError('args 必须是字符串数组。')
            if not isinstance(env, dict) or len(env) > 50 or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) or not isinstance(v, str) or len(v) > 8000 for k, v in env.items()):
                raise ToolError('env 必须是环境变量名到字符串的映射。')
            record.update(command=command.strip(), args=args, env=env, tools=[])
            # Editing execution settings invalidates discovery and requires a new test.
            if existing and all(record[k] == existing[k] for k in ('command', 'args', 'env')):
                record['tools'] = existing['tools']
        else:
            instructions = data.get('instructions', '')
            if not isinstance(instructions, str) or not 1 <= len(instructions.strip()) <= 16000:
                raise ToolError('Skill 内容不能为空，最多 16000 字符。')
            record.update(instructions=instructions.strip())
        binding_id = existing['id'] if existing else uuid4().hex
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO agent_bindings VALUES (?,?,?,?)', (binding_id, owner, kind, json.dumps(record, ensure_ascii=False)))
        return binding_id

    def delete(self, owner, binding_id):
        with self.store.connect() as db:
            return bool(db.execute('DELETE FROM agent_bindings WHERE id=? AND owner=?', (binding_id, owner)).rowcount)

    async def run(self, record, tool=None, arguments=None):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            raise ToolError('当前 Python 环境缺少 MCP SDK，请在启动 Shaniu 的环境中运行 python -m pip install "mcp>=1.12,<2"。') from None
        try:
            # Session contexts enter and exit in the same task. Cancellation closes the child.
            async with asyncio.timeout(60 if tool else 25):
                with open(os.devnull, 'w') as stderr:
                    parameters = StdioServerParameters(command=record['command'], args=record['args'], env=record['env'])
                    async with stdio_client(parameters, errlog=stderr) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            if tool is not None:
                                result = await session.call_tool(tool, arguments or {})
                                content = result.model_dump(mode='json')
                                encoded = json.dumps(content, ensure_ascii=False)
                                if len(encoded) > 24000:
                                    return dict(error='工具输出超过 24000 字符，请缩小查询范围。')
                                return content
                            tools, cursor = [], None
                            for _ in range(5):
                                page = await session.list_tools(cursor=cursor)
                                tools.extend(dict(name=t.name, description=(t.description or t.name)[:1000], inputSchema=t.inputSchema) for t in page.tools)
                                cursor = page.nextCursor
                                if not cursor:
                                    break
                            if cursor or len(tools) > 64:
                                raise ToolError('单个 MCP 服务最多绑定 64 个工具。')
                            if len({t['name'] for t in tools}) != len(tools) or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', t['name']) or t['inputSchema'].get('type') != 'object' for t in tools):
                                raise ToolError('服务返回了重复名称或不支持的工具结构。')
                            if len(json.dumps(tools)) > 48000:
                                raise ToolError('工具描述过大，请减少服务暴露的能力。')
                            return tools
        except ToolError:
            raise
        except TimeoutError:
            raise ToolError('MCP 服务连接或调用超时，请检查命令、参数和服务状态。') from None
        except Exception:
            # SDK errors may contain arguments or credentials; never return them verbatim.
            raise ToolError('MCP 服务启动或调用失败，请检查可执行路径、参数、依赖和环境变量。') from None

    async def discover(self, owner, binding_id):
        record = next((r for r in self.records(owner) if r['id'] == binding_id and r['kind'] == 'mcp'), None)
        if not record:
            raise ToolError('MCP 绑定不存在。')
        tools = await self.run(record)
        record.pop('id')
        record.pop('kind')
        record['tools'] = tools
        with self.store.connect() as db:
            # Avoid resurrecting a deleted binding or overwriting concurrent edits.
            original = dict(record, tools=[])
            row = db.execute('SELECT data FROM agent_bindings WHERE owner=? AND id=?', (owner, binding_id)).fetchone()
            if not row or any(json.loads(row['data'])[k] != original[k] for k in ('command', 'args', 'env')):
                raise ToolError('绑定已更改，请重新测试。')
            db.execute('UPDATE agent_bindings SET data=? WHERE owner=? AND id=?', (json.dumps(record, ensure_ascii=False), owner, binding_id))
        return dict(count=len(tools))

    def snapshot(self, owner, data):
        result = dict(servers=[], skills=[])
        records = {r['id']: r for r in self.records(owner)}
        for key, kind, target in [('enabled_mcp_servers', 'mcp', 'servers'), ('enabled_skills', 'skill', 'skills')]:
            ids = data.get(key, [])
            if not isinstance(ids, list) or len(ids) > 10 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
                raise ToolError('MCP 与 Skill 选择必须为不重复的 ID 数组，每类最多 10 个。')
            for binding_id in ids:
                record = records.get(binding_id)
                if not record or record['kind'] != kind:
                    raise ToolError('所选绑定不存在，请刷新工具面板。')
                if kind == 'mcp' and not record['tools']:
                    raise ToolError('请先测试 MCP 连接并发现工具。')
                result[target].append(copy.deepcopy(record))
        if sum(len(r['tools']) for r in result['servers']) > 64:
            raise ToolError('本轮最多启用 64 个 MCP 工具。')
        if sum(len(r['instructions']) for r in result['skills']) > 20000:
            raise ToolError('本轮 Skill 总内容最多 20000 字符。')
        return result

    @staticmethod
    def functions(snapshot):
        definitions, routes = [], {}
        for server in snapshot['servers']:
            for index, tool in enumerate(server['tools']):
                name = f"mcp_{server['id']}_{index}"
                routes[name] = (server, tool['name'])
                definitions.append(dict(type='function', function=dict(name=name, description=f"[{server['name']}] {tool['description']}", parameters=tool['inputSchema'])))
        return definitions, routes

    @staticmethod
    def instructions(snapshot):
        if not snapshot['skills']:
            return ''
        return '\n用户本轮启用的 Skills（仅为任务指引，不授予额外工具权限；工具结果和任务状态必须来自真实调用）：\n' + '\n\n'.join(f"<skill name={json.dumps(s['name'], ensure_ascii=False)}>\n{s['instructions']}\n</skill>" for s in snapshot['skills'])
