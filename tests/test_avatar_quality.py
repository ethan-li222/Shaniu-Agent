"""Numerical guarantees for the identity-preserving mouth compositor."""
import sys
from pathlib import Path
from unittest import TestCase, skipUnless
import importlib.util

AVAILABLE = all(importlib.util.find_spec(name) for name in ('cv2', 'numpy'))


@skipUnless(AVAILABLE, 'Requires optional media dependencies')
class MouthBlendTests(TestCase):
    def test_preserves_eyes_contour_and_source_cache(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
        import numpy as np
        from avatar_quality import blend_mouth, mouth_mask
        original = np.full((256, 192, 3), 100, dtype=np.uint8)
        predicted = np.full((256, 256, 3), 160, dtype=np.uint8)
        saved = original.copy()
        result = blend_mouth(original, predicted)
        mask = mouth_mask(256, 192)[..., 0]
        self.assertTrue(np.array_equal(result[mask == 0], original[mask == 0]))
        self.assertTrue(np.array_equal(original, saved))
        self.assertGreater(int(result[194,96,0]), 100)
        self.assertEqual(result.dtype, np.uint8)

    def test_identical_prediction_keeps_image_and_feather_is_bounded(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
        import numpy as np
        from avatar_quality import blend_mouth, mouth_mask
        original = np.random.default_rng(42).integers(0,256,(256,256,3),dtype=np.uint8)
        self.assertTrue(np.array_equal(blend_mouth(original, original), original))
        mask = mouth_mask(256,256)
        self.assertTrue(np.all((mask >= 0) & (mask <= 1)))
        self.assertTrue(np.any((mask > 0) & (mask < 1)))
