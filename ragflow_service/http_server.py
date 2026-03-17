from __future__ import annotations

import cgi
import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ENV_FILE, Settings, write_dotenv
from .document_service import RagflowDocumentService
from .exceptions import ConfigError, RagflowAPIError, ValidationError
from .ragflow_client import FileUpload, RagflowClient

STATIC_ROOT = Path(__file__).resolve().parent.parent / "frontend"
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def resolve_static_path(relative_path: str) -> Path:
    candidate = (STATIC_ROOT / relative_path).resolve()
    static_root = STATIC_ROOT.resolve()
    if static_root not in candidate.parents and candidate != static_root:
        raise ValidationError("Invalid static asset path.")
    return candidate


def read_static_asset(relative_path: str) -> tuple[bytes, str]:
    file_path = resolve_static_path(relative_path)
    if not file_path.is_file():
        raise FileNotFoundError(relative_path)
    content_type = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    return file_path.read_bytes(), content_type


class ServiceRuntime:
    def __init__(self, settings: Settings):
        self._lock = RLock()
        self._settings = settings
        self._service = self._build_service(settings)

    def get_service(self) -> RagflowDocumentService:
        with self._lock:
            if self._service is None:
                raise ConfigError(
                    "RAGFlow is not configured. Open the web UI or call /api/v1/settings to set "
                    "RAGFLOW_BASE_URL and RAGFLOW_API_KEY first."
                )
            return self._service

    def get_settings(self) -> Settings:
        with self._lock:
            return self._settings

    def update_settings(self, payload: dict[str, Any]) -> Settings:
        with self._lock:
            next_settings = Settings.from_payload(payload, fallback=self._settings)
            write_dotenv(ENV_FILE, next_settings.to_env_mapping())
            self._settings = next_settings
            self._service = self._build_service(next_settings)
            return self._settings

    def _build_service(self, settings: Settings) -> RagflowDocumentService:
        if not settings.is_ragflow_configured():
            return None
        return RagflowDocumentService(
            RagflowClient(
                base_url=settings.ragflow_base_url,
                api_key=settings.ragflow_api_key,
                timeout=settings.request_timeout,
            )
        )


def create_application(
    settings: Settings,
    service: RagflowDocumentService | None = None,
) -> ThreadingHTTPServer:
    runtime = ServiceRuntime(settings)
    if service is not None:
        runtime._service = service

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "RagflowService/1.0"

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            try:
                if method == "GET" and path in {"/", "/index.html"}:
                    self._serve_static("index.html")
                    return

                if method == "GET" and path.startswith("/static/"):
                    self._serve_static(path[len("/static/") :])
                    return

                if method == "GET" and path == "/api/v1/settings":
                    self._write_json(
                        HTTPStatus.OK,
                        {
                            "success": True,
                            "data": {
                                "settings": runtime.get_settings().to_payload(mask_secret=True),
                                "env_file": str(ENV_FILE),
                            },
                        },
                    )
                    return

                if method == "PUT" and path == "/api/v1/settings":
                    body = self._parse_json_body()
                    settings = runtime.update_settings(body)
                    self._write_json(
                        HTTPStatus.OK,
                        {
                            "success": True,
                            "data": {
                                "settings": settings.to_payload(mask_secret=True),
                                "message": (
                                    "RAGFlow client settings were applied to runtime. "
                                    "SERVICE_HOST and SERVICE_PORT will take effect after restart."
                                ),
                            },
                        },
                    )
                    return

                if method == "GET" and path == "/health":
                    service = runtime.get_service()
                    self._write_json(HTTPStatus.OK, {"success": True, "data": service.healthz()})
                    return

                if method == "GET":
                    match = re.fullmatch(r"/api/v1/datasets/([^/]+)/documents", path)
                    if match:
                        service = runtime.get_service()
                        query = self._normalize_query(parse_qs(parsed.query, keep_blank_values=False))
                        data = service.list_documents(match.group(1), query)
                        self._write_json(HTTPStatus.OK, {"success": True, "data": data})
                        return

                if method == "POST" and path == "/api/v1/documents/upload":
                    service = runtime.get_service()
                    payload = self._parse_upload_form()
                    data = service.upload_documents(**payload)
                    self._write_json(HTTPStatus.OK, {"success": True, "data": data})
                    return

                if method == "POST" and path == "/api/v1/retrieval":
                    service = runtime.get_service()
                    body = self._parse_json_body()
                    data = service.retrieve_chunks(
                        question=body.get("question", ""),
                        dataset_ids=body.get("dataset_ids"),
                        document_ids=body.get("document_ids"),
                        page=self._coerce_int(body.get("page"), default=1),
                        page_size=self._coerce_int(body.get("page_size"), default=30),
                        similarity_threshold=self._coerce_float(body.get("similarity_threshold")),
                        vector_similarity_weight=self._coerce_float(body.get("vector_similarity_weight")),
                        top_k=self._coerce_optional_int(body.get("top_k")),
                        rerank_id=body.get("rerank_id"),
                        keyword=self._coerce_bool(body.get("keyword"), default=False),
                        highlight=self._coerce_bool(body.get("highlight"), default=False),
                        cross_languages=body.get("cross_languages"),
                        use_kg=self._coerce_optional_bool(body.get("use_kg")),
                        metadata_condition=body.get("metadata_condition"),
                    )
                    self._write_json(HTTPStatus.OK, {"success": True, "data": data})
                    return

                if method == "PUT" and path == "/api/v1/documents/metadata":
                    service = runtime.get_service()
                    body = self._parse_json_body()
                    data = service.batch_update_document_metadata(
                        dataset_id=body.get("dataset_id", ""),
                        documents=body.get("documents", []),
                    )
                    self._write_json(HTTPStatus.OK, {"success": True, "data": data})
                    return

                if method == "PUT":
                    match = re.fullmatch(r"/api/v1/documents/([^/]+)/([^/]+)/metadata", path)
                    if match:
                        service = runtime.get_service()
                        body = self._parse_json_body()
                        data = service.update_document_metadata(
                            dataset_id=match.group(1),
                            document_id=match.group(2),
                            meta_fields=body.get("meta_fields", {}),
                            enabled=self._coerce_optional_int(body.get("enabled")),
                            name=body.get("name"),
                            chunk_method=body.get("chunk_method"),
                            parser_config=body.get("parser_config"),
                        )
                        self._write_json(HTTPStatus.OK, {"success": True, "data": data})
                        return

                self._write_json(
                    HTTPStatus.NOT_FOUND,
                    {"success": False, "error": {"message": f"Unknown route: {path}"}},
                )
            except ValidationError as exc:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"success": False, "error": {"message": str(exc)}},
                )
            except ConfigError as exc:
                self._write_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"success": False, "error": {"message": str(exc)}},
                )
            except RagflowAPIError as exc:
                self._write_json(
                    HTTPStatus.BAD_GATEWAY
                    if exc.status_code >= 500
                    else HTTPStatus(exc.status_code),
                    {
                        "success": False,
                        "error": {
                            "message": str(exc),
                            "ragflow_code": exc.code,
                            "ragflow_payload": exc.payload,
                        },
                    },
                )
            except json.JSONDecodeError as exc:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"success": False, "error": {"message": f"Invalid JSON body: {exc.msg}"}},
                )
            except Exception as exc:  # pragma: no cover
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"success": False, "error": {"message": str(exc)}},
                )

        def _serve_static(self, relative_path: str) -> None:
            try:
                payload, content_type = read_static_asset(relative_path)
            except FileNotFoundError:
                self._write_json(
                    HTTPStatus.NOT_FOUND,
                    {"success": False, "error": {"message": f"Static asset not found: {relative_path}"}},
                )
                return
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _parse_upload_form(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValidationError("`Content-Type` must be multipart/form-data.")

            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                },
                keep_blank_values=True,
            )

            files = []
            for item in form.list or []:
                if item.filename and item.name in {"files", "file"}:
                    files.append(
                        FileUpload(
                            filename=item.filename,
                            data=item.file.read(),
                            content_type=item.type,
                        )
                    )

            dataset_id = form.getfirst("dataset_id", "").strip()
            if not dataset_id:
                raise ValidationError("Multipart form must include `dataset_id`.")

            return {
                "dataset_id": dataset_id,
                "files": files,
                "shared_meta_fields": self._parse_json_field(form.getfirst("shared_meta_fields")),
                "per_file_meta_fields": self._parse_json_field(form.getfirst("per_file_meta_fields")),
                "parse_after_upload": self._coerce_bool(form.getfirst("parse_after_upload"), default=True),
                "enabled": self._coerce_optional_int(form.getfirst("enabled")),
                "chunk_method": self._empty_to_none(form.getfirst("chunk_method")),
                "parser_config": self._parse_json_field(form.getfirst("parser_config")),
            }

        def _parse_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValidationError("JSON request body must be an object.")
            return payload

        def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _normalize_query(self, query: dict[str, list[str]]) -> dict[str, Any]:
            normalized: dict[str, Any] = {}
            for key, values in query.items():
                if len(values) == 1:
                    normalized[key] = values[0]
                else:
                    normalized[key] = values
            return normalized

        def _parse_json_field(self, raw: str | None) -> dict[str, Any] | None:
            raw = self._empty_to_none(raw)
            if raw is None:
                return None
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValidationError("JSON form field must be an object.")
            return value

        def _coerce_bool(self, value: Any, *, default: bool) -> bool:
            if value in (None, ""):
                return default
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            raise ValidationError(f"Invalid boolean value: {value}")

        def _coerce_optional_bool(self, value: Any) -> bool | None:
            if value in (None, ""):
                return None
            return self._coerce_bool(value, default=False)

        def _coerce_int(self, value: Any, default: int | None = None) -> int:
            if value in (None, ""):
                if default is None:
                    raise ValidationError("Missing integer value.")
                return default
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"Invalid integer value: {value}") from exc

        def _coerce_optional_int(self, value: Any) -> int | None:
            if value in (None, ""):
                return None
            return self._coerce_int(value)

        def _coerce_float(self, value: Any) -> float | None:
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"Invalid numeric value: {value}") from exc

        def _empty_to_none(self, value: Any) -> str | None:
            if value is None:
                return None
            text = str(value).strip()
            return text or None

    return ThreadingHTTPServer((settings.server_host, settings.server_port), RequestHandler)


def serve() -> None:
    settings = Settings.from_env()
    server = create_application(settings)
    if settings.is_ragflow_configured():
        print(
            f"Listening on http://{settings.server_host}:{settings.server_port} "
            f"-> {settings.ragflow_base_url}"
        )
    else:
        print(
            f"Listening on http://{settings.server_host}:{settings.server_port} "
            "(RAGFlow not configured yet; open the UI to set it)"
        )
    server.serve_forever()
