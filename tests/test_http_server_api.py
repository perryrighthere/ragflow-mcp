import unittest

from fastapi.testclient import TestClient

from ragflow_service.config import Settings
from ragflow_service.http_server import create_application
from ragflow_service.ragflow_client import UpstreamResponse


class FakeClient:
    def __init__(self):
        self.retrieve_calls = []
        self.list_calls = []
        self.upload_calls = []
        self.update_calls = []
        self.parse_calls = []

    def healthz(self):
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": {"status": "ok"}})

    def retrieve_chunks(self, payload):
        self.retrieve_calls.append(payload)
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": payload})

    def list_documents(self, dataset_id, query):
        self.list_calls.append((dataset_id, query))
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": {"dataset_id": dataset_id, "query": query}})

    def upload_documents(self, dataset_id, files):
        self.upload_calls.append((dataset_id, files))
        return UpstreamResponse(
            status_code=200,
            payload={"code": 0, "data": {"dataset_id": dataset_id, "filenames": [file.filename for file in files]}},
        )

    def update_document(self, dataset_id, document_id, payload):
        self.update_calls.append((dataset_id, document_id, payload))
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": payload})

    def parse_documents(self, dataset_id, payload):
        self.parse_calls.append((dataset_id, payload))
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": payload})


class HttpServerApiTests(unittest.TestCase):
    def setUp(self):
        app = create_application(
            Settings(
                ragflow_base_url="http://ragflow.local:9380",
                ragflow_api_key="secret-key",
                request_timeout=60.0,
                server_host="127.0.0.1",
                server_port=8080,
            )
        )
        app.state.runtime._client = FakeClient()
        self.client = TestClient(app)
        self.fake_client = app.state.runtime._client

    def test_root_redirects_to_docs(self):
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/docs")

    def test_docs_page_is_available(self):
        response = self.client.get("/docs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Swagger UI", response.text)

    def test_retrieval_route_proxies_raw_payload(self):
        response = self.client.post(
            "/api/v1/retrieval",
            json={
                "question": "五看六定是什么？",
                "dataset_ids": ["kb_123"],
                "highlight": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake_client.retrieve_calls[0]["question"], "五看六定是什么？")
        self.assertEqual(response.json()["data"]["dataset_ids"], ["kb_123"])

    def test_retrieval_route_does_not_inject_page_defaults(self):
        response = self.client.post(
            "/api/v1/retrieval",
            json={
                "question": "五看六定",
                "dataset_ids": ["kb_123"],
                "vector_similarity_weight": 0.7,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.fake_client.retrieve_calls[0],
            {
                "question": "五看六定",
                "dataset_ids": ["kb_123"],
                "vector_similarity_weight": 0.7,
            },
        )

    def test_list_documents_route_proxies_query_params(self):
        response = self.client.get(
            "/api/v1/datasets/kb_123/documents",
            params=[("page", "1"), ("page_size", "20"), ("run", "DONE"), ("run", "UNSTART")],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.fake_client.list_calls[0],
            ("kb_123", {"page": 1, "page_size": 20, "run": ["DONE", "UNSTART"]}),
        )

    def test_upload_route_proxies_files(self):
        response = self.client.post(
            "/api/v1/datasets/kb_123/documents",
            files=[("files", ("a.txt", b"hello", "text/plain"))],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake_client.upload_calls[0][0], "kb_123")
        self.assertEqual(self.fake_client.upload_calls[0][1][0].filename, "a.txt")


if __name__ == "__main__":
    unittest.main()
