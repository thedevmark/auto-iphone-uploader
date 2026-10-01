import io
import unittest
from unittest.mock import patch

from PIL import Image

from scripts import phone_youtube
from video_drop import ocr
from video_drop.phone_ui import PhoneLayout


def png(width=1320, height=2868):
    out = io.BytesIO()
    Image.new("RGB", (width, height)).save(out, format="PNG")
    return out.getvalue()


def node(kind, name, x, y, value=""):
    return {"type": "XCUIElementType" + kind, "name": name, "label": name, "value": value,
            "rect": {"x": x - 20, "y": y - 20, "width": 40, "height": 40}, "isVisible": "1"}


# Lines read by Windows OCR from the 2026-09-30 reference release Add details screenshot (3x pixels).
DETAILS = "\n".join([
    '{"text":"Add details","x":560,"y":180,"w":200,"h":50}',
    '{"text":"Description","x":180,"y":1580,"w":145,"h":38}',
    '{"text":"Add description","x":200,"y":1612,"w":272,"h":46}',
    '{"text":"Paid promotion & brands","x":250,"y":2340,"w":376,"h":42}',
    'noise that is not json',
])


class OcrTests(unittest.TestCase):
    def test_parse_skips_noise_and_reads_pixels(self):
        lines = ocr.parse(DETAILS)
        self.assertEqual(len(lines), 4)
        self.assertEqual(ocr.find(lines, "add description").center_points(3.0), (112.0, 545.0))

    def test_find_normalises_curly_apostrophes_and_spaces(self):
        lines = ocr.parse('{"text":"No,  it doesn’t","x":0,"y":0,"w":10,"h":10}')
        self.assertEqual(ocr.find(lines, "No, it doesn't").text, "No,  it doesn’t")

    def test_find_refuses_missing_or_ambiguous_labels(self):
        lines = ocr.parse(DETAILS + '\n{"text":"Description","x":0,"y":0,"w":9,"h":9}')
        with self.assertRaisesRegex(ocr.OcrError, "found 2"):
            ocr.find(lines, "Description")
        with self.assertRaisesRegex(ocr.OcrError, "found 0"):
            ocr.find(lines, "AI use, Tags")

    def test_prefix_match_is_opt_in(self):
        lines = ocr.parse('{"text":"AI use, Tags","x":0,"y":0,"w":10,"h":10}')
        with self.assertRaises(ocr.OcrError):
            ocr.find(lines, "AI use")
        self.assertEqual(ocr.find(lines, "AI use", exact=False).text, "AI use, Tags")


class FakePhone:
    def __init__(self):
        self.taps = []
        self.typed = []
        self.opened = False

    def tap(self, x, y):
        self.taps.append((x, y))
        if (x, y) == (112.0, 545.0):
            self.opened = True

    def type_text(self, text):
        self.typed.append(text)

    def ui_tree(self):
        value = "\n".join(self.typed) if len(self.typed) > 1 else "".join(self.typed)
        return {"type": "XCUIElementTypeApplication", "children": [
            node("TextView", "", 220, 300, value), node("Keyboard", "", 220, 800),
            node("Button", "Return", 400, 880)]}


@patch("scripts.phone_youtube.time.sleep", lambda seconds: None)
class HiddenRowTests(unittest.TestCase):
    def setUp(self):
        self.phone = FakePhone()
        patches = [patch.object(phone_youtube, "phone", self.phone),
                   patch.object(phone_youtube, "layout", lambda **kw: PhoneLayout(440, 956)),
                   patch.object(phone_youtube, "screen_pixels", lambda: png()),
                   patch.object(phone_youtube, "matches", lambda label, exact=True: [])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_hidden_row_is_tapped_where_ocr_reads_it_and_proven_by_the_next_screen(self):
        with patch.object(ocr, "read_png", lambda data: ocr.parse(DETAILS)):
            phone_youtube.open_row("Add description", opened=lambda: self.phone.opened)
        self.assertEqual(self.phone.taps, [(112.0, 545.0)])

    def test_a_tap_that_opens_nothing_stops_the_run(self):
        clock = iter(range(0, 100, 5))
        with patch.object(ocr, "read_png", lambda data: ocr.parse(DETAILS)), \
                patch("scripts.phone_youtube.time.monotonic", lambda: next(clock)):
            with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "did not open"):
                phone_youtube.open_row("Paid promotion & brands", opened=lambda: False)

    def test_a_row_ocr_cannot_find_is_never_guessed(self):
        with patch.object(ocr, "read_png", lambda data: ocr.parse(DETAILS)):
            with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "not found"):
                phone_youtube.open_row("AI use, Tags", exact=False, opened=lambda: True)
        self.assertEqual(self.phone.taps, [])

    def test_description_lines_are_split_by_the_return_key_and_read_back(self):
        phone_youtube.type_description("#podcast #radio\nA guest of the show")
        self.assertEqual(self.phone.typed, ["#podcast #radio", "A guest of the show"])
        self.assertEqual(self.phone.taps, [(400, 880)])

    def test_description_that_did_not_land_stops_the_run(self):
        self.phone.type_text = lambda text: None
        with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "does not match"):
            phone_youtube.type_description("#radio")


if __name__ == "__main__":
    unittest.main()
