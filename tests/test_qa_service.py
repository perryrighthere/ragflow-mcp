import unittest

from ragflow_service.qa_service import KnowledgeBaseQAService, get_default_prompt_templates, get_prompt_template_metadata
from ragflow_service.ragflow_client import UpstreamResponse


class FakeRagflowClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def retrieve_chunks(self, payload):
        self.calls.append(payload)
        return UpstreamResponse(status_code=200, payload=self.payload)


class FakeLLMClient:
    def __init__(self):
        self.calls = []
        self.model = "fake-model"

    def create_chat_completion(self, messages, *, temperature=None, max_tokens=None):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return {
            "model": "fake-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "这是答案。",
                    }
                }
            ],
            "usage": {"total_tokens": 88},
        }

    def extract_message_content(self, payload):
        return payload["choices"][0]["message"]["content"]

    def stream_chat_completion(self, messages, *, temperature=None, max_tokens=None):
        return iter([])

    def extract_stream_delta(self, payload):
        return ""


class QAServiceTests(unittest.TestCase):
    def test_answer_question_only_passes_document_and_content_to_llm(self):
        ragflow_payload = {
            "code": 0,
            "data": {
                "total": 2,
                "chunks": [
                    {
                        "content": "五看包括看行业、看市场、看用户、看竞争、看自己。",
                        "document_keyword": "doc-a",
                        "similarity": 0.91,
                    },
                    {
                        "content": "六定包括定位、定标、定价、定配、定本、定量。",
                        "document_keyword": "doc-b",
                        "vector_similarity": 0.83,
                    },
                ],
            },
        }
        ragflow_client = FakeRagflowClient(ragflow_payload)
        llm_client = FakeLLMClient()
        service = KnowledgeBaseQAService(ragflow_client, llm_client)

        result = service.answer_question(
            {
                "question": "五看六定是什么？",
                "dataset_ids": ["kb_123"],
                "temperature": 0.2,
            }
        )

        self.assertEqual(
            result["sources"],
            [
                {"document_keyword": "doc-a", "content": "五看包括看行业、看市场、看用户、看竞争、看自己。"},
                {"document_keyword": "doc-b", "content": "六定包括定位、定标、定价、定配、定本、定量。"},
            ],
        )
        self.assertEqual(result["llm_messages"], llm_client.calls[0]["messages"])
        self.assertEqual(result["prompt_templates"], get_default_prompt_templates())
        self.assertEqual(ragflow_client.calls[0]["page_size"], 6)
        self.assertEqual(llm_client.calls[0]["temperature"], 0.2)
        prompt = llm_client.calls[0]["messages"][1]["content"]
        self.assertIn("Document: doc-a", prompt)
        self.assertIn("Content:\n五看包括看行业、看市场、看用户、看竞争、看自己。", prompt)
        self.assertIn("Document: doc-b", prompt)
        self.assertNotIn("similarity", prompt)
        self.assertNotIn("0.91", prompt)

    def test_answer_question_skips_llm_when_no_sources(self):
        ragflow_client = FakeRagflowClient({"code": 0, "data": {"total": 0, "chunks": []}})
        llm_client = FakeLLMClient()
        service = KnowledgeBaseQAService(ragflow_client, llm_client)

        result = service.answer_question({"question": "没有命中怎么办？"})

        self.assertEqual(result["source_count"], 0)
        self.assertEqual(result["retrieval_total"], 0)
        self.assertEqual(result["llm_messages"], [])
        self.assertEqual(result["prompt_templates"], get_default_prompt_templates())
        self.assertEqual(llm_client.calls, [])

    def test_answer_question_applies_prompt_overrides(self):
        ragflow_client = FakeRagflowClient(
            {
                "code": 0,
                "data": {
                    "total": 1,
                    "chunks": [
                        {
                            "content": "流程目标是支撑产品完成立项。",
                            "document_keyword": "doc-a",
                        }
                    ],
                },
            }
        )
        llm_client = FakeLLMClient()
        service = KnowledgeBaseQAService(ragflow_client, llm_client)

        result = service.answer_question(
            {
                "question": "流程目的是什么？",
                "system_prompt": "你是流程顾问。",
                "user_prompt_template": "Q={{question}}\nKB={{knowledge_snippets}}",
            }
        )

        self.assertEqual(result["prompt_templates"]["system_prompt"], "你是流程顾问。")
        self.assertEqual(result["prompt_templates"]["user_prompt_template"], "Q={{question}}\nKB={{knowledge_snippets}}")
        self.assertEqual(result["llm_messages"][0]["content"], "你是流程顾问。")
        self.assertIn("Q=流程目的是什么？", result["llm_messages"][1]["content"])
        self.assertIn("KB=[1]", result["llm_messages"][1]["content"])

    def test_prepare_answer_builds_context_without_calling_llm(self):
        ragflow_client = FakeRagflowClient(
            {
                "code": 0,
                "data": {
                    "total": 1,
                    "chunks": [
                        {
                            "content": "流程目标是支撑产品完成立项。",
                            "document_keyword": "doc-a",
                        }
                    ],
                },
            }
        )
        llm_client = FakeLLMClient()
        service = KnowledgeBaseQAService(ragflow_client, llm_client)

        prepared = service.prepare_answer({"question": "流程目的是什么？"})

        self.assertEqual(prepared.question, "流程目的是什么？")
        self.assertEqual(prepared.source_count, 1)
        self.assertEqual(prepared.prompt_templates, get_default_prompt_templates())
        self.assertEqual(prepared.llm_messages[0]["role"], "system")
        self.assertIn("Document: doc-a", prepared.llm_messages[1]["content"])
        self.assertEqual(llm_client.calls, [])

    def test_prompt_template_metadata_includes_supported_variables(self):
        metadata = get_prompt_template_metadata()

        self.assertEqual(metadata["system_prompt"], get_default_prompt_templates()["system_prompt"])
        self.assertIn("{{question}}", metadata["supported_variables"])
        self.assertIn("{{knowledge_snippets}}", metadata["supported_variables"])


if __name__ == "__main__":
    unittest.main()
