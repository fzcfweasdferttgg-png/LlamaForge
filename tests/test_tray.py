import conftest_paths  # noqa: F401
import unittest

import tray

ORANGE, BLUE, GREEN, AMBER = (0xe8, 0x66, 0x1c), (0x2c, 0x6f, 0xb7), (0x26, 0x75, 0x4f), (0xff, 0xc2, 0x1a)


@unittest.skipUnless(tray._PIL, "Pillow not installed")
class MarkTest(unittest.TestCase):
    """The tray and the app icons draw the Stowage mark: an amber hold with
    orange + blue stowed on top, green bottom-left and one empty bay."""

    def px(self, img, x, y):                     # x, y in the mark's 32-unit grid
        k = img.size[0] / 32
        return img.getpixel((int(x * k), int(y * k)))[:3]

    def assertColour(self, got, want):           # downscaling rings by a few levels
        self.assertTrue(all(abs(a - b) <= 8 for a, b in zip(got, want)), (got, want))

    def test_mark_colours_sit_in_their_bays(self):
        img = tray.mark_image(64)
        self.assertEqual(img.size, (64, 64))
        self.assertColour(self.px(img, 2.5, 16), AMBER)       # the hold's frame
        self.assertColour(self.px(img, 10, 10), ORANGE)
        self.assertColour(self.px(img, 22, 10), BLUE)
        self.assertColour(self.px(img, 9, 21), GREEN)
        self.assertNotEqual(self.px(img, 20.5, 21.5)[0], 255)  # the empty bay

    def test_loaded_stows_a_container_in_the_empty_bay(self):
        self.assertColour(self.px(tray.mark_image(64, loaded=True), 20.5, 21.5), AMBER)

    def test_rounded_tile_has_transparent_corners(self):
        img = tray.mark_image(256, rounded=True)
        self.assertEqual(img.getpixel((0, 0))[3], 0)
        self.assertColour(self.px(img, 10, 10), ORANGE)

    def test_tray_icon_is_the_mark(self):
        self.assertColour(self.px(tray._icon_image(False), 10, 10), ORANGE)


if __name__ == "__main__":
    unittest.main()
