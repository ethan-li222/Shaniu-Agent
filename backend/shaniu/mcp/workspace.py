"""Per-owner notes and source-based local learning retrieval."""
import asyncio
import hashlib
import os
import re
from pathlib import Path
from urllib.parse import quote
from ..config import ROOT
from .common import ToolError
from .catalog import LOCAL_TOOLS, TOOL_GROUP

TEXT_SUFFIXES = {'.txt', '.md', '.json', '.csv', '.py', '.ts', '.js', '.html', '.css', '.yaml', '.yml'}

class WorkspaceTools:
    def __init__(self, workspace=None, documents=None):
        self.workspace = Path(workspace or ROOT / 'data/workspaces').resolve()
        self.documents = Path(documents or ROOT / 'docs').resolve()

    def user_root(self, owner):
        return self.workspace / hashlib.sha256(owner.encode()).hexdigest()[:32]

    def path(self, owner, value='.', directory=False):
        if not isinstance(value, str) or len(value) > 200:
            raise ToolError('工作区路径不正确。')
        value = value.replace('\\', '/')
        if value != '.' and (not value or any(not part or part.startswith('.') or part.endswith((' ', '.')) or re.fullmatch(r'(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?', part) or ':' in part or any(ord(c) < 32 or c in '<>"|?*' for c in part) for part in value.split('/'))):
            raise ToolError('请使用工作区内的相对路径，不能越界或访问隐藏文件。')
        root = self.user_root(owner).resolve()
        target = (root / value).resolve()
        if not root.is_relative_to(self.workspace) or not target.is_relative_to(root) or (not directory and (target == root or target.suffix.lower() not in TEXT_SUFFIXES)):
            raise ToolError('只能访问工作区内支持的文本文件。')
        return target

    @staticmethod
    def read(path):
        if not path.is_file() or path.stat().st_size > 65536:
            raise ToolError('文本文件不存在或超过 64KB。')
        try:
            return path.read_text(encoding='utf-8-sig')
        except UnicodeError:
            raise ToolError('当前只支持 UTF-8 文本文件。') from None

    def search(self, owner, query, knowledge=False):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ToolError('请输入 1–200 字的检索关键词。')
        # Chinese bigrams allow short natural-language queries without a tokenizer.
        tokens = re.findall(r'[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}', query.lower())
        terms = list(dict.fromkeys([query.lower().strip()] + [t[i:i+2] for t in tokens if re.match(r'[\u4e00-\u9fff]', t) for i in range(len(t)-1)] + tokens))[:80]
        roots = [('workspace', self.user_root(owner))]
        if knowledge:
            roots.append(('docs', self.documents))
        hits, scanned = [], 0
        for source, root in roots:
            if not root.is_dir():
                continue
            root = root.resolve()
            if source == 'workspace' and not root.is_relative_to(self.workspace):
                raise ToolError('工作区路径不正确。')
            for folder, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith('.') and (Path(folder)/d).resolve().is_relative_to(root) and not (Path(folder)/d).is_symlink()]
                for name in sorted(files):
                    scanned += 1
                    if scanned > 500:
                        break
                    path = Path(folder)/name
                    if name.startswith('.') or path.suffix.lower() not in TEXT_SUFFIXES or not path.resolve().is_relative_to(root):
                        continue
                    try:
                        text = self.read(path)
                    except (ToolError, OSError):
                        continue
                    score = sum(text.lower().count(term) for term in terms)
                    if score:
                        position = min(text.lower().find(term) for term in terms if term in text.lower())
                        hits.append(dict(source=f'{source}/{path.relative_to(root).as_posix()}', score=score,
                                         excerpt=text[max(0, position-100):position+700]))
                if scanned > 500:
                    break
        hits.sort(key=lambda h: (-h['score'], h['source']))
        return dict(query=query, matches=hits[:6], truncated=scanned > 500, message='找到相关片段。' if hits else '没有找到匹配资料，请换用更具体的关键词。')

    async def call(self, owner, name, arguments):
        tool = next((t for t in LOCAL_TOOLS if t['name'] == name and TOOL_GROUP[name] in {'files', 'knowledge'}), None)
        if not tool or not isinstance(arguments, dict) or set(arguments)-set(tool['inputSchema']['properties']) or any(k not in arguments for k in tool['inputSchema']['required']):
            raise ToolError('工具名称或参数不正确。')
        try:
            if name == 'workspace_list':
                path = self.path(owner, arguments.get('path', '.'), directory=True)
                if not path.exists() and path == self.user_root(owner):
                    return dict(entries=[], message='工作区还没有文件，可以先保存一份笔记。')
                if not path.is_dir():
                    raise ToolError('工作区目录不存在。')
                entries = [dict(name=p.name, directory=p.is_dir()) for p in sorted(path.iterdir()) if not p.name.startswith('.') and p.resolve().is_relative_to(self.user_root(owner).resolve())]
                return dict(entries=entries[:100], truncated=len(entries)>100)
            if name == 'workspace_read':
                return dict(path=arguments['path'], content=self.read(self.path(owner, arguments['path'])))
            if name == 'workspace_write':
                path = self.path(owner, arguments['path'])
                content = arguments['content']
                if not isinstance(content, str) or len(content)>16000:
                    raise ToolError('笔记内容必须为不超过 16000 字的文本。')
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x', encoding='utf-8') as file:
                    file.write(content)
                return dict(path=arguments['path'], bytes=len(content.encode()), message='文件已保存到你的傻妞工作区。', artifacts=[dict(filename=path.name, kind='document', url='/api/workspace/file?path='+quote(arguments['path'], safe=''))])
            if name in {'workspace_search', 'knowledge_search'}:
                return await asyncio.to_thread(self.search, owner, arguments['query'], name == 'knowledge_search')
        except FileExistsError:
            raise ToolError('文件已存在，请使用新文件名；已有文件不会被覆盖。') from None
        except (OSError, ValueError):
            raise ToolError('文件或资料无法读取，请检查参数。') from None
