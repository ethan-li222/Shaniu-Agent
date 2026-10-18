import unittest

from shaniu.text import speech_text, MarkdownSpeechBuffer
from shaniu.realtime import SentenceBuffer


class MarkdownTests(unittest.TestCase):
    SOURCE = '''# 学习计划

1. **学习** Python。
2. 阅读 [文档](https://example.com/a?x=1&y=2)。

> 使用 `print`，保持节奏。

| 项目 | 时间 |
| --- | --- |
| 复习 | 20分钟 |

```python
print("不要朗读代码。")

print("也不要读这一行")
```

最后，休息一下！'''

    def test_structure_links_tables_code_and_source_preservation(self):
        result = speech_text(self.SOURCE)
        self.assertIn('学习计划', result)
        self.assertIn('阅读 文档。', result)
        self.assertIn('使用 print，保持节奏。', result)
        self.assertIn('项目，时间\n复习，20分钟', result)
        self.assertEqual(result.count('代码内容请查看屏幕。'), 1)
        for marker in ['https:', '**', '```', '|', '不要朗读代码']:
            self.assertNotIn(marker, result)
        self.assertIn('**学习**', self.SOURCE)

    def test_every_stream_fragment_size_uses_clean_text(self):
        expected = ''.join(speech_text(self.SOURCE).split())
        for size in [1, 2, 7, 31, len(self.SOURCE)]:
            stream, sentences, result = MarkdownSpeechBuffer(), SentenceBuffer(), []
            for start in range(0, len(self.SOURCE), size):
                for plain in stream.feed(self.SOURCE[start:start+size]):
                    result.extend(sentences.feed(plain))
            for plain in stream.feed(final=True):
                result.extend(sentences.feed(plain))
            result.extend(sentences.feed('', final=True))
            self.assertEqual(''.join(''.join(result).split()), expected, size)

    def test_plain_text_still_streams_and_open_link_waits(self):
        buffer = MarkdownSpeechBuffer()
        self.assertEqual(buffer.feed('你好。'), ['你好。'])
        self.assertEqual(buffer.feed('查看 [文档](https://exa'), [])
        self.assertEqual(buffer.feed('mple.com/路径。'), [])
        self.assertEqual(buffer.feed(')\n\n'), ['查看 文档'])

    def test_entities_tasks_references_and_literal_symbols(self):
        self.assertEqual(speech_text('- [x] 完成 **学习**\n- [ ] 继续复习'), '完成 学习\n继续复习')
        self.assertEqual(speech_text('查看 [文档][docs]。\n\n[docs]: https://example.com'), '查看 文档。')
        self.assertEqual(speech_text('A &amp; B，2 * 3 = 6，a_b。'), 'A & B，2 * 3 = 6，a_b。')
        self.assertEqual(speech_text('---'), '')
