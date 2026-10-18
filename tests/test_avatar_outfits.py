import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from shaniu.avatar_catalog import SHANIU_AVATAR, avatar_asset_root, avatar_preview
from shaniu.config import Settings
from shaniu.server import create_app


def assets(root, name, preview):
    folder = root / name
    for kind in ('full_imgs', 'face_imgs'):
        (folder / kind).mkdir(parents=True)
        (folder / kind / '00000000.png').write_bytes(b'image')
    (folder / 'coords.pkl').write_bytes(b'coords')
    (folder / 'preview.jpg').write_bytes(preview)
    return folder


class OutfitAssetTests(TestCase):
    def test_shared_resolver_selects_emotion_and_falls_back_when_missing(self):
        with tempfile.TemporaryDirectory() as temp, patch('shaniu.avatar_catalog.ROOT', Path(temp)):
            base = assets(Path(temp) / 'data/avatar', SHANIU_AVATAR, b'silver')
            red = assets(base / 'emotions', 'gentle', b'red')
            self.assertEqual(avatar_asset_root(SHANIU_AVATAR, 'gentle'), red)
            self.assertEqual(avatar_preview(SHANIU_AVATAR, 'gentle').read_bytes(), b'red')
            self.assertEqual(avatar_asset_root(SHANIU_AVATAR, 'happy'), base)
            with self.assertRaises(ValueError):
                avatar_asset_root(SHANIU_AVATAR, '../../outside')


class OutfitPreviewTests(IsolatedAsyncioTestCase):
    async def test_preview_tracks_mode_without_changing_selected_character(self):
        with tempfile.TemporaryDirectory() as temp, patch('shaniu.avatar_catalog.ROOT', Path(temp)):
            base = assets(Path(temp) / 'data/avatar', SHANIU_AVATAR, b'silver')
            assets(base / 'emotions', 'gentle', b'red')
            async with TestClient(TestServer(create_app(Settings(database=Path(temp)/'app.db'))), cookie_jar=CookieJar(unsafe=True)) as client:
                prefix = f'/api/avatars/{SHANIU_AVATAR}/preview'
                self.assertEqual(await (await client.get(prefix+'?emotion=cool')).read(), b'silver')
                self.assertEqual(await (await client.get(prefix+'?emotion=gentle')).read(), b'red')
                self.assertEqual((await client.get(prefix+'?emotion=unknown')).status, 404)
