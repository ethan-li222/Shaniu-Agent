"""Keep model Markdown intact; derive a separate script for speech/captions."""
import re

from markdown_it import MarkdownIt

MARKDOWN = MarkdownIt('js-default', {'html': True})
CODE_NOTICE = '代码内容请查看屏幕。'


def speech_text(source):
    # Reference definitions may arrive in a later streamed block. The readable
    # label is sufficient for speech regardless of when its URL is defined.
    source = re.sub(r'!?\[([^\]\n]+)\]\[[^\]\n]*\]', r'\1', source)
    def inline(tokens):
        output = []
        for token in tokens or []:
            if token.type in {'text', 'code_inline'}:
                output.append(token.content)
            elif token.type in {'softbreak', 'hardbreak'}:
                output.append('\n')
            elif token.type == 'image':
                output.append(inline(token.children) or token.content)
        return ''.join(output)

    output = []
    for token in MARKDOWN.parse(source):
        if token.type == 'inline':
            value = inline(token.children)
            value = re.sub(r'^\s*\[[ xX]\]\s*', '', value)
            output.append(value)
        elif token.type in {'fence', 'code_block'}:
            output.append(CODE_NOTICE)
        elif token.type in {'paragraph_close', 'heading_close', 'tr_close', 'list_item_close'}:
            output.append('\n')
        elif token.type in {'td_close', 'th_close'}:
            output.append('，')
    plain = ''.join(output)
    plain = re.sub(r'，\s*\n', '\n', plain)
    plain = re.sub(r'[ \t]+', ' ', plain)
    return re.sub(r'\n\s*\n+', '\n', plain).strip()


class MarkdownSpeechBuffer:
    """Commit formatted blocks only after completion; plain prose still streams.

    Fences and inline markup may span arbitrary model deltas. We hold Markdown
    paragraphs until a blank line/end, so URLs and partial delimiters aren't read.
    """
    def __init__(self):
        self.pending = ''
        self.line = ''
        self.fence = None

    def feed(self, delta='', final=False):
        output = []
        for char in delta:
            self.pending += char
            self.line += char
            if char == '\n':
                fence = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)\n$', self.line)
                closed = False
                if fence:
                    marker = fence.group(1)
                    if self.fence is None:
                        self.fence = marker
                    elif marker[0] == self.fence[0] and len(marker) >= len(self.fence) and not fence.group(2).strip():
                        self.fence = None
                        closed = True
                if self.fence is None and (not self.line.strip() or closed):
                    output.append(speech_text(self.pending))
                    self.pending = ''
                self.line = ''
            elif char in '。！？!?；;' and self.fence is None:
                # A formatted paragraph stays buffered even when punctuation is
                # inside a link, code span, emphasis, table or reference label.
                if not re.search(r'[*_`\[\]<>#|~\\]|^\s*(?:[-+]\s|\d+[.)]\s| {4})', self.pending):
                    output.append(speech_text(self.pending))
                    self.pending = ''
                    self.line = ''
        if final:
            output.append(speech_text(self.pending))
            self.pending = self.line = ''
            self.fence = None
        return [value for value in output if value]
