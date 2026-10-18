import sys
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from moneyprinter_state import PersistentState, track_stage
from start_moneyprinter import existing_service


class ExternalStateTests(TestCase):
    def test_model_error_strings_stop_before_narration_or_material_search(self):
        with tempfile.TemporaryDirectory() as folder:
            state = PersistentState(Path(folder) / 'state.db')
            for stage in ['script', 'terms']:
                wrapped = track_stage(lambda task_id: 'Error: invalid credentials', stage, state)
                self.assertIsNone(wrapped(stage))
                self.assertEqual(state.get_task(stage)['state'], -1)
                self.assertEqual(state.get_task(stage)['failure_reason'], stage + '_failed')

    def test_stages_progress_and_restart_survive_without_original_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'state.db'
            state = PersistentState(path)
            state.update_task('failed', progress=10)
            def failure(task_id):
                state.update_task(task_id, state=-1)
                return None
            track_stage(failure, 'script', state)('failed')
            self.assertEqual(state.get_task('failed')['progress'], 10)
            self.assertEqual(state.get_task('failed')['failure_reason'], 'script_failed')
            state.update_task('pending', progress=40)
            state.update_task('complete', state=1, progress=100, videos=['final.mp4'])
            reopened = PersistentState(path)
            self.assertEqual(reopened.get_task('pending')['failure_reason'], 'service_restarted')
            self.assertEqual(reopened.get_task('complete')['videos'], ['final.mp4'])
            self.assertEqual(reopened.get_task('failed')['shaniu_stage'], 'script')

    def test_launcher_reuses_only_a_compatible_listener(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                return b'{"paths":{"/api/v1/videos":{},"/api/v1/audio":{},"/api/v1/subtitle":{},"/api/v1/tasks/{task_id}":{}}}'
        with patch('start_moneyprinter.socket.create_connection'), patch('start_moneyprinter.urlopen', return_value=Response()):
            self.assertTrue(existing_service(8080))
        with patch('start_moneyprinter.socket.create_connection'), patch('start_moneyprinter.urlopen', side_effect=ValueError):
            with self.assertRaises(RuntimeError):
                existing_service(8080)
        with patch('start_moneyprinter.socket.create_connection', side_effect=ConnectionRefusedError):
            self.assertFalse(existing_service(8080))
