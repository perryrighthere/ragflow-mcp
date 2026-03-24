from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .exceptions import KnowledgePortalAPIError, ValidationError
from .knowledge_portal_client import KnowledgePortalClient


def _default_output_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "output" / "attachments"


class KnowledgePortalSyncService:
    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        default_timeout: float = 60.0,
        client_factory: Callable[..., KnowledgePortalClient] | None = None,
    ):
        self.output_dir = output_dir or _default_output_dir()
        self.default_timeout = default_timeout
        self.client_factory = client_factory or KnowledgePortalClient

    def sync_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._validate_payload(payload)
        client = self.client_factory(
            base_url=normalized["base_url"],
            community_id=normalized["community_id"],
            username=normalized["username"],
            password=normalized["password"],
            timeout=normalized["timeout"],
        )

        list_items = self._collect_all_documents(
            client,
            doc_type=normalized["type"],
            page_size=normalized["page_size"],
            fd_cate_id=normalized["fd_cate_id"],
            begin_time=normalized["begin_time"],
        )

        target_root = self.output_dir
        target_root.mkdir(parents=True, exist_ok=True)

        downloaded_files = 0
        documents: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        remaining_download_files = normalized["max_download_files"]
        download_limit_reached = False

        for item in list_items:
            if remaining_download_files == 0:
                download_limit_reached = True
                break

            fd_id = str(item.get("fdId") or "").strip()
            if not fd_id:
                errors.append({"stage": "detail", "detail": "Document item is missing fdId", "item": item})
                continue

            try:
                detail_response = client.get_document_detail(fd_id=fd_id)
                detail_data = detail_response.get("data")
                if not isinstance(detail_data, dict):
                    raise KnowledgePortalAPIError(
                        "文件详情接口返回的 data 不是对象",
                        status_code=502,
                        payload={"document_id": fd_id, "response": detail_response},
                    )

                document_dir = self._build_document_dir(target_root, fd_id, str(detail_data.get("fdName") or item.get("fdName") or "document"))
                document_dir.mkdir(parents=True, exist_ok=True)

                detail_path = document_dir / "detail.json"
                detail_path.write_text(json.dumps(detail_response, ensure_ascii=False, indent=2), encoding="utf-8")

                content_path = None
                content_text = detail_data.get("fdContent")
                if isinstance(content_text, str) and content_text.strip():
                    content_path = document_dir / "content.md"
                    content_path.write_text(self._render_content_markdown(detail_data), encoding="utf-8")

                downloaded = self._download_document_files(
                    client,
                    detail_data,
                    document_dir,
                    max_files=remaining_download_files,
                )
                downloaded_files += len(downloaded)
                if remaining_download_files is not None:
                    remaining_download_files -= len(downloaded)
                    if remaining_download_files == 0:
                        download_limit_reached = True

                documents.append(
                    {
                        "fdId": fd_id,
                        "fdName": detail_data.get("fdName") or item.get("fdName") or "",
                        "saved_dir": str(document_dir),
                        "detail_json_path": str(detail_path),
                        "content_path": str(content_path) if content_path is not None else None,
                        "downloaded_files": downloaded,
                    }
                )
            except KnowledgePortalAPIError as exc:
                errors.append(
                    {
                        "stage": "detail_or_download",
                        "fdId": fd_id,
                        "detail": str(exc),
                        "payload": exc.payload,
                    }
                )

        return {
            "base_url": normalized["base_url"],
            "output_dir": str(target_root),
            "total_documents": len(list_items),
            "downloaded_document_count": len(documents),
            "downloaded_file_count": downloaded_files,
            "max_download_files": normalized["max_download_files"],
            "download_limit_reached": download_limit_reached,
            "documents": documents,
            "errors": errors,
        }

    def _collect_all_documents(
        self,
        client: KnowledgePortalClient,
        *,
        doc_type: str,
        page_size: int,
        fd_cate_id: str | None,
        begin_time: str | None,
    ) -> list[dict[str, Any]]:
        page_no = 1
        seen_ids: set[str] = set()
        documents: list[dict[str, Any]] = []
        total_rows: int | None = None

        while True:
            response = client.list_documents(
                page_no=page_no,
                page_size=page_size,
                doc_type=doc_type,
                fd_cate_id=fd_cate_id,
                begin_time=begin_time,
            )
            data = response.get("data")
            if not isinstance(data, dict):
                raise KnowledgePortalAPIError(
                    "文件列表接口返回的 data 不是对象",
                    status_code=502,
                    payload={"response": response},
                )

            items = data.get("data") or []
            if not isinstance(items, list):
                raise KnowledgePortalAPIError(
                    "文件列表接口返回的 data.data 不是数组",
                    status_code=502,
                    payload={"response": response},
                )

            total_rows_value = data.get("totalRows")
            if isinstance(total_rows_value, int):
                total_rows = total_rows_value

            for item in items:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("fdId") or item.get("fdNo") or "")
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                documents.append(item)

            if not items:
                break
            if total_rows is not None and len(documents) >= total_rows:
                break
            if len(items) < page_size:
                break
            page_no += 1

        return documents

    def _download_document_files(
        self,
        client: KnowledgePortalClient,
        detail_data: dict[str, Any],
        document_dir: Path,
        *,
        max_files: int | None = None,
    ) -> list[dict[str, Any]]:
        downloads_dir = document_dir / "attachments"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        saved_files: list[dict[str, Any]] = []
        used_names: set[str] = set()
        seen_file_ids: set[str] = set()

        for target in self._iter_download_targets(detail_data):
            if max_files is not None and len(saved_files) >= max_files:
                break
            file_id = target["file_id"]
            if file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)

            upstream = client.download_attachment(file_id=file_id)
            if not isinstance(upstream.payload, (bytes, bytearray)):
                raise KnowledgePortalAPIError(
                    "附件接口未返回二进制内容",
                    status_code=502,
                    payload={"file_id": file_id, "payload": upstream.payload},
                )

            raw_name = target["file_name"] or self._extract_filename_from_headers(upstream.headers or {}) or file_id
            filename = self._uniquify_filename(self._sanitize_filename(raw_name), used_names)
            file_path = downloads_dir / filename
            file_path.write_bytes(bytes(upstream.payload))
            saved_files.append(
                {
                    "kind": target["kind"],
                    "file_id": file_id,
                    "file_name": filename,
                    "path": str(file_path),
                    "size_bytes": file_path.stat().st_size,
                }
            )

        return saved_files

    def _iter_download_targets(self, detail_data: dict[str, Any]) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []

        cover = detail_data.get("fdCoverImg")
        if isinstance(cover, dict):
            file_id = str(cover.get("fileId") or "").strip()
            if file_id:
                targets.append(
                    {
                        "kind": "cover",
                        "file_id": file_id,
                        "file_name": str(cover.get("fileName") or ""),
                    }
                )

        files = detail_data.get("fdFile")
        if isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                file_id = str(item.get("fileId") or "").strip()
                if not file_id:
                    continue
                targets.append(
                    {
                        "kind": "attachment",
                        "file_id": file_id,
                        "file_name": str(item.get("fileName") or ""),
                    }
                )

        return targets

    def _render_content_markdown(self, detail_data: dict[str, Any]) -> str:
        title = str(detail_data.get("fdName") or "Untitled Document")
        content = str(detail_data.get("fdContent") or "").strip()
        return f"# {title}\n\n{content}\n"

    def _build_document_dir(self, output_root: Path, fd_id: str, fd_name: str) -> Path:
        slug = self._slugify(fd_name)
        if slug:
            return output_root / f"{fd_id}_{slug}"
        return output_root / fd_id

    def _validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = str(payload.get("base_url") or "").strip()
        community_id = str(payload.get("community_id") or "").strip()
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "").strip()
        begin_time = str(payload.get("begin_time") or "").strip() or None
        fd_cate_id = str(payload.get("fd_cate_id") or "").strip() or None
        doc_type = str(payload.get("type") or "mutildoc").strip() or "mutildoc"

        if not base_url:
            raise ValidationError("base_url is required")
        if "://" not in base_url:
            base_url = f"https://{base_url}"
        if not community_id:
            raise ValidationError("community_id is required")
        if not username:
            raise ValidationError("username is required")
        if not password:
            raise ValidationError("password is required")

        page_size_raw = payload.get("page_size", 100)
        max_download_files_raw = payload.get("max_download_files")
        timeout_raw = payload.get("timeout", self.default_timeout)
        try:
            page_size = int(page_size_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("page_size must be an integer") from exc
        if page_size <= 0:
            raise ValidationError("page_size must be greater than 0")

        max_download_files = None
        if max_download_files_raw is not None:
            try:
                max_download_files = int(max_download_files_raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError("max_download_files must be an integer") from exc
            if max_download_files <= 0:
                raise ValidationError("max_download_files must be greater than 0")

        try:
            timeout = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("timeout must be a number") from exc
        if timeout <= 0:
            raise ValidationError("timeout must be greater than 0")

        return {
            "base_url": base_url.rstrip("/"),
            "community_id": community_id,
            "username": username,
            "password": password,
            "type": doc_type,
            "page_size": page_size,
            "max_download_files": max_download_files,
            "begin_time": begin_time,
            "fd_cate_id": fd_cate_id,
            "timeout": timeout,
        }

    def _extract_filename_from_headers(self, headers: dict[str, str]) -> str | None:
        content_disposition = headers.get("Content-Disposition") or headers.get("content-disposition")
        if not content_disposition:
            return None
        matches = re.findall(r"filename\\*?=(?:UTF-8''|\"?)([^\";]+)", content_disposition)
        if not matches:
            return None
        return matches[-1].strip()

    def _sanitize_filename(self, filename: str) -> str:
        cleaned = filename.strip() or "attachment.bin"
        cleaned = re.sub(r"[\\\\/:*?\"<>|]+", "_", cleaned)
        return cleaned[:180]

    def _uniquify_filename(self, filename: str, used_names: set[str]) -> str:
        candidate = filename
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        index = 1
        while candidate in used_names:
            candidate = f"{stem}_{index}{suffix}"
            index += 1
        used_names.add(candidate)
        return candidate

    def _slugify(self, value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        text = re.sub(r"\s+", "_", text)
        text = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text)
        return text[:80].strip("_")
