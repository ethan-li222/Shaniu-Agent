import importlib.util
from pathlib import Path
from unittest import TestCase

spec = importlib.util.spec_from_file_location('moneyprinter_compat', Path(__file__).resolve().parents[1] / 'scripts/moneyprinter_compat.py')
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


class BoundaryCompatTests(TestCase):
    def test_legacy_boundaries_preserve_tick_units_and_end_time(self):
        subtitles = compat.LegacySubMaker()
        subtitles.create_sub((10_000_000, 2_500_000), '你好')
        subtitles.create_sub((12_500_000, 5_000_000), '傻妞')
        self.assertEqual(subtitles.offset, [(10_000_000, 12_500_000), (12_500_000, 17_500_000)])
        self.assertEqual(subtitles.subs, ['你好', '傻妞'])
        self.assertEqual(compat.mktimestamp(subtitles.offset[-1][1]), '00:00:01.750')
        self.assertEqual(compat.mktimestamp(36_610_001_000), '01:01:01.000')
