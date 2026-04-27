import io
import json
import unittest
from urllib import error
from unittest.mock import patch

from ragflow_service.exceptions import RagInfoSyncError
from ragflow_service.rag_info_sync_client import RagInfoSyncClient


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


class RagInfoSyncClientTests(unittest.TestCase):
    def test_sync_rag_info_posts_json_payload_and_redacts_tenant_logs(self):
        client = RagInfoSyncClient("http://sync.local/syncRagInfo", timeout=30.0)
        payload = {
            "knowledgeDatabaseId": "kb_123",
            "ragFileId": "rf-doc-1",
            "originFileId": "doc-1",
            "tenantId": "tenant-key",
        }

        with patch(
            "ragflow_service.rag_info_sync_client.request.urlopen",
            return_value=FakeHTTPResponse(200, b'{"code":0}'),
        ) as mocked:
            with self.assertLogs("ragflow_service", level="INFO") as captured:
                response = client.sync_rag_info(payload)

        request_obj = mocked.call_args.args[0]
        self.assertEqual(request_obj.full_url, "http://sync.local/syncRagInfo")
        self.assertEqual(json.loads(request_obj.data.decode("utf-8")), payload)
        self.assertEqual(mocked.call_args.kwargs["timeout"], 30.0)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload, {"code": 0})

        output = "\n".join(captured.output)
        self.assertIn('"tenantId": "<redacted>"', output)
        self.assertNotIn("tenant-key", output)

    def test_sync_rag_info_raises_for_http_errors(self):
        client = RagInfoSyncClient("http://sync.local/syncRagInfo")
        http_error = error.HTTPError(
            url="http://sync.local/syncRagInfo",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"failed"}'),
        )

        with patch("ragflow_service.rag_info_sync_client.request.urlopen", side_effect=http_error):
            with self.assertRaisesRegex(RagInfoSyncError, "RAG info sync failed with HTTP 500"):
                client.sync_rag_info(
                    {
                        "knowledgeDatabaseId": "kb_123",
                        "ragFileId": "rf-doc-1",
                        "originFileId": "doc-1",
                        "tenantId": "tenant-key",
                    }
                )


if __name__ == "__main__":
    unittest.main()
