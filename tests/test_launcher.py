import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase
from unittest.mock import patch

from shaniu.launcher import main, running_service


class LauncherTests(TestCase):
    def setUp(self):
        self.payload = dict(status='ok', service='shaniu')
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(owner.payload).encode())
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_repeated_launch_reuses_service_without_constructing_app(self):
        with patch.dict('os.environ', SHANIU_HOST='127.0.0.1', SHANIU_PORT=str(self.port)), patch('sys.argv', ['run.py', '--open-browser']), patch('shaniu.server.create_app') as create, patch('shaniu.launcher.webbrowser.open') as browser:
            self.assertEqual(main(), 0)
            create.assert_not_called()
            browser.assert_called_once_with(f'http://127.0.0.1:{self.port}')

    def test_unrelated_service_is_not_opened_or_replaced(self):
        self.payload = dict(status='ok', service='another-app')
        with patch.dict('os.environ', SHANIU_HOST='127.0.0.1', SHANIU_PORT=str(self.port)), patch('sys.argv', ['run.py', '--open-browser']), patch('shaniu.server.create_app') as create, patch('shaniu.launcher.webbrowser.open') as browser:
            self.assertEqual(main(), 1)
            create.assert_not_called()
            browser.assert_not_called()

    def test_previous_health_format_is_recognized(self):
        self.payload = dict(status='ok', provider='compatible', avatar={}, tts={}, model='test')
        self.assertEqual(running_service('127.0.0.1', self.port), 'shaniu')

    def test_closed_port_is_free(self):
        self.server.shutdown()
        self.server.server_close()
        self.assertEqual(running_service('127.0.0.1', self.port), 'free')
