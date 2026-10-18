from array import array
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prosody import natural_pauses, punctuation_boundaries, pause_seconds, profile
from speech import SpeechResult, trim_realtime_padding


class ProsodyTests(unittest.TestCase):
    def test_realtime_keeps_short_edge_padding_and_shifts_captions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'voice.wav'
            self.write_wave(path, [0]*8000+[1000]*8000+[0]*8000)
            result = SpeechResult(path, [dict(start=.5, end=1.5, text='你好。')], 'edge', 'test', 'word_boundary')
            trim_realtime_padding(result, threading.Event())
            with wave.open(str(path), 'rb') as audio:
                samples = array('h', audio.readframes(audio.getnframes()))
            self.assertEqual(len(samples), 480+8000+1920)
            self.assertEqual(list(samples[480:8480]), [1000]*8000)
            self.assertAlmostEqual(result.cues[0]['start'], .03)
            self.assertAlmostEqual(result.cues[0]['end'], .65)

    def test_realtime_does_not_append_an_extra_terminal_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'voice.wav'
            self.write_wave(path, [1000]*6400+[0]*1600)
            before = path.read_bytes()
            natural_pauses(path, '你好。', [dict(text='你好', start=0, end=.4)], 'cool', threading.Event(), include_terminal=False)
            self.assertEqual(path.read_bytes(), before)

    def write_wave(self, path, samples):
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(array('h', samples).tobytes())

    def test_pause_insertion_preserves_samples_and_shifts_word_timestamps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'voice.wav'
            original = [1000]*6400 + [0]*1600 + [1000]*6400 + [0]*1600
            self.write_wave(path, original)
            events = [dict(text='你好', start=0, end=.4), dict(text='再见', start=.5, end=.9)]
            result = natural_pauses(path, '你好。再见。', events, 'cool', threading.Event())
            # Each existing 100ms pause extends to 260ms, without removing samples.
            with wave.open(str(path), 'rb') as audio:
                samples = array('h', audio.readframes(audio.getnframes()))
            self.assertEqual(len(samples), len(original)+5120)
            self.assertEqual(list(samples[:6400]), original[:6400])
            self.assertAlmostEqual(result[1]['start'], .66)
            self.assertAlmostEqual(result[1]['end'], 1.06)
            self.assertEqual(result[0]['end'], .4)

    def test_existing_long_pauses_and_audible_splices_are_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'voice.wav'
            events = [dict(text='你好', start=0, end=.4), dict(text='再见', start=.8, end=1.2)]
            for original in [[1000]*6400+[0]*6400+[1000]*6400+[0]*6400, [1000]*25600]:
                self.write_wave(path, original)
                before = path.read_bytes()
                natural_pauses(path, '你好。再见。', events, 'cool', threading.Event())
                self.assertEqual(path.read_bytes(), before)

    def test_incomplete_alignment_cannot_invent_pauses(self):
        self.assertEqual(punctuation_boundaries('完整正文。', [dict(text='其他', start=0, end=1)]), [])
        self.assertEqual(punctuation_boundaries('你好。', [dict(text='你好', start=float('nan'), end=1)]), [])
        events = [dict(text='3', start=0, end=.1), dict(text='14', start=.2, end=.3)]
        self.assertEqual(punctuation_boundaries('3:14', events)[0]['punctuation'], '')

    def test_emotions_keep_fast_rates_and_different_pause_lengths(self):
        self.assertEqual(profile('happy')['rate'], '+24%')
        self.assertGreater(pause_seconds('……', 'shy'), pause_seconds('，', 'happy'))
        self.assertEqual(pause_seconds('', 'cool'), 0)
