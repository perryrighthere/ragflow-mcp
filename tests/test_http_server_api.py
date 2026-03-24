import unittest

from fastapi.testclient import TestClient

from ragflow_service.exceptions import RagflowAPIError
from ragflow_service.config import Settings
from ragflow_service.http_server import create_application
from ragflow_service.qa_service import get_default_prompt_templates
from ragflow_service.ragflow_client import UpstreamResponse


class FakeClient:
    def __init__(self):
        self.retrieve_calls = []
        self.list_calls = []
        self.upload_calls = []
        self.update_calls = []
        self.parse_calls = []
        self.qa_mode = False
        self.qa_payload = {
            "code": 0,
            "data": {
                "chunks": [
                    {
                        "content": "五看包括看行业、看市场、看用户、看竞争、看自己。",
                        "document_keyword": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
                        "similarity": 0.91,
                    }
                ],
                "total": 1,
            },
        }

    def healthz(self):
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": {"status": "ok"}})

    def retrieve_chunks(self, payload):
        self.retrieve_calls.append(payload)
        if self.qa_mode:
            return UpstreamResponse(status_code=200, payload=self.qa_payload)
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


class FakeLLMClient:
    def __init__(self):
        self.calls = []
        self.model = "test-qa-model"

    def create_chat_completion(self, messages, *, temperature=None, max_tokens=None):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return {
            "model": self.model,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "五看包括看行业、看市场、看用户、看竞争、看自己。",
                    }
                }
            ],
            "usage": {"total_tokens": 123},
        }

    def extract_message_content(self, payload):
        return payload["choices"][0]["message"]["content"]


class HttpServerApiTests(unittest.TestCase):
    def setUp(self):
        app = create_application(
            Settings(
                ragflow_base_url="http://ragflow.local:9380",
                ragflow_api_key="secret-key",
                llm_base_url="https://llm.local/v1",
                llm_api_key="llm-key",
                llm_model="test-qa-model",
                request_timeout=60.0,
                llm_timeout=60.0,
                server_host="127.0.0.1",
                server_port=8080,
            )
        )
        app.state.runtime._client = FakeClient()
        app.state.runtime._llm_client = FakeLLMClient()
        self.client = TestClient(app)
        self.fake_client = app.state.runtime._client
        self.fake_llm = app.state.runtime._llm_client

    def test_root_serves_console_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Knowledge Base Q&amp;A Console", response.text)

    def test_docs_page_is_available(self):
        response = self.client.get("/docs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Swagger UI", response.text)

    def test_prompt_template_route_returns_defaults(self):
        response = self.client.get("/api/v1/qa/prompt-templates")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["system_prompt"], get_default_prompt_templates()["system_prompt"])
        self.assertIn("{{question}}", response.json()["data"]["supported_variables"])

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

    def test_qa_route_returns_answer_and_trimmed_sources(self):
        self.fake_client.qa_mode = True
        response = self.client.post(
            "/api/v1/qa/answer",
            json={
                "question": "五看是什么？",
                "dataset_ids": ["kb_123"],
                "page_size": 3,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["answer"], "五看包括看行业、看市场、看用户、看竞争、看自己。")
        self.assertEqual(
            payload["sources"],
            [
                {
                    "document_keyword": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
                    "content": "五看包括看行业、看市场、看用户、看竞争、看自己。",
                }
            ],
        )
        self.assertEqual(payload["llm_messages"], self.fake_llm.calls[0]["messages"])
        self.assertEqual(payload["prompt_templates"], get_default_prompt_templates())
        self.assertEqual(self.fake_client.retrieve_calls[-1]["page_size"], 3)
        llm_prompt = self.fake_llm.calls[0]["messages"][1]["content"]
        self.assertIn("Document: IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx", llm_prompt)
        self.assertIn("Content:\n五看包括看行业、看市场、看用户、看竞争、看自己。", llm_prompt)
        self.assertNotIn("similarity", llm_prompt)

    def test_qa_route_accepts_prompt_overrides(self):
        self.fake_client.qa_mode = True
        response = self.client.post(
            "/api/v1/qa/answer",
            json={
                "question": "五看是什么？",
                "system_prompt": "你是项目顾问。",
                "user_prompt_template": "问题={{question}}\n资料={{knowledge_snippets}}",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["prompt_templates"]["system_prompt"], "你是项目顾问。")
        self.assertEqual(payload["llm_messages"][0]["content"], "你是项目顾问。")
        self.assertIn("问题=五看是什么？", payload["llm_messages"][1]["content"])

    def test_qa_route_returns_json_when_ragflow_connection_fails(self):
        def broken_retrieve(payload):
            raise RagflowAPIError("Unable to connect to RAGFlow: Connection reset by peer", status_code=502)

        self.fake_client.retrieve_chunks = broken_retrieve
        response = self.client.post(
            "/api/v1/qa/answer",
            json={
                "question": "五看是什么？",
            },
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"detail": "Unable to connect to RAGFlow: Connection reset by peer"},
        )


if __name__ == "__main__":
    unittest.main()
