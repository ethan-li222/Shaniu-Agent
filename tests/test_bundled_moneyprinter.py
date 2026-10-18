"""Distribution boundaries: a self-contained engine must not publish private data."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class BundledEngineTests(TestCase):
    def test_receipts_cover_code_and_keep_upstream_license(self):
        engine = ROOT / 'vendor/moneyprinter'
        receipt = json.loads((engine / 'SOURCE.json').read_text(encoding='utf-8'))
        self.assertIn('Copyright (c) 2024 Harry', (engine / 'LICENSE').read_text(encoding='utf-8'))
        listed = {r['path']: r for r in receipt['files']}
        self.assertEqual({p.relative_to(engine).as_posix() for p in (engine / 'app').rglob('*.py')},
                         {p for p in listed if p.startswith('app/')})
        for name, record in listed.items():
            self.assertEqual(hashlib.sha256((engine / name).read_bytes()).hexdigest(),
                             record.get('distributed_sha256', record['upstream_sha256']))
        self.assertFalse((engine / 'config.toml').exists())

    def test_default_uses_included_engine_but_explicit_paths_are_supported(self):
        paths = module('project_paths')
        with patch.dict(os.environ, {'SHANIU_MPT_PROJECT': ''}):
            self.assertEqual(paths.moneyprinter_project(), ROOT / 'vendor/moneyprinter')
        with patch.dict(os.environ, {'SHANIU_MPT_PROJECT': '../external-video-engine'}):
            self.assertEqual(paths.moneyprinter_project(), ROOT.parent / 'external-video-engine')

    def test_private_template_creation_preserves_settings_and_does_not_write_vendor(self):
        runtime = module('moneyprinter_runtime')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundled = root / 'vendor/moneyprinter'
            bundled.mkdir(parents=True)
            (bundled / 'config.example.toml').write_text('[app]\npexels_api_keys = []\n', encoding='utf-8')
            with patch.object(runtime, 'ROOT', root), patch.object(runtime, 'BUNDLED', bundled), patch.dict(os.environ, {'SHANIU_MPT_CONFIG': ''}):
                config = runtime.configure_project(bundled)
                self.assertEqual(config, root / 'data/moneyprinter/config.toml')
                config.write_text('user settings', encoding='utf-8')
                self.assertEqual(runtime.configure_project(bundled).read_text(), 'user settings')
                self.assertFalse((bundled / 'config.toml').exists())
                with patch.dict(os.environ, {'SHANIU_MPT_CONFIG': 'vendor/private.toml'}):
                    with self.assertRaises(ValueError):
                        runtime.configure_project(bundled)
                external = root / 'external'
                self.assertEqual(runtime.configure_project(external), external / 'config.toml')

    def test_explicit_installed_font_is_validated_and_no_font_is_copied(self):
        runtime = module('moneyprinter_runtime')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            font = root / 'authorized.ttf'
            font.write_bytes(b'test fixture')
            with patch.object(runtime, 'ROOT', root), patch.dict(os.environ, {'SHANIU_MPT_FONT_FILE': 'authorized.ttf'}):
                self.assertEqual(runtime.subtitle_font(None, root / 'empty'), str(font))
            with patch.dict(os.environ, {'SHANIU_MPT_FONT_FILE': str(root / 'missing.ttf')}):
                with self.assertRaises(ValueError):
                    runtime.subtitle_font(None, root / 'empty')
