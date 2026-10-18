import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import moneyprinter_speech as speech
from moneyprinter_compat import LegacySubMaker
from moneyprinter_state import PersistentState, track_stage


def test_online_failure_falls_back_atomically_and_reports_actual_provider():
    async def fail(*args):
        args[-1].write_bytes(b'partial')
        raise RuntimeError('403 sensitive URL')
    def local(text, rate, path):
        path.write_bytes(b'complete')
        return SimpleNamespace(shaniu_provider='windows', shaniu_alignment='word_boundary')
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder)/'audio.mp3'
        with patch.dict('os.environ', {'SHANIU_MPT_TTS_PROVIDER': 'edge', 'SHANIU_MPT_TTS_FALLBACK': 'true'}), patch.object(speech, 'online', fail), patch.object(speech, 'local', local):
            if sys.platform != 'win32':
                pytest.skip('Windows local fallback')
            maker = speech.synthesize('你好', 'zh-CN-XiaoxiaoNeural', 1, str(target))
        assert target.read_bytes() == b'complete'
        assert maker.shaniu_provider == 'windows'
        assert '本地' in maker.shaniu_warning
        assert not target.with_name('audio.pending.mp3').exists()


def test_disabled_fallback_removes_partial_audio_and_keeps_error_sanitized():
    async def fail(*args):
        args[-1].write_bytes(b'partial')
        raise RuntimeError('sensitive URL')
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder)/'audio.mp3'
        target.touch()
        with patch.dict('os.environ', {'SHANIU_MPT_TTS_PROVIDER': 'edge', 'SHANIU_MPT_TTS_FALLBACK': 'false'}), patch.object(speech, 'online', fail), patch.object(speech, 'local') as local:
            with pytest.raises(speech.SpeechUnavailable) as error:
                speech.synthesize('你好', 'zh-CN-XiaoxiaoNeural', 1, str(target))
        local.assert_not_called()
        assert 'sensitive' not in str(error.value)
        assert error.value.failure_reason == 'audio_online_failed'
        assert not target.exists()
        assert not target.with_name('audio.pending.mp3').exists()


def test_fallback_subtitles_use_actual_cues_and_state_keeps_warning():
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder)/'subtitle.srt'
        original = SimpleNamespace(create_subtitle=lambda **kwargs: pytest.fail('must use measured local cues'))
        speech.install(original)
        maker = LegacySubMaker()
        maker.shaniu_cues = [dict(start=.2, end=1.4, text='你好，世界。')]
        maker.shaniu_provider = 'windows'
        maker.shaniu_warning = 'local fallback'
        maker.shaniu_alignment = 'word_boundary'
        original.create_subtitle(maker, '你好，世界。', str(target))
        assert '00:00:00,200 --> 00:00:01,400' in target.read_text(encoding='utf-8')
        state = PersistentState(Path(folder)/'tasks.db')
        track_stage(lambda tid: ('audio.mp3', 2, maker), 'audio', state)('test')
        assert state.get_task('test')['speech_warning'] == 'local fallback'
        def fail(tid):
            raise speech.SpeechUnavailable('private')
        with pytest.raises(speech.SpeechUnavailable):
            track_stage(fail, 'audio', state)('failed')
        assert state.get_task('failed')['failure_reason'] == 'audio_fallback_failed'
