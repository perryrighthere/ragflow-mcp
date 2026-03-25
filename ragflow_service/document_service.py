from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .exceptions import RagflowAPIError, ValidationError
from .knowledge_portal_service import KnowledgePortalSyncService
from .ragflow_client import FileUpload, RagflowClient, UpstreamResponse


class RagflowDocumentService:
    def __init__(self, client: RagflowClient, knowledge_portal_service: KnowledgePortalSyncService):
        self.client = client
        self.knowledge_portal_service = knowledge_portal_service

    def import_knowledge_portal_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._validate_import_payload(payload)
        sync_payload = {
            "base_url": normalized["base_url"],
            "community_id": normalized["community_id"],
            "username": normalized["username"],
            "password": normalized["password"],
            "type": normalized["type"],
            "page_size": normalized["page_size"],
            "max_download_files": normalized["max_download_files"],
            "begin_time": normalized["begin_time"],
            "fd_cate_id": normalized["fd_cate_id"],
            "timeout": normalized["timeout"],
            "include_attachments": normalized["include_attachments"],
            "include_cover_image": normalized["include_cover_image"],
        }
        sync_result = self.knowledge_portal_service.sync_documents(sync_payload)

        imported_documents: list[dict[str, Any]] = []
        skipped_documents: list[dict[str, Any]] = []
        errors = self._normalize_errors(sync_result.get("errors"))
        parse_document_ids: list[str] = []
        uploaded_file_count = 0
        updated_document_count = 0

        for portal_document in sync_result.get("documents") or []:
            fd_id = str(portal_document.get("fdId") or "").strip()
            fd_name = str(portal_document.get("fdName") or "").strip()
            try:
                detail_data = self._load_detail_data(portal_document)
                upload_sources = self._build_upload_sources(
                    portal_document,
                    detail_data=detail_data,
                    include_attachments=normalized["include_attachments"],
                    include_cover_image=normalized["include_cover_image"],
                    fallback_to_content_markdown=normalized["fallback_to_content_markdown"],
                )
            except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
                errors.append(
                    {
                        "stage": "prepare_upload",
                        "fdId": fd_id,
                        "detail": str(exc),
                    }
                )
                continue

            if not upload_sources:
                skipped_documents.append(
                    {
                        "fdId": fd_id,
                        "fdName": fd_name or str(detail_data.get("fdName") or ""),
                        "reason": "No eligible files were available for upload.",
                        "saved_dir": portal_document.get("saved_dir"),
                    }
                )
                continue

            try:
                uploaded_documents = self._upload_sources(
                    dataset_id=normalized["dataset_id"],
                    upload_sources=upload_sources,
                )
            except RagflowAPIError as exc:
                errors.append(self._build_ragflow_error("upload", fd_id=fd_id, exc=exc))
                continue

            uploaded_file_count += len(uploaded_documents)
            ragflow_documents: list[dict[str, Any]] = []

            for index, uploaded_document in enumerate(uploaded_documents):
                upload_source = upload_sources[index]
                update_payload = self._build_document_update_payload(
                    base_update=normalized["document_update"],
                    detail_data=detail_data,
                    portal_document=portal_document,
                    upload_source=upload_source,
                )
                ragflow_entry = {
                    "document_id": uploaded_document["id"],
                    "name": uploaded_document["name"],
                    "upload_source": self._serialize_upload_source(upload_source),
                }
                try:
                    update_response = self.client.update_document(
                        normalized["dataset_id"],
                        uploaded_document["id"],
                        update_payload,
                    )
                    self._require_success_response(
                        update_response,
                        action=f"update document {uploaded_document['id']}",
                    )
                except RagflowAPIError as exc:
                    errors.append(
                        self._build_ragflow_error(
                            "update",
                            fd_id=fd_id,
                            exc=exc,
                            document_id=uploaded_document["id"],
                        )
                    )
                    ragflow_entry["status"] = "uploaded"
                    ragflow_documents.append(ragflow_entry)
                    continue

                updated_document_count += 1
                parse_document_ids.append(uploaded_document["id"])
                ragflow_entry["status"] = "updated"
                ragflow_entry["meta_fields"] = update_payload.get("meta_fields", {})
                ragflow_documents.append(ragflow_entry)

            imported_documents.append(
                {
                    "fdId": fd_id,
                    "fdName": fd_name or str(detail_data.get("fdName") or ""),
                    "saved_dir": portal_document.get("saved_dir"),
                    "upload_sources": [self._serialize_upload_source(item) for item in upload_sources],
                    "ragflow_documents": ragflow_documents,
                }
            )

        parse_result = None
        if normalized["parse_after_upload"] and parse_document_ids:
            try:
                parse_response = self.client.parse_documents(
                    normalized["dataset_id"],
                    {"document_ids": parse_document_ids},
                )
                self._require_success_response(parse_response, action="parse documents")
                parse_result = parse_response.payload
            except RagflowAPIError as exc:
                errors.append(self._build_ragflow_error("parse", fd_id="", exc=exc))

        return {
            "dataset_id": normalized["dataset_id"],
            "base_url": sync_result.get("base_url"),
            "output_dir": sync_result.get("output_dir"),
            "total_documents": sync_result.get("total_documents", 0),
            "downloaded_document_count": sync_result.get("downloaded_document_count", 0),
            "downloaded_file_count": sync_result.get("downloaded_file_count", 0),
            "max_download_files": sync_result.get("max_download_files"),
            "download_limit_reached": sync_result.get("download_limit_reached", False),
            "imported_document_count": len(imported_documents),
            "uploaded_file_count": uploaded_file_count,
            "updated_document_count": updated_document_count,
            "parse_requested": normalized["parse_after_upload"],
            "parsed_document_count": len(parse_document_ids),
            "parse_result": parse_result,
            "documents": imported_documents,
            "skipped_documents": skipped_documents,
            "errors": errors,
        }

    def _validate_import_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_sync_payload = self.knowledge_portal_service._validate_payload(payload)
        dataset_id = str(payload.get("dataset_id") or "").strip()
        if not dataset_id:
            raise ValidationError("dataset_id is required")

        document_update_raw = payload.get("document_update")
        if document_update_raw is None:
            document_update_raw = {}
        if not isinstance(document_update_raw, dict):
            raise ValidationError("document_update must be an object")
        document_update = deepcopy(document_update_raw)

        meta_fields = document_update.get("meta_fields")
        if meta_fields is not None and not isinstance(meta_fields, dict):
            raise ValidationError("document_update.meta_fields must be an object")

        parse_after_upload = bool(payload.get("parse_after_upload", False))
        fallback_to_content_markdown = bool(payload.get("fallback_to_content_markdown", True))
        include_attachments = normalized_sync_payload["include_attachments"]
        include_cover_image = normalized_sync_payload["include_cover_image"]
        if not include_attachments and not include_cover_image and not fallback_to_content_markdown:
            raise ValidationError(
                "At least one upload source must be enabled: include_attachments, include_cover_image, "
                "or fallback_to_content_markdown."
            )

        normalized_sync_payload.update(
            {
                "dataset_id": dataset_id,
                "document_update": document_update,
                "parse_after_upload": parse_after_upload,
                "fallback_to_content_markdown": fallback_to_content_markdown,
            }
        )
        return normalized_sync_payload

    def _build_upload_sources(
        self,
        portal_document: dict[str, Any],
        *,
        detail_data: dict[str, Any],
        include_attachments: bool,
        include_cover_image: bool,
        fallback_to_content_markdown: bool,
    ) -> list[dict[str, Any]]:
        upload_sources: list[dict[str, Any]] = []
        downloaded_files = portal_document.get("downloaded_files") or []
        if isinstance(downloaded_files, list):
            for item in downloaded_files:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip()
                if kind == "attachment" and not include_attachments:
                    continue
                if kind == "cover" and not include_cover_image:
                    continue
                path_value = str(item.get("path") or "").strip()
                if not path_value:
                    continue
                file_path = Path(path_value)
                if not file_path.is_file():
                    raise ValidationError(f"Upload source file does not exist: {file_path}")
                upload_sources.append(
                    {
                        "kind": kind or "attachment",
                        "path": str(file_path),
                        "portal_file_id": str(item.get("file_id") or "").strip() or None,
                        "portal_file_name": str(item.get("file_name") or file_path.name),
                        "upload": FileUpload(
                            filename=file_path.name,
                            data=file_path.read_bytes(),
                        ),
                    }
                )

        if upload_sources or not fallback_to_content_markdown:
            return upload_sources

        content_path_value = str(portal_document.get("content_path") or "").strip()
        if not content_path_value:
            return upload_sources
        content_path = Path(content_path_value)
        if not content_path.is_file():
            raise ValidationError(f"Content markdown file does not exist: {content_path}")
        upload_sources.append(
            {
                "kind": "content_markdown",
                "path": str(content_path),
                "portal_file_id": str(detail_data.get("fdId") or portal_document.get("fdId") or "").strip() or None,
                "portal_file_name": content_path.name,
                "upload": FileUpload(
                    filename=content_path.name,
                    data=content_path.read_bytes(),
                    content_type="text/markdown",
                ),
            }
        )
        return upload_sources

    def _upload_sources(self, *, dataset_id: str, upload_sources: list[dict[str, Any]]) -> list[dict[str, str]]:
        response = self.client.upload_documents(
            dataset_id,
            [item["upload"] for item in upload_sources],
        )
        self._require_success_response(response, action="upload documents")

        if not isinstance(response.payload, dict):
            raise RagflowAPIError(
                "RAGFlow upload returned a non-JSON payload",
                status_code=502,
                payload={"raw": response.raw_text or ""},
            )
        data = response.payload.get("data")
        if not isinstance(data, list):
            raise RagflowAPIError(
                "RAGFlow upload returned an invalid data field",
                status_code=502,
                payload=response.payload,
            )
        if len(data) != len(upload_sources):
            raise RagflowAPIError(
                "RAGFlow upload response count does not match the uploaded file count",
                status_code=502,
                payload=response.payload,
            )

        uploaded_documents: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                raise RagflowAPIError(
                    "RAGFlow upload returned a non-object document entry",
                    status_code=502,
                    payload=response.payload,
                )
            document_id = str(item.get("id") or "").strip()
            if not document_id:
                raise RagflowAPIError(
                    "RAGFlow upload returned a document without an id",
                    status_code=502,
                    payload=response.payload,
                )
            uploaded_documents.append(
                {
                    "id": document_id,
                    "name": str(item.get("name") or item.get("location") or document_id),
                }
            )
        return uploaded_documents

    def _build_document_update_payload(
        self,
        *,
        base_update: dict[str, Any],
        detail_data: dict[str, Any],
        portal_document: dict[str, Any],
        upload_source: dict[str, Any],
    ) -> dict[str, Any]:
        payload = deepcopy(base_update)
        user_meta_fields = payload.get("meta_fields") or {}
        payload["meta_fields"] = {
            **self._build_knowledge_portal_meta_fields(
                detail_data=detail_data,
                portal_document=portal_document,
                upload_source=upload_source,
            ),
            **user_meta_fields,
        }
        return payload

    def _build_knowledge_portal_meta_fields(
        self,
        *,
        detail_data: dict[str, Any],
        portal_document: dict[str, Any],
        upload_source: dict[str, Any],
    ) -> dict[str, Any]:
        meta_fields = {
            "knowledge_portal_fd_id": str(detail_data.get("fdId") or portal_document.get("fdId") or "").strip(),
            "knowledge_portal_fd_no": str(detail_data.get("fdNo") or "").strip(),
            "knowledge_portal_fd_name": str(detail_data.get("fdName") or portal_document.get("fdName") or "").strip(),
            "knowledge_portal_fd_cate_id": str(detail_data.get("fdCateId") or "").strip(),
            "knowledge_portal_fd_publish_time": str(detail_data.get("fdPublishTime") or "").strip(),
            "knowledge_portal_fd_creator_no": str(detail_data.get("fdCreatorNo") or "").strip(),
            "knowledge_portal_fd_creator_name": str(detail_data.get("fdCreatorName") or "").strip(),
            "knowledge_portal_fd_link": str(detail_data.get("fdLink") or "").strip(),
            "knowledge_portal_file_kind": str(upload_source.get("kind") or "").strip(),
            "knowledge_portal_file_id": str(upload_source.get("portal_file_id") or "").strip(),
            "knowledge_portal_file_name": str(upload_source.get("portal_file_name") or "").strip(),
        }
        return {key: value for key, value in meta_fields.items() if value != ""}

    def _load_detail_data(self, portal_document: dict[str, Any]) -> dict[str, Any]:
        detail_json_path = str(portal_document.get("detail_json_path") or "").strip()
        if not detail_json_path:
            raise ValidationError("detail_json_path is required to prepare knowledge portal uploads")
        detail_payload = json.loads(Path(detail_json_path).read_text(encoding="utf-8"))
        if not isinstance(detail_payload, dict):
            raise ValidationError("detail_json_path must contain a JSON object")
        detail_data = detail_payload.get("data")
        if not isinstance(detail_data, dict):
            raise ValidationError("detail_json_path must contain a data object")
        return detail_data

    def _require_success_response(self, response: UpstreamResponse, *, action: str) -> None:
        if response.status_code >= 400:
            raise RagflowAPIError(
                f"RAGFlow {action} failed with HTTP {response.status_code}",
                status_code=response.status_code,
                payload=response.payload if isinstance(response.payload, dict) else {"raw": response.raw_text or ""},
            )
        if isinstance(response.payload, dict):
            code = response.payload.get("code")
            if code is not None and code != 0:
                raise RagflowAPIError(
                    f"RAGFlow {action} returned an unsuccessful business code",
                    status_code=502,
                    payload=response.payload,
                )

    def _build_ragflow_error(
        self,
        stage: str,
        *,
        fd_id: str,
        exc: RagflowAPIError,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        error = {
            "stage": f"ragflow_{stage}",
            "fdId": fd_id,
            "detail": str(exc),
        }
        if document_id:
            error["document_id"] = document_id
        if exc.payload:
            error["payload"] = exc.payload
        return error

    def _normalize_errors(self, errors: Any) -> list[dict[str, Any]]:
        if not isinstance(errors, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in errors:
            if isinstance(item, dict):
                normalized.append(item)
        return normalized

    def _serialize_upload_source(self, upload_source: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": upload_source.get("kind"),
            "path": upload_source.get("path"),
            "file_id": upload_source.get("portal_file_id"),
            "file_name": upload_source.get("portal_file_name"),
        }
