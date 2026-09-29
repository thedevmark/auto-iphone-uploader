import io
import unittest

from PIL import Image, ImageDraw

from video_drop.screens.icons import IconError, locate, to_points


def screen_with_circles(centers, size=(200, 120)):
    image = Image.new("L", size, 30)
    draw = ImageDraw.Draw(image)
    for cx, cy in centers:
        draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), outline=230, width=3)
    return image


class IconTests(unittest.TestCase):
    def setUp(self):
        self.icon = screen_with_circles([(12, 12)], size=(24, 24))

    def test_finds_icon_inside_band(self):
        screen = screen_with_circles([(40, 60)])
        self.assertEqual(locate(screen, self.icon, (0, 30, 120, 90)), (40.0, 60.0))

    def test_band_limits_the_search(self):
        screen = screen_with_circles([(40, 60), (160, 60)])
        self.assertEqual(locate(screen, self.icon, (120, 30, 200, 90)), (160.0, 60.0))

    def test_two_equal_matches_in_band_fail(self):
        screen = screen_with_circles([(40, 60), (100, 60)])
        with self.assertRaisesRegex(IconError, "ambiguous"):
            locate(screen, self.icon, (0, 30, 200, 90))

    def test_absent_icon_fails(self):
        with self.assertRaisesRegex(IconError, "not found"):
            locate(Image.new("L", (200, 120), 30), self.icon, (0, 0, 200, 120))

    def test_to_points_downscales_retina_screenshot(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1320, 2868), "white").save(buffer, "PNG")
        image = to_points(buffer.getvalue(), 440, 956)
        self.assertEqual((image.size, image.mode), ((440, 956), "L"))


if __name__ == "__main__":
    unittest.main()
