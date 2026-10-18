import sqlite3
from contextlib import closing
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from shaniu.avatar_catalog import AVATARS, DEFAULT_AVATAR, avatar_root
from shaniu.config import Settings
from shaniu.server import create_app
from shaniu.storage import Store


class AvatarMigrationTests(TestCase):
    def test_old_database_keeps_conversations_and_adds_default_avatar(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'old.db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, emotion TEXT NOT NULL, created_at TEXT NOT NULL)')
                db.execute("INSERT INTO conversations VALUES ('cid', 'owner', '原会话', 'cool', 'date')")
                db.commit()
            store = Store(path)
            self.assertEqual(store.conversation('cid', 'owner')['avatar_id'], DEFAULT_AVATAR)
            store.avatar('cid', 'wav2lip_avatar_glass_man')
            self.assertEqual(Store(path).conversation('cid', 'owner')['avatar_id'], 'wav2lip_avatar_glass_man')

    def test_catalog_rejects_paths_and_unknown_assets(self):
        for value in ['../outside', 'unknown', '/absolute']:
            with self.assertRaises(ValueError):
                avatar_root(value)


class AvatarApiTests(IsolatedAsyncioTestCase):
    async def test_selection_persists_and_is_owner_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(database=Path(temp) / 'app.db')
            async with TestClient(TestServer(create_app(settings)), cookie_jar=CookieJar(unsafe=True)) as client:
                headers = {'X-Shaniu-Client': 'web'}
                await client.get('/api/health')
                cid = (await (await client.post('/api/conversations', json={}, headers=headers)).json())['id']
                url = f'/api/conversations/{cid}/avatar'
                with patch('shaniu.server.avatar_ready', return_value=True):
                    catalog = await (await client.get('/api/avatars')).json()
                    self.assertEqual({item['id'] for item in catalog}, {item['id'] for item in AVATARS})
                    response = await client.patch(url, json={'avatar_id': 'wav2lip_avatar_glass_man'}, headers=headers)
                    self.assertEqual(response.status, 200)
                saved = await (await client.get(f'/api/conversations/{cid}/messages')).json()
                self.assertEqual(saved['conversation']['avatar_id'], 'wav2lip_avatar_glass_man')
                with patch('shaniu.server.avatar_ready', return_value=False):
                    response = await client.patch(url, json={'avatar_id': DEFAULT_AVATAR}, headers=headers)
                    self.assertEqual(response.status, 400)
                async with TestClient(TestServer(create_app(settings)), cookie_jar=CookieJar(unsafe=True)) as stranger:
                    self.assertEqual((await stranger.patch(url, json={'avatar_id': DEFAULT_AVATAR}, headers=headers)).status, 404)
                self.assertEqual((await client.patch(url, json={'avatar_id': '../outside'}, headers=headers)).status, 400)
                self.assertEqual((await client.get('/api/avatars/unknown/preview')).status, 404)
