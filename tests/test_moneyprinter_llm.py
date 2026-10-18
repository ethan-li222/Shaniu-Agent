import sys
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from moneyprinter_llm import LLMConfig


class MoneyPrinterLLMTests(TestCase):
    def test_default_xunfei_reuses_key_without_exposing_it(self):
        settings = LLMConfig.from_env({'SHANIU_API_KEY': 'secret-value'})
        original = dict(llm_provider='gemini', gemini_api_key='old-value')
        settings.apply(original)
        self.assertEqual(original['llm_provider'], 'openai')
        self.assertEqual(original['openai_base_url'], 'https://maas-api.cn-huabei-1.xf-yun.com/v2')
        self.assertEqual(original['openai_model_name'], 'xopglm53')
        self.assertEqual(original['openai_api_key'], 'secret-value')
        self.assertEqual(original['gemini_api_key'], 'old-value')
        self.assertNotIn('secret-value', repr(settings))
        self.assertNotIn('api_key', settings.public())

    def test_independent_endpoint_model_and_key(self):
        settings = LLMConfig.from_env(dict(
            SHANIU_MPT_LLM_API_BASE='https://example.com/v1/chat/completions/',
            SHANIU_MPT_LLM_MODEL='different-model', SHANIU_MPT_LLM_API_KEY='different-key',
            SHANIU_API_KEY='conversation-key'))
        self.assertEqual(settings.base_url, 'https://example.com/v1')
        self.assertEqual(settings.model, 'different-model')
        self.assertEqual(settings.api_key, 'different-key')
        local = LLMConfig.from_env(dict(SHANIU_MPT_LLM_API_BASE='http://127.0.0.1:11434/v1',
                                       SHANIU_MPT_LLM_API_KEY='local'))
        self.assertIn('11434', local.base_url)

    def test_explicit_upstream_mode_does_not_change_original_config(self):
        original = dict(llm_provider='gemini', gemini_model_name='original-model')
        before = original.copy()
        settings = LLMConfig.from_env({'SHANIU_MPT_LLM_PROVIDER': 'upstream'})
        settings.apply(original)
        self.assertEqual(original, before)
        self.assertEqual(settings.public(), dict(provider='upstream'))

    def test_invalid_configuration_fails_without_showing_credentials(self):
        for base in ['http://remote.example/v1', 'https://user:password@example.com',
                     'https://example.com?api_key=secret', 'file:///tmp', 'https:///v1']:
            with self.assertRaises(ValueError) as error:
                LLMConfig.from_env(dict(SHANIU_MPT_LLM_API_BASE=base, SHANIU_API_KEY='secret'))
            self.assertNotIn('secret', str(error.exception))
        with self.assertRaises(ValueError):
            LLMConfig.from_env({})
        with self.assertRaises(ValueError):
            LLMConfig.from_env({'SHANIU_MPT_LLM_PROVIDER': 'unsupported'})
