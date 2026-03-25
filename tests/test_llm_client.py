import unittest
from unittest.mock import patch

from ragflow_service.exceptions import LLMAPIError
from ragflow_service.llm_client import OpenAICompatibleClient


class FakeStreamingHTTPResponse:
    def __init__(self, lines):
        self._lines = [line.encode("utf-8") for line in lines]

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_stream_chat_completion_parses_sse_and_preserves_delta_spacing(self):
        client = OpenAICompatibleClient("https://llm.local/v1", "llm-key", "test-model")

        with patch(
            "ragflow_service.llm_client.request.urlopen",
            return_value=FakeStreamingHTTPResponse(
                [
                    'data: {"model":"test-model","choices":[{"delta":{"role":"assistant","content":"Hello"}}]}\n',
                    "\n",
                    'data: {"choices":[{"delta":{"content":" world"}}],"usage":{"total_tokens":42}}\n',
                    "\n",
                    "data: [DONE]\n",
                    "\n",
                ]
            ),
        ):
            chunks = list(client.stream_chat_completion([{"role": "user", "content": "Say hello"}]))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["model"], "test-model")
        self.assertEqual(chunks[1]["usage"], {"total_tokens": 42})
        self.assertEqual(client.extract_stream_delta(chunks[0]), "Hello")
        self.assertEqual(client.extract_stream_delta(chunks[1]), " world")
        self.assertEqual("".join(client.extract_stream_delta(chunk) for chunk in chunks).strip(), "Hello world")

    def test_stream_chat_completion_rejects_non_json_events(self):
        client = OpenAICompatibleClient("https://llm.local/v1", "llm-key", "test-model")

        with patch(
            "ragflow_service.llm_client.request.urlopen",
            return_value=FakeStreamingHTTPResponse(
                [
                    "data: not-json\n",
                    "\n",
                ]
            ),
        ):
            with self.assertRaisesRegex(LLMAPIError, "LLM stream chunk is not a JSON object"):
                list(client.stream_chat_completion([{"role": "user", "content": "Say hello"}]))


if __name__ == "__main__":
    unittest.main()
