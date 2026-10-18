import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from speech import captions, timestamp, write_subtitles, synthesize, SpeechCancelled, windows_boundaries


class CaptionTests(unittest.TestCase):
    def test_windows_date_progress_repeats_source_span_without_repeating_text(self):
        events = windows_boundaries([
            dict(position=0, count=5, text='2026年', start=1),
            dict(position=5, count=3, text='10月', start=3),
            dict(position=0, count=5, text='2026年', start=2),
        ], 4)
        self.assertEqual(len(events), 2)
        cues, alignment = captions('2026年10月', events, 4)
        self.assertEqual(alignment, 'word_boundary')
        self.assertEqual(cues, [dict(start=1, end=4, text='2026年10月')])

    def test_word_boundaries_preserve_punctuation_and_real_gaps(self):
        cues, alignment = captions('你好！我是傻妞。', [
            dict(text='你好', start=.1, end=.8),
            dict(text='我', start=1.4, end=1.7),
            dict(text='是', start=1.7, end=2),
            dict(text='傻妞', start=2, end=2.6),
        ], 3)
        self.assertEqual(alignment, 'word_boundary')
        self.assertEqual(cues, [dict(start=.1, end=.8, text='你好！'), dict(start=1.4, end=2.6, text='我是傻妞。')])
        self.assertEqual(''.join(c['text'] for c in cues), '你好！我是傻妞。')

    def test_mismatch_discloses_sentence_fallback(self):
        cues, alignment = captions('完整正文。', [dict(text='别的内容', start=0, end=1)], 2)
        self.assertEqual(alignment, 'sentence_fallback')
        self.assertEqual(cues, [dict(start=0, end=2, text='完整正文。')])

    def test_entities_mixed_language_and_time_clamping(self):
        cues, alignment = captions('AI & 傻妞！', [dict(text='AI', start=-.1, end=.5), dict(text='傻妞', start=.5, end=5)], 1)
        self.assertEqual(alignment, 'word_boundary')
        self.assertEqual(cues, [dict(start=0, end=1, text='AI & 傻妞！')])

    def test_srt_and_vtt_encode_multilingual_timestamps(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'clip.mp4'
            write_subtitles(path, [dict(start=59.9996, end=61.001, text='你好，AI。')])
            self.assertIn('00:01:00,000 --> 00:01:01,001', path.with_suffix('.srt').read_text(encoding='utf-8'))
            self.assertTrue(path.with_suffix('.vtt').read_text(encoding='utf-8').startswith('WEBVTT\n\n00:01:00.000'))
        self.assertEqual(timestamp(3600.125), '01:00:00,125')

    def test_edge_failure_reports_actual_fallback_and_cancellation(self):
        async def failing(*args):
            raise RuntimeError('synthetic network failure')
        with patch('speech.edge', failing), patch('speech.windows', return_value=([dict(text='你好', start=0, end=1)], 'test-local')), patch('speech.duration', return_value=1):
            result = synthesize('你好', Path('unused.wav'), config=dict(provider='edge'))
            self.assertEqual(result.provider, 'windows')
            self.assertTrue(result.warning)
            self.assertEqual(result.alignment, 'word_boundary')
            with self.assertRaises(RuntimeError):
                synthesize('你好', Path('unused.wav'), config=dict(provider='edge', fallback=False))
        stop = threading.Event()
        stop.set()
        with self.assertRaises(SpeechCancelled):
            synthesize('你好', Path('unused.wav'), stop=stop)
