import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from video_drop import server


class StaticFileTests(unittest.TestCase):
    def setUp(self):
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)

    def test_brand_files_are_served_with_their_types(self):
        for path, (name, content_type) in server.STATIC_FILES.items():
            with self.subTest(path=path), urllib.request.urlopen(self.base + path, timeout=5) as response:
                self.assertEqual(response.headers["Content-Type"], content_type)
                self.assertEqual(response.read(), (server.ROOT / "web" / name).read_bytes())

    def test_only_listed_files_are_served(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + "/fonts/OFL.txt", timeout=5)
        self.assertEqual(caught.exception.code, 404)

    def test_page_loads_the_bundled_font_offline(self):
        page = (server.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("url(/fonts/inter-variable.woff2)", page)
        self.assertNotIn("fonts.googleapis.com", page)


if __name__ == "__main__":
    unittest.main()
