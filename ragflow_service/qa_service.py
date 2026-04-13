from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .exceptions import ConfigError, RagflowAPIError, ValidationError
from .llm_client import OpenAICompatibleClient
from .ragflow_client import RagflowClient, UpstreamResponse

DEFAULT_SYSTEM_PROMPT = """
你是一个知识库问答助手。
请仅基于提供的知识片段进行回答。
每条知识片段只包含文档名称和正文内容。
如果现有知识片段不足以回答问题，请明确说明知识库中暂无足够信息。
请使用与用户问题相同的语言作答。
如果引用了知识片段中的信息，请在对应内容后使用方括号编号标注来源，例如 [1]、[2]。
引用编号必须与提供的引用文档列表编号一致。
""".strip()

DEFAULT_USER_PROMPT_TEMPLATE = """
Question:
{{question}}

Knowledge snippets:
{{knowledge_snippets}}
""".strip()

DEFAULT_DIRECT_SYSTEM_PROMPT = """
你是一个问答助手。
请直接、准确地回答用户问题。
如果问题存在不确定性，请明确说明你的判断依据或不确定点。
请使用与用户问题相同的语言作答。
""".strip()

DEFAULT_DIRECT_USER_PROMPT_TEMPLATE = "{{question}}".strip()
DEFAULT_TITLE_SYSTEM_PROMPT = """
你是一个对话标题生成助手。
请基于用户首轮问题和助手首轮回答，生成一个简短、清晰、便于列表展示的中文标题。
要求：
1. 只输出标题本身，不要解释，不要引号，不要编号。
2. 尽量控制在 18 个汉字以内；若是英文，尽量控制在 8 个单词以内。
3. 标题要准确概括主题，避免空泛词语，例如“新对话”或“问题咨询”。
""".strip()

DEFAULT_TITLE_USER_PROMPT_TEMPLATE = """
用户首轮问题：
{{question}}

助手首轮回答：
{{answer}}
""".strip()

NO_SOURCES_ANSWER = "知识库中没有检索到可用于回答当前问题的内容，请尝试补充关键词或缩小范围。"

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


@dataclass(frozen=True)
class PreparedAnswer:
    question: str
    sources: list[dict[str, str | int]]
    referenced_documents: list[dict[str, str | int]]
    retrieval_total: int
    llm_messages: list[dict[str, str]]
    prompt_templates: dict[str, str]
    uses_retrieval: bool

    @property
    def source_count(self) -> int:
        return len(self.sources)

    def to_response(self, *, answer: str, model: str | None, usage: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": answer,
            "sources": self.sources,
            "referenced_documents": self.referenced_documents,
            "source_count": self.source_count,
            "retrieval_total": self.retrieval_total,
            "llm_messages": self.llm_messages,
            "prompt_templates": self.prompt_templates,
            "model": model,
            "usage": usage,
        }


class KnowledgeBaseQAService:
    def __init__(self, ragflow_client: RagflowClient | None, llm_client: OpenAICompatibleClient):
        self._ragflow_client = ragflow_client
        self._llm_client = llm_client

    def answer_question(self, payload: dict[str, Any]) -> dict[str, Any]:
        prepared = self.prepare_answer(payload)
        if prepared.uses_retrieval and not prepared.sources:
            return prepared.to_response(answer=NO_SOURCES_ANSWER, model=None, usage=None)

        llm_payload = self._llm_client.create_chat_completion(
            prepared.llm_messages,
            temperature=payload.get("temperature"),
            max_tokens=payload.get("max_tokens"),
        )
        answer = self._llm_client.extract_message_content(llm_payload)

        return prepared.to_response(
            answer=answer,
            model=llm_payload.get("model") or self._llm_client.model,
            usage=llm_payload.get("usage"),
        )

    def prepare_answer(
        self,
        payload: dict[str, Any],
        *,
        history_summary: str = "",
        history_messages: list[dict[str, str]] | None = None,
    ) -> PreparedAnswer:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValidationError("question is required")

        uses_retrieval = self._should_use_retrieval(payload)
        prompt_templates = self._resolve_prompt_templates(payload, uses_retrieval=uses_retrieval)
        if not uses_retrieval:
            return PreparedAnswer(
                question=question,
                sources=[],
                referenced_documents=[],
                retrieval_total=0,
                llm_messages=self._build_messages(
                    question,
                    [],
                    prompt_templates=prompt_templates,
                    history_summary=history_summary,
                    history_messages=history_messages,
                ),
                prompt_templates=prompt_templates,
                uses_retrieval=False,
            )

        retrieval_payload = self._build_retrieval_payload(question, payload)
        if self._ragflow_client is None:
            raise ConfigError(
                "RAGFlow is not configured. Set RAGFLOW_BASE_URL and RAGFLOW_API_KEY in the environment or .env first."
            )
        retrieval_response = self._ragflow_client.retrieve_chunks(retrieval_payload)
        self._raise_for_retrieval_failure(retrieval_response)

        retrieved_chunks = self._extract_retrieved_chunks(retrieval_response.payload)
        referenced_documents = self._build_referenced_documents(retrieved_chunks)
        sources = self._build_sources(retrieved_chunks, referenced_documents)
        if not sources:
            return PreparedAnswer(
                question=question,
                sources=[],
                referenced_documents=[],
                retrieval_total=self._extract_retrieval_total(retrieval_response.payload),
                llm_messages=[],
                prompt_templates=prompt_templates,
                uses_retrieval=True,
            )

        return PreparedAnswer(
            question=question,
            sources=sources,
            referenced_documents=referenced_documents,
            retrieval_total=self._extract_retrieval_total(
                retrieval_response.payload,
                fallback=len(sources),
            ),
            llm_messages=self._build_messages(
                question,
                sources,
                prompt_templates=prompt_templates,
                history_summary=history_summary,
                history_messages=history_messages,
            ),
            prompt_templates=prompt_templates,
            uses_retrieval=True,
        )

    def generate_conversation_title(self, *, question: str, answer: str) -> str:
        normalized_question = str(question or "").strip()
        normalized_answer = str(answer or "").strip()
        if not normalized_question:
            raise ValidationError("question is required")
        if not normalized_answer:
            raise ValidationError("answer is required")

        messages = [
            {"role": "system", "content": DEFAULT_TITLE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": DEFAULT_TITLE_USER_PROMPT_TEMPLATE.replace("{{question}}", normalized_question).replace(
                    "{{answer}}",
                    normalized_answer,
                ),
            },
        ]
        payload = self._llm_client.create_chat_completion(messages, temperature=0.2, max_tokens=32)
        raw_title = self._llm_client.extract_message_content(payload)
        return self._normalize_generated_title(raw_title, fallback_question=normalized_question)

    def _should_use_retrieval(self, payload: dict[str, Any]) -> bool:
        return "dataset_ids" in payload and payload.get("dataset_ids") is not None

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

    def _extract_retrieved_chunks(self, payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, dict):
            return []

        data = payload.get("data")
        if not isinstance(data, dict):
            return []

        chunks = data.get("chunks")
        if not isinstance(chunks, list):
            return []

        chunks_with_metadata: list[dict[str, str]] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue

            document_keyword = str(
                chunk.get("document_keyword")
                or chunk.get("document_name")
                or chunk.get("doc_name")
                or ""
            ).strip()
            content = str(chunk.get("content", "")).strip()
            if not document_keyword and not content:
                continue

            chunks_with_metadata.append(
                {
                    "document_keyword": document_keyword,
                    "dataset_id": str(chunk.get("dataset_id", "")).strip(),
                    "document_id": str(chunk.get("document_id", "")).strip(),
                    "content": content,
                }
            )

        return chunks_with_metadata

    def _build_referenced_documents(
        self,
        retrieved_chunks: list[dict[str, str]],
    ) -> list[dict[str, str | int]]:
        referenced_documents: list[dict[str, str | int]] = []
        seen_keys: dict[tuple[str, str, str], int] = {}

        for chunk in retrieved_chunks:
            document_name = chunk["document_keyword"]
            dataset_id = chunk["dataset_id"]
            document_id = chunk["document_id"]
            reference_key = (dataset_id, document_id, document_name)
            if reference_key in seen_keys:
                continue

            reference_index = len(referenced_documents) + 1
            seen_keys[reference_key] = reference_index
            referenced_documents.append(
                {
                    "index": reference_index,
                    "document_name": document_name,
                    "dataset_id": dataset_id,
                    "document_id": document_id,
                }
            )

        return referenced_documents

    def _build_sources(
        self,
        retrieved_chunks: list[dict[str, str]],
        referenced_documents: list[dict[str, str | int]],
    ) -> list[dict[str, str | int]]:
        reference_index_by_key = {
            (
                str(document["dataset_id"]),
                str(document["document_id"]),
                str(document["document_name"]),
            ): int(document["index"])
            for document in referenced_documents
        }

        sources: list[dict[str, str | int]] = []
        for chunk in retrieved_chunks:
            reference_index = reference_index_by_key.get(
                (chunk["dataset_id"], chunk["document_id"], chunk["document_keyword"]),
                0,
            )
            sources.append(
                {
                    "reference_index": reference_index,
                    "document_keyword": chunk["document_keyword"],
                    "content": chunk["content"],
                }
            )

        return sources

    def _build_messages(
        self,
        question: str,
        sources: list[dict[str, str | int]],
        *,
        prompt_templates: dict[str, str] | None = None,
        history_summary: str = "",
        history_messages: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        prompt_templates = prompt_templates or get_default_prompt_templates()
        snippets: list[str] = []
        for index, source in enumerate(sources, start=1):
            reference_index = int(source.get("reference_index") or index)
            document_keyword = str(source.get("document_keyword", "")).strip()
            content = str(source.get("content", "")).strip()
            lines = [f"[{reference_index}]"]
            if document_keyword:
                lines.append(f"Document: {document_keyword}")
            if content:
                lines.append(f"Content:\n{content}")
            snippets.append("\n".join(lines))
        snippets_text = "\n\n".join(snippets)

        user_message = self._render_user_prompt(
            prompt_templates["user_prompt_template"],
            question=question,
            knowledge_snippets=snippets_text,
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": prompt_templates["system_prompt"]}]
        normalized_history_summary = str(history_summary or "").strip()
        if normalized_history_summary:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "以下是当前会话更早历史的摘要，仅用于延续上下文，不要机械复述给用户：\n"
                        f"{normalized_history_summary}"
                    ),
                }
            )

        for message in history_messages or []:
            role = str(message.get("role", "")).strip()
            content = str(message.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})
        return messages

    def _resolve_prompt_templates(self, payload: dict[str, Any], *, uses_retrieval: bool) -> dict[str, str]:
        defaults = get_default_prompt_templates(uses_retrieval=uses_retrieval)
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

    def _normalize_generated_title(self, title: str, *, fallback_question: str) -> str:
        normalized = str(title or "").strip().splitlines()[0].strip() if str(title or "").strip() else ""
        normalized = normalized.strip("\"'` ")
        normalized = " ".join(normalized.split())
        if not normalized:
            normalized = fallback_question
        return normalized[:80]

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


def get_default_prompt_templates(*, uses_retrieval: bool = True) -> dict[str, str]:
    if not uses_retrieval:
        return {
            "system_prompt": DEFAULT_DIRECT_SYSTEM_PROMPT,
            "user_prompt_template": DEFAULT_DIRECT_USER_PROMPT_TEMPLATE,
        }

    return {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "user_prompt_template": DEFAULT_USER_PROMPT_TEMPLATE,
    }


def get_prompt_template_metadata() -> dict[str, Any]:
    return {
        **get_default_prompt_templates(),
        "direct_answer_defaults": get_default_prompt_templates(uses_retrieval=False),
        "supported_variables": SUPPORTED_PROMPT_VARIABLES,
    }
