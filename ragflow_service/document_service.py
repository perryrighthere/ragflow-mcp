from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .exceptions import RagInfoSyncError, RagflowAPIError, ValidationError
from .knowledge_portal_service import KnowledgePortalSyncService
from .rag_info_sync_client import RagInfoSyncClient
from .ragflow_client import LOGGER, FileUpload, RagflowClient, UpstreamResponse


PRESENTATION_FILE_EXTENSIONS = {".pptx"}


class RagflowDocumentService:
    def __init__(
        self,
        client: RagflowClient,
        knowledge_portal_service: KnowledgePortalSyncService,
        *,
        rag_info_sync_client: RagInfoSyncClient | None = None,
        tenant_id: str | None = None,
    ):
        self.client = client
        self.knowledge_portal_service = knowledge_portal_service
        self.rag_info_sync_client = rag_info_sync_client
        self.tenant_id = tenant_id if tenant_id is not None else str(getattr(client, "api_key", "") or "")

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
        should_stop_sync = None
        if normalized["max_download_files"] is not None and not normalized["fallback_to_content_markdown"]:
            def should_stop_sync(summary: dict[str, Any]) -> bool:
                return bool(summary.get("download_limit_reached"))

        sync_result, synced_documents = self.knowledge_portal_service.start_sync(
            sync_payload,
            collect_documents=False,
            should_stop=should_stop_sync,
        )

        imported_documents: list[dict[str, Any]] = []
        skipped_documents: list[dict[str, Any]] = []
        errors_raw = sync_result.get("errors")
        if isinstance(errors_raw, list):
            errors = errors_raw
        else:
            errors = []
            sync_result["errors"] = errors
        parse_document_ids: list[str] = []
        parse_candidates: list[dict[str, Any]] = []
        uploaded_file_count = 0
        updated_document_count = 0

        for portal_document in synced_documents:
            fd_id = str(portal_document.get("fdId") or "").strip()
            fd_name = str(portal_document.get("fdName") or "").strip()
            try:
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
                        dataset_id=normalized["dataset_id"],
                        document_id=uploaded_document["id"],
                        detail_data=detail_data,
                        portal_document=portal_document,
                        upload_source=upload_source,
                        uploaded_document=uploaded_document,
                    )
                    rag_info_payload = self._build_rag_info_payload(
                        dataset_id=normalized["dataset_id"],
                        document_id=uploaded_document["id"],
                        fd_id=fd_id,
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
                        parse_candidates.append(
                            {
                                "document_id": uploaded_document["id"],
                                "update_payload": update_payload,
                                "upload_source": upload_source,
                                "uploaded_document": uploaded_document,
                                "update_succeeded": False,
                            }
                        )
                        continue

                    updated_document_count += 1
                    try:
                        self._sync_rag_info(rag_info_payload)
                    except RagInfoSyncError as exc:
                        errors.append(
                            self._build_rag_info_sync_error(
                                fd_id=fd_id,
                                document_id=uploaded_document["id"],
                                exc=exc,
                            )
                        )
                    parse_group = self._build_parse_group(
                        update_payload,
                        upload_source=upload_source,
                        uploaded_document=uploaded_document,
                    )
                    LOGGER.info(
                        "RAGFlow import document classified -> document_id=%s name=%s chunk_method=%s parse_group=%s upload_source=%s",
                        uploaded_document["id"],
                        uploaded_document["name"],
                        update_payload.get("chunk_method"),
                        parse_group,
                        self._serialize_upload_source(upload_source),
                    )
                    parse_document_ids.append(uploaded_document["id"])
                    parse_candidates.append(
                        {
                            "document_id": uploaded_document["id"],
                            "update_payload": update_payload,
                            "upload_source": upload_source,
                            "uploaded_document": uploaded_document,
                            "update_succeeded": True,
                        }
                    )
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
            finally:
                try:
                    self.knowledge_portal_service.cleanup_document_cache(portal_document)
                except OSError as exc:
                    errors.append(
                        {
                            "stage": "cleanup_cache",
                            "fdId": fd_id,
                            "detail": str(exc),
                        }
                    )

        parse_result = None
        if normalized["parse_after_upload"] and parse_candidates:
            parse_results: list[dict[str, Any]] = []
            parse_document_groups = self._build_parse_document_groups(
                normalized["dataset_id"],
                parse_candidates,
                errors,
            )
            parse_document_ids = parse_document_groups["default"] + parse_document_groups["presentation"]
            for group_name in ("default", "presentation"):
                group_document_ids = parse_document_groups[group_name]
                if not group_document_ids:
                    continue
                try:
                    LOGGER.info(
                        "RAGFlow parse batch -> group=%s count=%s document_ids=%s",
                        group_name,
                        len(group_document_ids),
                        group_document_ids,
                    )
                    parse_response = self.client.parse_documents(
                        normalized["dataset_id"],
                        {"document_ids": group_document_ids},
                    )
                    self._require_success_response(parse_response, action=f"parse {group_name} documents")
                    parse_results.append(
                        {
                            "group": group_name,
                            "document_ids": group_document_ids,
                            "response": parse_response.payload,
                        }
                    )
                except RagflowAPIError as exc:
                    errors.append(self._build_ragflow_error("parse", fd_id="", exc=exc))
            if len(parse_results) == 1:
                parse_result = parse_results[0]["response"]
            elif parse_results:
                parse_result = {"batches": parse_results}

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

        if "parser_confiog" in document_update and "parser_config" not in document_update:
            raise ValidationError("document_update.parser_confiog is not supported; did you mean parser_config?")

        meta_fields = document_update.get("meta_fields")
        if meta_fields is not None and not isinstance(meta_fields, dict):
            raise ValidationError("document_update.meta_fields must be an object")
        parser_config = document_update.get("parser_config")
        if parser_config is not None and not isinstance(parser_config, dict):
            raise ValidationError("document_update.parser_config must be an object")

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
                    "location": str(item.get("location") or ""),
                    "type": str(item.get("type") or ""),
                }
            )
        return uploaded_documents

    def _build_document_update_payload(
        self,
        *,
        base_update: dict[str, Any],
        dataset_id: str,
        document_id: str,
        detail_data: dict[str, Any],
        portal_document: dict[str, Any],
        upload_source: dict[str, Any],
        uploaded_document: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = deepcopy(base_update)
        user_meta_fields = payload.get("meta_fields") or {}
        fd_id = str(detail_data.get("fdId") or portal_document.get("fdId") or "").strip()
        payload["meta_fields"] = {
            **self._build_knowledge_portal_meta_fields(
                detail_data=detail_data,
                portal_document=portal_document,
                upload_source=upload_source,
            ),
            **user_meta_fields,
            **self._build_rag_info_payload(
                dataset_id=dataset_id,
                document_id=document_id,
                fd_id=fd_id,
            ),
        }
        if self._is_presentation_upload(upload_source, uploaded_document=uploaded_document):
            payload["chunk_method"] = "presentation"
            payload["parser_config"] = self._build_presentation_parser_config(payload.get("parser_config"))
        return payload

    def _build_presentation_parser_config(self, parser_config: Any) -> dict[str, Any]:
        if not isinstance(parser_config, dict):
            return {"raptor": {"use_raptor": False}}
        raptor = parser_config.get("raptor")
        if isinstance(raptor, dict):
            return {"raptor": deepcopy(raptor)}
        return {"raptor": {"use_raptor": False}}

    def _build_parse_document_groups(
        self,
        dataset_id: str,
        parse_candidates: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {"default": [], "presentation": []}
        for candidate in parse_candidates:
            document_id = str(candidate["document_id"])
            update_payload = candidate["update_payload"]
            upload_source = candidate["upload_source"]
            uploaded_document = candidate["uploaded_document"]
            ragflow_document = self._load_ragflow_document_for_parse(dataset_id, document_id, errors)
            parse_group = self._build_parse_group(
                update_payload,
                upload_source=upload_source,
                uploaded_document=uploaded_document,
                ragflow_document=ragflow_document,
            )
            update_succeeded = bool(candidate.get("update_succeeded", True))
            if not update_succeeded and parse_group != "presentation":
                LOGGER.info(
                    "RAGFlow parse candidate skipped after update failure -> document_id=%s parse_group=%s",
                    document_id,
                    parse_group,
                )
                continue
            if parse_group == "presentation":
                self._ensure_presentation_document_config(
                    dataset_id=dataset_id,
                    document_id=document_id,
                    update_payload=update_payload,
                    ragflow_document=ragflow_document,
                    errors=errors,
                )
            LOGGER.info(
                "RAGFlow parse candidate classified -> document_id=%s upload_name=%s ragflow_name=%s ragflow_location=%s ragflow_type=%s ragflow_chunk_method=%s parse_group=%s",
                document_id,
                uploaded_document.get("name"),
                (ragflow_document or {}).get("name"),
                (ragflow_document or {}).get("location"),
                (ragflow_document or {}).get("type"),
                (ragflow_document or {}).get("chunk_method"),
                parse_group,
            )
            groups[parse_group].append(document_id)
        return groups

    def _load_ragflow_document_for_parse(
        self,
        dataset_id: str,
        document_id: str,
        errors: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        try:
            response = self.client.list_documents(dataset_id, {"id": document_id})
            self._require_success_response(response, action=f"list document {document_id}")
        except RagflowAPIError as exc:
            errors.append(self._build_ragflow_error("list_document", fd_id="", exc=exc, document_id=document_id))
            return None
        document = self._extract_listed_document(response.payload, document_id)
        if document is None:
            LOGGER.info("RAGFlow listed document not found -> document_id=%s", document_id)
        return document

    def _extract_listed_document(self, payload: Any, document_id: str) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        docs = None
        if isinstance(data, dict):
            docs = data.get("docs")
        elif isinstance(data, list):
            docs = data
        if not isinstance(docs, list):
            return None
        for item in docs:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").strip() == document_id:
                return {
                    "id": document_id,
                    "name": str(item.get("name") or ""),
                    "location": str(item.get("location") or ""),
                    "type": str(item.get("type") or ""),
                    "chunk_method": str(item.get("chunk_method") or ""),
                    "parser_config": item.get("parser_config") if isinstance(item.get("parser_config"), dict) else {},
                    "run": str(item.get("run") or ""),
                }
        return None

    def _ensure_presentation_document_config(
        self,
        *,
        dataset_id: str,
        document_id: str,
        update_payload: dict[str, Any],
        ragflow_document: dict[str, Any] | None,
        errors: list[dict[str, Any]],
    ) -> None:
        current_method = str((ragflow_document or {}).get("chunk_method") or update_payload.get("chunk_method") or "")
        if current_method.strip().lower() == "presentation":
            return
        presentation_payload = deepcopy(update_payload)
        presentation_payload["chunk_method"] = "presentation"
        presentation_payload["parser_config"] = self._build_presentation_parser_config(
            presentation_payload.get("parser_config")
        )
        try:
            response = self.client.update_document(dataset_id, document_id, presentation_payload)
            self._require_success_response(response, action=f"ensure presentation document {document_id}")
            update_payload.clear()
            update_payload.update(presentation_payload)
            LOGGER.info("RAGFlow presentation config ensured -> document_id=%s", document_id)
        except RagflowAPIError as exc:
            errors.append(self._build_ragflow_error("ensure_presentation", fd_id="", exc=exc, document_id=document_id))

    def _build_parse_group(
        self,
        update_payload: dict[str, Any],
        *,
        upload_source: dict[str, Any] | None = None,
        uploaded_document: dict[str, str] | None = None,
        ragflow_document: dict[str, Any] | None = None,
    ) -> str:
        chunk_method = str(
            update_payload.get("chunk_method") or (ragflow_document or {}).get("chunk_method") or ""
        ).strip().lower()
        if chunk_method == "presentation":
            return "presentation"
        if upload_source is not None and self._is_presentation_upload(
            upload_source,
            uploaded_document=uploaded_document,
            ragflow_document=ragflow_document,
        ):
            return "presentation"
        return "default"

    def _build_rag_info_payload(self, *, dataset_id: str, document_id: str, fd_id: str) -> dict[str, str]:
        return {
            "knowledgeDatabaseId": dataset_id,
            "ragFileId": document_id,
            "originFileId": fd_id,
            "tenantId": self.tenant_id,
        }

    def _sync_rag_info(self, payload: dict[str, str]) -> UpstreamResponse | None:
        if self.rag_info_sync_client is None:
            return None
        response = self.rag_info_sync_client.sync_rag_info(payload)
        if response.status_code >= 400:
            raise RagInfoSyncError(
                f"RAG info sync failed with HTTP {response.status_code}",
                status_code=response.status_code,
                payload=response.payload if isinstance(response.payload, dict) else {"raw": response.raw_text or ""},
            )
        return response

    def _is_presentation_upload(
        self,
        upload_source: dict[str, Any],
        *,
        uploaded_document: dict[str, str] | None = None,
        ragflow_document: dict[str, Any] | None = None,
    ) -> bool:
        names = [
            str(upload_source.get("portal_file_name") or ""),
            str(upload_source.get("path") or ""),
        ]
        upload = upload_source.get("upload")
        if isinstance(upload, FileUpload):
            names.append(upload.filename)
        if uploaded_document is not None:
            names.extend(str(uploaded_document.get(key) or "") for key in ("name", "location"))
            document_type = str(uploaded_document.get("type") or "").strip().lower()
            if document_type in {"ppt", "pptx", "presentation"}:
                return True
        if ragflow_document is not None:
            names.extend(str(ragflow_document.get(key) or "") for key in ("name", "location"))
            document_type = str(ragflow_document.get("type") or "").strip().lower()
            if document_type in {"ppt", "pptx", "presentation"}:
                return True
        return any(Path(name).suffix.lower() in PRESENTATION_FILE_EXTENSIONS for name in names if name)

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

    def _build_rag_info_sync_error(
        self,
        *,
        fd_id: str,
        document_id: str,
        exc: RagInfoSyncError,
    ) -> dict[str, Any]:
        error = {
            "stage": "rag_info_sync",
            "fdId": fd_id,
            "document_id": document_id,
            "detail": str(exc),
        }
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
