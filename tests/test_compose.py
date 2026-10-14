"""Build compositor: the backdrop/inset primitives (gradient, shadow, cropped rounded image)."""

import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pugrec.edit import compose as C  # noqa: E402


def canvas(w=200, h=100):
    return np.zeros((h, w, 3), np.uint8)


class Gradient(unittest.TestCase):
    def test_angle_convention_matches_css(self):
        t = C.gradient_t(100, 50, 90)       # 90 deg: left -> right
        self.assertLess(t[25, 0], 0.02)
        self.assertGreater(t[25, -1], 0.98)
        t = C.gradient_t(100, 50, 180)      # 180 deg: top -> bottom
        self.assertLess(t[0, 50], 0.02)
        self.assertGreater(t[-1, 50], 0.98)
        t = C.gradient_t(100, 100, 135)     # diagonal: the corners are the ends
        self.assertLess(t[0, 0], 0.02)
        self.assertGreater(t[-1, -1], 0.98)

    def test_colours_at_the_ends_and_between(self):
        c = canvas()
        C.paint(c, [{"type": "gradient", "dst": [0, 0, 200, 100], "colors": ["#ff0000", "#0000ff"], "angle": 90}], {})
        self.assertEqual(list(c[50, 0]), [1, 0, 254])      # BGR: red at the left (within a step)
        self.assertEqual(list(c[50, -1]), [254, 0, 1])     # blue at the right
        mid = c[50, 100]
        self.assertTrue(120 < mid[0] < 135 and 120 < mid[2] < 135)

    def test_three_stops_and_rounded_corners(self):
        c = canvas()
        C.paint(c, [{"type": "gradient", "dst": [0, 0, 200, 100], "colors": ["#000000", "#ffffff", "#000000"],
                     "angle": 90, "shape": "round", "radius": 30}], {})
        self.assertGreater(c[50, 100].min(), 240)          # the middle stop
        self.assertEqual(list(c[0, 0]), [0, 0, 0])         # outside the rounded corner: untouched


class Shadow(unittest.TestCase):
    def test_soft_dark_under_the_card_nothing_far_away(self):
        c = np.full((100, 200, 3), 200, np.uint8)
        C.paint(c, [{"type": "fill", "dst": [0, 0, 200, 100], "color": "#c8c8c8"},
                    {"type": "shadow", "dst": [50, 25, 100, 50], "radius": 10, "blur": 20, "opacity": 0.5,
                     "offset": 4}], {})
        self.assertLess(c[50, 100, 0], 120)                # under the card: darkened by ~half
        self.assertLess(c[25 + 50 + 4, 100, 0], 200)       # just below the card edge: soft falloff
        self.assertEqual(c[2, 2, 0], 200)                  # a corner, well away: untouched


class Image(unittest.TestCase):
    def test_crop_and_round(self):
        from PIL import Image as PILImage
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "split.png")
            im = PILImage.new("RGB", (100, 50), (255, 0, 0))
            im.paste((0, 255, 0), (50, 0, 100, 50))        # left half red, right half green
            im.save(path)
            c = canvas(100, 100)
            C.paint(c, [{"type": "image", "img": "a", "dst": [0, 0, 100, 100], "rect": [50, 0, 50, 50],
                         "shape": "round", "radius": 20}], {}, {"a": {"file": path}})
            self.assertEqual(list(c[50, 50]), [0, 255, 0])    # only the green half, scaled up
            self.assertEqual(list(c[0, 0]), [0, 0, 0])        # rounded corner


if __name__ == "__main__":
    unittest.main()
