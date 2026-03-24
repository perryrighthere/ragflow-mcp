from __future__ import annotations

from typing import Any

from .exceptions import RagflowAPIError, ValidationError
from .llm_client import OpenAICompatibleClient
from .ragflow_client import RagflowClient, UpstreamResponse

DEFAULT_SYSTEM_PROMPT = """
你是一个知识库问答助手。
请仅基于提供的知识片段进行回答。
每条知识片段只包含文档名称和正文内容。
如果现有知识片段不足以回答问题，请明确说明知识库中暂无足够信息。
请使用与用户问题相同的语言作答。
在合适的情况下，请在回答中提及支撑答案的文档名称。
""".strip()

DEFAULT_USER_PROMPT_TEMPLATE = """
Question:
{{question}}

Knowledge snippets:
{{knowledge_snippets}}
""".strip()

SUPPORTED_PROMPT_VARIABLES = {
    "{{question}}": "The original user question.",
    "{{knowledge_snippets}}": "The merged retrieval snippets built from document names and content only.",
}

RETRIEVAL_FIELDS = {
    "dataset_ids",
    "document_ids",
    "page",
    "page_size",
    "similarity_threshold",
    "vector_similarity_weight",
    "top_k",
    "rerank_id",
    "keyword",
    "highlight",
    "cross_languages",
    "metadata_condition",
    "use_kg",
}


class KnowledgeBaseQAService:
    def __init__(self, ragflow_client: RagflowClient, llm_client: OpenAICompatibleClient):
        self._ragflow_client = ragflow_client
        self._llm_client = llm_client

    def answer_question(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValidationError("question is required")

        retrieval_payload = self._build_retrieval_payload(question, payload)
        retrieval_response = self._ragflow_client.retrieve_chunks(retrieval_payload)
        self._raise_for_retrieval_failure(retrieval_response)

        sources = self._extract_sources(retrieval_response.payload)
        if not sources:
            return {
                "question": question,
                "answer": "知识库中没有检索到可用于回答当前问题的内容，请尝试补充关键词或缩小范围。",
                "sources": [],
                "source_count": 0,
                "retrieval_total": self._extract_retrieval_total(retrieval_response.payload),
                "llm_messages": [],
                "prompt_templates": self._resolve_prompt_templates(payload),
                "model": None,
                "usage": None,
            }

        prompt_templates = self._resolve_prompt_templates(payload)
        llm_messages = self._build_messages(question, sources, prompt_templates=prompt_templates)
        llm_payload = self._llm_client.create_chat_completion(
            llm_messages,
            temperature=payload.get("temperature"),
            max_tokens=payload.get("max_tokens"),
        )
        answer = self._llm_client.extract_message_content(llm_payload)

        return {
            "question": question,
            "answer": answer,
            "sources": sources,
            "source_count": len(sources),
            "retrieval_total": self._extract_retrieval_total(retrieval_response.payload, fallback=len(sources)),
            "llm_messages": llm_messages,
            "prompt_templates": prompt_templates,
            "model": llm_payload.get("model") or self._llm_client.model,
            "usage": llm_payload.get("usage"),
        }

    def _build_retrieval_payload(self, question: str, payload: dict[str, Any]) -> dict[str, Any]:
        retrieval_payload: dict[str, Any] = {"question": question}
        for field in RETRIEVAL_FIELDS:
            value = payload.get(field)
            if value is not None:
                retrieval_payload[field] = value

        if "page_size" not in retrieval_payload and "top_k" not in retrieval_payload:
            retrieval_payload["page_size"] = 6

        return retrieval_payload

    def _raise_for_retrieval_failure(self, response: UpstreamResponse) -> None:
        if response.status_code >= 400:
            raise RagflowAPIError(
                f"RAGFlow retrieval request failed with status {response.status_code}.",
                status_code=response.status_code,
                payload=response.payload if isinstance(response.payload, dict) else {"raw_response": response.payload},
            )

        payload = response.payload
        if isinstance(payload, dict) and payload.get("code") not in (None, 0):
            raise RagflowAPIError("RAGFlow retrieval returned an error payload.", status_code=502, payload=payload)

    def _extract_sources(self, payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, dict):
            return []

        data = payload.get("data")
        if not isinstance(data, dict):
            return []

        chunks = data.get("chunks")
        if not isinstance(chunks, list):
            return []

        sources: list[dict[str, str]] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue

            document_keyword = str(chunk.get("document_keyword", "")).strip()
            content = str(chunk.get("content", "")).strip()
            if not document_keyword and not content:
                continue

            sources.append(
                {
                    "document_keyword": document_keyword,
                    "content": content,
                }
            )

        return sources

    def _build_messages(
        self,
        question: str,
        sources: list[dict[str, str]],
        *,
        prompt_templates: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        prompt_templates = prompt_templates or get_default_prompt_templates()
        snippets: list[str] = []
        for index, source in enumerate(sources, start=1):
            lines = [f"[{index}]"]
            if source["document_keyword"]:
                lines.append(f"Document: {source['document_keyword']}")
            if source["content"]:
                lines.append(f"Content:\n{source['content']}")
            snippets.append("\n".join(lines))
        snippets_text = "\n\n".join(snippets)

        return [
            {"role": "system", "content": prompt_templates["system_prompt"]},
            {
                "role": "user",
                "content": self._render_user_prompt(
                    prompt_templates["user_prompt_template"],
                    question=question,
                    knowledge_snippets=snippets_text,
                ),
            },
        ]

    def _resolve_prompt_templates(self, payload: dict[str, Any]) -> dict[str, str]:
        defaults = get_default_prompt_templates()
        system_prompt = payload.get("system_prompt")
        user_prompt_template = payload.get("user_prompt_template")

        return {
            "system_prompt": str(system_prompt) if system_prompt not in (None, "") else defaults["system_prompt"],
            "user_prompt_template": (
                str(user_prompt_template)
                if user_prompt_template not in (None, "")
                else defaults["user_prompt_template"]
            ),
        }

    def _render_user_prompt(self, template: str, *, question: str, knowledge_snippets: str) -> str:
        return template.replace("{{question}}", question).replace("{{knowledge_snippets}}", knowledge_snippets)

    def _extract_retrieval_total(self, payload: Any, *, fallback: int = 0) -> int:
        if not isinstance(payload, dict):
            return fallback

        data = payload.get("data")
        if not isinstance(data, dict):
            return fallback

        total = data.get("total")
        if isinstance(total, int):
            return total

        return fallback


def get_default_prompt_templates() -> dict[str, str]:
    return {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "user_prompt_template": DEFAULT_USER_PROMPT_TEMPLATE,
    }


def get_prompt_template_metadata() -> dict[str, Any]:
    return {
        **get_default_prompt_templates(),
        "supported_variables": SUPPORTED_PROMPT_VARIABLES,
    }
