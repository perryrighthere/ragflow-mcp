import unittest

from ragflow_service.exceptions import ValidationError
from ragflow_service.http_server import read_static_asset, resolve_static_path


class HttpServerStaticTests(unittest.TestCase):
    def test_index_page_asset_is_available(self):
        payload, content_type = read_static_asset("index.html")

        self.assertIn("text/html", content_type)
        self.assertIn("RAGFlow Metadata Console", payload.decode("utf-8"))

    def test_javascript_asset_is_available(self):
        payload, content_type = read_static_asset("app.js")

        self.assertIn("application/javascript", content_type)
        self.assertIn("handleUpload", payload.decode("utf-8"))

    def test_static_path_rejects_traversal(self):
        with self.assertRaises(ValidationError):
            resolve_static_path("../README.md")


if __name__ == "__main__":
    unittest.main()

