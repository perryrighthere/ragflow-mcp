import io
import json
import unittest
from urllib import error
from unittest.mock import patch

from ragflow_service.exceptions import RagflowAPIError
from ragflow_service.ragflow_client import RagflowClient


class FakeHTTPResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class RagflowClientTests(unittest.TestCase):
    def test_request_json_logs_full_raw_command_details(self):
        client = RagflowClient("http://ragflow.local:9380", "secret-key")

        with patch(
            "ragflow_service.ragflow_client.request.urlopen",
            return_value=FakeHTTPResponse(200, b'{"code":0}'),
        ):
            with self.assertLogs("ragflow_service", level="INFO") as captured:
                client.request_json("POST", "/api/v1/retrieval", json_body={"question": "五看六定"})

        output = "\n".join(captured.output)
        self.assertIn('RAGFlow request headers -> {"Accept": "application/json", "Content-Type": "application/json", "Authorization": "Bearer secret-key"}', output)
        self.assertIn('RAGFlow request raw body -> {"question": "五看六定"}', output)
        self.assertIn("RAGFlow request curl ->", output)
        self.assertIn("--request POST", output)
        self.assertIn("--url http://ragflow.local:9380/api/v1/retrieval", output)
        self.assertIn("--header 'Authorization: Bearer secret-key'", output)
        self.assertIn("--data-raw '{\"question\": \"五看六定\"}'", output)

    def test_request_json_returns_raw_success_payload(self):
        client = RagflowClient("http://ragflow.local:9380", "secret-key", timeout=30.0)

        with patch(
            "ragflow_service.ragflow_client.request.urlopen",
            return_value=FakeHTTPResponse(200, b'{"code":0,"data":{"status":"ok"}}'),
        ) as mocked:
            response = client.request_json("GET", "/v1/system/healthz", use_auth=False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload, {"code": 0, "data": {"status": "ok"}})
        self.assertEqual(mocked.call_args.kwargs["timeout"], 30.0)

    def test_request_json_returns_raw_http_error_payload(self):
        client = RagflowClient("http://ragflow.local:9380", "secret-key")
        http_error = error.HTTPError(
            url="http://ragflow.local:9380/api/v1/retrieval",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"code":102,"message":"bad request"}'),
        )

        with patch("ragflow_service.ragflow_client.request.urlopen", side_effect=http_error):
            response = client.request_json("POST", "/api/v1/retrieval", json_body={"question": "hi"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.payload, {"code": 102, "message": "bad request"})
        self.assertEqual(response.reason_phrase, "Bad Request")
        self.assertEqual(response.raw_text, '{"code":102,"message":"bad request"}')

    def test_request_json_returns_raw_http_error_text_for_gateway_failures(self):
        client = RagflowClient("http://ragflow.local:9380", "secret-key")
        http_error = error.HTTPError(
            url="http://ragflow.local:9380/api/v1/retrieval",
            code=502,
            msg="Bad Gateway",
            hdrs={"Content-Type": "text/plain"},
            fp=io.BytesIO(b"upstream connect error or disconnect/reset before headers"),
        )

        with patch("ragflow_service.ragflow_client.request.urlopen", side_effect=http_error):
            response = client.request_json("POST", "/api/v1/retrieval", json_body={"question": "hi"})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.reason_phrase, "Bad Gateway")
        self.assertEqual(response.raw_text, "upstream connect error or disconnect/reset before headers")
        self.assertEqual(response.payload, "upstream connect error or disconnect/reset before headers")
        self.assertEqual(response.headers, {"Content-Type": "text/plain"})

    def test_request_json_raises_for_connection_errors(self):
        client = RagflowClient("http://ragflow.local:9380", "secret-key")

        with patch(
            "ragflow_service.ragflow_client.request.urlopen",
            side_effect=error.URLError("connection refused"),
        ):
            with self.assertRaises(RagflowAPIError):
                client.request_json("GET", "/v1/system/healthz", use_auth=False)


if __name__ == "__main__":
    unittest.main()
