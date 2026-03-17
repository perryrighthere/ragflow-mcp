from __future__ import annotations

from copy import deepcopy
from typing import Any

from .exceptions import ValidationError
from .ragflow_client import FileUpload, RagflowClient

SUPPORTED_COMPARISON_OPERATORS = {
    "contains",
    "not contains",
    "start with",
    "empty",
    "not empty",
    "=",
    "≠",
    ">",
    "<",
    "≥",
    "≤",
}

VALUE_OPTIONAL_OPERATORS = {"empty", "not empty"}


class RagflowDocumentService:
    def __init__(self, client: RagflowClient):
        self.client = client

    def upload_documents(
        self,
        *,
        dataset_id: str,
        files: list[FileUpload],
        shared_meta_fields: dict[str, Any] | None = None,
        per_file_meta_fields: dict[str, dict[str, Any]] | None = None,
        parse_after_upload: bool = True,
        enabled: int | None = None,
        chunk_method: str | None = None,
        parser_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not dataset_id:
            raise ValidationError("`dataset_id` is required.")
        if not files:
            raise ValidationError("At least one file must be uploaded.")

        uploaded_documents = self.client.upload_documents(dataset_id, files)
        updated_documents: list[dict[str, Any]] = []
        document_ids: list[str] = []

        shared = deepcopy(shared_meta_fields or {})
        per_file = per_file_meta_fields or {}

        for document in uploaded_documents:
            doc_name = document.get("name") or document.get("location") or ""
            meta_fields = deepcopy(shared)
            meta_fields.update(per_file.get(doc_name, {}))

            update_payload: dict[str, Any] = {}
            if meta_fields:
                update_payload["meta_fields"] = meta_fields
            if enabled is not None:
                update_payload["enabled"] = enabled
            if chunk_method:
                update_payload["chunk_method"] = chunk_method
            if parser_config is not None:
                update_payload["parser_config"] = parser_config

            if update_payload:
                self.client.update_document(dataset_id, document["id"], update_payload)

            updated_documents.append(
                {
                    "document_id": document["id"],
                    "name": doc_name,
                    "meta_fields": meta_fields,
                    "parse_requested": parse_after_upload,
                }
            )
            document_ids.append(document["id"])

        parse_result = None
        if parse_after_upload and document_ids:
            parse_result = self.client.parse_documents(dataset_id, document_ids)

        return {
            "dataset_id": dataset_id,
            "documents": updated_documents,
            "parse_requested": parse_after_upload,
            "parse_result": parse_result,
        }

    def update_document_metadata(
        self,
        *,
        dataset_id: str,
        document_id: str,
        meta_fields: dict[str, Any],
        enabled: int | None = None,
        name: str | None = None,
        chunk_method: str | None = None,
        parser_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not dataset_id:
            raise ValidationError("`dataset_id` is required.")
        if not document_id:
            raise ValidationError("`document_id` is required.")
        if not isinstance(meta_fields, dict):
            raise ValidationError("`meta_fields` must be an object.")

        payload: dict[str, Any] = {"meta_fields": meta_fields}
        if enabled is not None:
            payload["enabled"] = enabled
        if name:
            payload["name"] = name
        if chunk_method:
            payload["chunk_method"] = chunk_method
        if parser_config is not None:
            payload["parser_config"] = parser_config

        self.client.update_document(dataset_id, document_id, payload)
        return {
            "dataset_id": dataset_id,
            "document_id": document_id,
            "meta_fields": meta_fields,
        }

    def batch_update_document_metadata(
        self,
        *,
        dataset_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not documents:
            raise ValidationError("`documents` must contain at least one item.")

        updated = []
        for item in documents:
            updated.append(
                self.update_document_metadata(
                    dataset_id=dataset_id,
                    document_id=item.get("document_id", ""),
                    meta_fields=item.get("meta_fields", {}),
                    enabled=item.get("enabled"),
                    name=item.get("name"),
                    chunk_method=item.get("chunk_method"),
                    parser_config=item.get("parser_config"),
                )
            )
        return {"dataset_id": dataset_id, "documents": updated}

    def retrieve_chunks(
        self,
        *,
        question: str,
        dataset_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        page: int = 1,
        page_size: int = 30,
        similarity_threshold: float | None = None,
        vector_similarity_weight: float | None = None,
        top_k: int | None = None,
        rerank_id: str | int | None = None,
        keyword: bool = False,
        highlight: bool = False,
        cross_languages: list[str] | None = None,
        use_kg: bool | None = None,
        metadata_condition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not question:
            raise ValidationError("`question` is required.")
        if not dataset_ids and not document_ids:
            raise ValidationError("At least one of `dataset_ids` or `document_ids` is required.")

        payload: dict[str, Any] = {
            "question": question,
            "page": page,
            "page_size": page_size,
            "keyword": keyword,
            "highlight": highlight,
        }
        if dataset_ids:
            payload["dataset_ids"] = dataset_ids
        if document_ids:
            payload["document_ids"] = document_ids
        if similarity_threshold is not None:
            payload["similarity_threshold"] = similarity_threshold
        if vector_similarity_weight is not None:
            payload["vector_similarity_weight"] = vector_similarity_weight
        if top_k is not None:
            payload["top_k"] = top_k
        if rerank_id is not None:
            payload["rerank_id"] = rerank_id
        if cross_languages:
            payload["cross_languages"] = cross_languages
        if use_kg is not None:
            payload["use_kg"] = use_kg

        normalized_metadata_condition = self._normalize_metadata_condition(metadata_condition)
        if normalized_metadata_condition:
            payload["metadata_condition"] = normalized_metadata_condition

        return self.client.retrieve_chunks(payload)

    def list_documents(self, dataset_id: str, query: dict[str, Any]) -> dict[str, Any]:
        if not dataset_id:
            raise ValidationError("`dataset_id` is required.")
        return self.client.list_documents(dataset_id, query)

    def healthz(self) -> dict[str, Any]:
        return self.client.healthz()

    def _normalize_metadata_condition(self, metadata_condition: dict[str, Any] | None) -> dict[str, Any] | None:
        if metadata_condition is None:
            return None
        if not isinstance(metadata_condition, dict):
            raise ValidationError("`metadata_condition` must be an object.")

        raw_conditions = metadata_condition.get("conditions")
        if raw_conditions in (None, []):
            return None
        if not isinstance(raw_conditions, list):
            raise ValidationError("`metadata_condition.conditions` must be an array.")

        conditions = []
        for item in raw_conditions:
            if not isinstance(item, dict):
                raise ValidationError("Each metadata condition must be an object.")
            name = str(item.get("name", "")).strip()
            operator = str(item.get("comparison_operator", "")).strip()
            value = item.get("value")

            if not name:
                raise ValidationError("Each metadata filter requires `name`.")
            if operator not in SUPPORTED_COMPARISON_OPERATORS:
                raise ValidationError(f"Unsupported metadata operator: {operator}")
            if operator not in VALUE_OPTIONAL_OPERATORS and value in (None, ""):
                raise ValidationError(f"Metadata filter `{name}` requires `value`.")

            condition = {
                "name": name,
                "comparison_operator": operator,
            }
            if operator not in VALUE_OPTIONAL_OPERATORS:
                condition["value"] = str(value)
            conditions.append(condition)

        return {"conditions": conditions}
