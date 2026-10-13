import unittest

from pugrec import share


class PlanTest(unittest.TestCase):
    def test_short_video_keeps_1080p(self):
        self.assertEqual(share.plan(60, 1920, 1080, True)[0], None)

    def test_steps_down_by_the_short_side(self):
        self.assertEqual(share.plan(90, 1080, 1920, True)[0], 720)
        self.assertEqual(share.plan(180, 1920, 1080, True)[0], 540)

    def test_fits_the_limit(self):
        for d in (20, 70, 150, 400):
            _, v = share.plan(d, 1920, 1080, True)
            self.assertLess((v + share.AUDIO_BPS) * d / 8, share.LIMIT_BYTES)

    def test_too_long_refused(self):
        with self.assertRaises(share.ShareError):
            share.plan(600, 1920, 1080, True)


if __name__ == "__main__":
    unittest.main()
