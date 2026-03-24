import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main
from ragflow_service.ragflow_client import UpstreamResponse


class MainCliTests(unittest.TestCase):
    def test_request_command_prints_raw_response(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.calls = []

            def request_json(self, method, path, **kwargs):
                self.calls.append((method, path, kwargs))
                return UpstreamResponse(status_code=200, payload={"code": 0, "data": {"ok": True}})

        stdout = io.StringIO()
        with patch("main.RagflowClient", return_value=FakeClient()) as mocked_client:
            with redirect_stdout(stdout):
                exit_code = main.main(
                    [
                        "request",
                        "POST",
                        "/api/v1/retrieval",
                        "--json",
                        json.dumps({"question": "hello"}),
                        "--base-url",
                        "http://ragflow.local:9380",
                        "--api-key",
                        "secret-key",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("HTTP 200", stdout.getvalue())
        self.assertIn('"ok": true', stdout.getvalue())
        self.assertEqual(mocked_client.return_value.calls[0][0], "POST")
        self.assertEqual(mocked_client.return_value.calls[0][1], "/api/v1/retrieval")


if __name__ == "__main__":
    unittest.main()
