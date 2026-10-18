"""Check the local service before constructing the app or touching its database."""
import argparse
import json
import os
import socket
import threading
import urllib.error
import urllib.request
import webbrowser

from dotenv import load_dotenv

from .config import ROOT


def running_service(host, port):
    target = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    try:
        with socket.create_connection((target, port), timeout=1):
            pass
    except OSError:
        # Windows may time out instead of immediately refusing a closed port.
        # Verify that binding is actually possible before declaring it free.
        family = socket.AF_INET6 if ':' in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((host, port))
            return 'free'
        except OSError:
            return 'unavailable'
    address = f'[{target}]' if ':' in target else target
    # Never send a local probe through proxy environment variables.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://{address}:{port}/api/health', timeout=2) as response:
            data = json.loads(response.read(16384))
        if isinstance(data, dict) and data.get('status') == 'ok':
            if data.get('service') == 'shaniu':
                return 'shaniu'
            # Support the service that was already running before this fix.
            if data.get('provider') in {'demo', 'compatible'} and isinstance(data.get('avatar'), dict) and isinstance(data.get('tts'), dict) and isinstance(data.get('model'), str):
                return 'shaniu'
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return 'occupied'


def main():
    parser = argparse.ArgumentParser(description='启动傻妞；已运行时复用已有服务')
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    host = os.getenv('SHANIU_HOST', '127.0.0.1')
    try:
        port = int(os.getenv('SHANIU_PORT', '8011'))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        print('SHANIU_PORT 必须是 1–65535 的整数。')
        return 1
    browser_host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    address = f'[{browser_host}]' if ':' in browser_host else browser_host
    url = f'http://{address}:{port}'
    state = running_service(host, port)
    if state == 'shaniu':
        print(f'傻妞已经在运行，直接使用：{url}')
        if args.open_browser:
            webbrowser.open(url)
        return 0
    if state != 'free':
        print(f'无法启动：{host}:{port} 已被其他程序占用或不可访问。')
        print('请关闭占用该端口的程序，或在 .env 修改 SHANIU_PORT 后重试。')
        return 1

    from aiohttp import web
    from .server import create_app
    app = create_app()
    if args.open_browser:
        async def open_when_ready(app):
            # aiohttp invokes startup before binding; wait for the health endpoint.
            def wait_and_open():
                import time
                for _ in range(50):
                    if running_service(host, port) == 'shaniu':
                        webbrowser.open(url)
                        return
                    time.sleep(.1)
            threading.Thread(target=wait_and_open, daemon=True).start()
        app.on_startup.append(open_when_ready)
    try:
        web.run_app(app, host=host, port=port)
    except OSError as error:
        if error.errno in {98, 10048} or getattr(error, 'winerror', None) == 10048:
            print(f'启动时端口 {port} 被占用，请再次运行启动脚本检查已有服务。')
            return 1
        raise
    return 0
