from __future__ import annotations

import json
import logging
import mimetypes
import shlex
import socket
import sys
import uuid
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from .exceptions import RagflowAPIError


LOGGER = logging.getLogger("ragflow_service")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


@dataclass(frozen=True)
class FileUpload:
    filename: str
    data: bytes
    content_type: str | None = None


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    payload: Any
    headers: dict[str, str] | None = None
    body_was_empty: bool = False
    reason_phrase: str | None = None
    raw_text: str | None = None


class RagflowClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def healthz(self) -> UpstreamResponse:
        return self.request_json("GET", "/v1/system/healthz", use_auth=False)

    def retrieve_chunks(self, payload: dict[str, Any]) -> UpstreamResponse:
        return self.request_json("POST", "/api/v1/retrieval", json_body=payload)

    def list_documents(self, dataset_id: str, query: dict[str, Any] | None = None) -> UpstreamResponse:
        return self.request_json("GET", f"/api/v1/datasets/{dataset_id}/documents", query=query)

    def upload_documents(self, dataset_id: str, files: list[FileUpload]) -> UpstreamResponse:
        return self.request_multipart("POST", f"/api/v1/datasets/{dataset_id}/documents", files=files)

    def update_document(self, dataset_id: str, document_id: str, payload: dict[str, Any]) -> UpstreamResponse:
        return self.request_json(
            "PUT",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}",
            json_body=payload,
        )

    def parse_documents(self, dataset_id: str, payload: dict[str, Any]) -> UpstreamResponse:
        return self.request_json("POST", f"/api/v1/datasets/{dataset_id}/chunks", json_body=payload)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        query: dict[str, Any] | None = None,
        use_auth: bool = True,
    ) -> UpstreamResponse:
        body = None
        raw_body_text = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if json_body is not None:
            raw_body_text = json.dumps(json_body, ensure_ascii=False)
            body = raw_body_text.encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self._request(
            method,
            path,
            body=body,
            headers=headers,
            query=query,
            use_auth=use_auth,
            log_payload=json_body,
            raw_body_text=raw_body_text,
        )

    def request_multipart(
        self,
        method: str,
        path: str,
        *,
        files: list[FileUpload],
        query: dict[str, Any] | None = None,
        use_auth: bool = True,
    ) -> UpstreamResponse:
        body, content_type = self._encode_multipart(files)
        return self._request(
            method,
            path,
            body=body,
            headers={
                "Accept": "application/json",
                "Content-Type": content_type,
            },
            query=query,
            use_auth=use_auth,
            log_payload={"files": [file.filename for file in files]},
            files=files,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        use_auth: bool = True,
        log_payload: Any | None = None,
        raw_body_text: str | None = None,
        files: list[FileUpload] | None = None,
    ) -> UpstreamResponse:
        request_headers = headers.copy() if headers else {}
        if use_auth:
            request_headers["Authorization"] = f"Bearer {self.api_key}"

        url = self._build_url(path, query)
        self._log_request(
            method,
            url,
            request_headers,
            log_payload,
            use_auth,
            raw_body_text=raw_body_text,
            files=files,
        )

        req = request.Request(url=url, data=body, method=method.upper())
        for key, value in request_headers.items():
            req.add_header(key, value)

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                payload = self._parse_payload(raw)
                upstream = UpstreamResponse(
                    status_code=response.status,
                    payload=payload,
                    headers=self._extract_headers(response),
                    body_was_empty=not raw,
                    reason_phrase=self._extract_reason_phrase(response),
                    raw_text=self._decode_raw_text(raw),
                )
        except error.HTTPError as exc:
            raw = exc.read()
            payload = self._parse_payload(raw)
            upstream = UpstreamResponse(
                status_code=exc.code,
                payload=payload,
                headers=self._extract_headers(exc),
                body_was_empty=not raw,
                reason_phrase=self._extract_reason_phrase(exc),
                raw_text=self._decode_raw_text(raw),
            )
        except error.URLError as exc:
            raise RagflowAPIError(f"Unable to connect to RAGFlow: {exc.reason}", status_code=502) from exc
        except (ConnectionError, TimeoutError, OSError, socket.timeout) as exc:
            raise RagflowAPIError(f"Unable to connect to RAGFlow: {exc}", status_code=502) from exc

        self._log_response(upstream)
        return upstream

    def _build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if not query:
            return url

        flattened: list[tuple[str, str]] = []
        for key, value in query.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    flattened.append((key, str(item)))
            else:
                flattened.append((key, str(value)))

        if not flattened:
            return url
        return f"{url}?{parse.urlencode(flattened)}"

    def _parse_payload(self, raw: bytes) -> Any:
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

    def _decode_raw_text(self, raw: bytes) -> str | None:
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace")

    def _extract_headers(self, response: Any) -> dict[str, str]:
        headers = getattr(response, "headers", None)
        if headers is None:
            return {}
        try:
            return dict(headers.items())
        except AttributeError:
            return dict(headers)

    def _extract_reason_phrase(self, response: Any) -> str | None:
        reason = getattr(response, "reason", None)
        if reason is None:
            reason = getattr(response, "msg", None)
        if reason is None:
            return None
        return str(reason)

    def _log_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: Any,
        use_auth: bool,
        *,
        raw_body_text: str | None = None,
        files: list[FileUpload] | None = None,
    ) -> None:
        LOGGER.info("RAGFlow request -> %s %s auth=%s", method.upper(), url, "on" if use_auth else "off")
        LOGGER.info("RAGFlow request headers -> %s", self._render_log_payload(headers))
        if payload is not None:
            LOGGER.info("RAGFlow request payload -> %s", self._render_log_payload(payload))
        if raw_body_text is not None:
            LOGGER.info("RAGFlow request raw body -> %s", raw_body_text)
        elif files:
            file_payload = [
                {
                    "filename": file.filename,
                    "content_type": file.content_type or "application/octet-stream",
                    "size_bytes": len(file.data),
                }
                for file in files
            ]
            LOGGER.info("RAGFlow request files -> %s", self._render_log_payload(file_payload))
        LOGGER.info(
            "RAGFlow request curl -> %s",
            self._build_curl_command(method, url, headers, raw_body_text=raw_body_text, files=files),
        )

    def _log_response(self, response: UpstreamResponse) -> None:
        LOGGER.info("RAGFlow response <- HTTP %s", response.status_code)
        if response.reason_phrase:
            LOGGER.info("RAGFlow response reason <- %s", response.reason_phrase)
        if response.status_code >= 400 or response.body_was_empty:
            LOGGER.info("RAGFlow response headers <- %s", self._render_log_payload(response.headers or {}))
        if response.body_was_empty:
            LOGGER.info("RAGFlow response payload <- <empty body>")
            return
        if response.status_code >= 400:
            LOGGER.info("RAGFlow response raw body <- %s", response.raw_text or "<empty body>")
            if isinstance(response.payload, (dict, list)):
                LOGGER.info("RAGFlow response payload <- %s", self._render_log_payload(response.payload))
            return
        LOGGER.info("RAGFlow response payload <- %s", self._render_log_payload(response.payload))

    def _render_log_payload(self, payload: Any) -> str:
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, ensure_ascii=False)
        return str(payload)

    def _build_curl_command(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        raw_body_text: str | None = None,
        files: list[FileUpload] | None = None,
    ) -> str:
        command = ["curl", "--request", method.upper(), "--url", url]
        for key, value in headers.items():
            command.extend(["--header", f"{key}: {value}"])
        if raw_body_text is not None:
            command.extend(["--data-raw", raw_body_text])
        elif files:
            for file in files:
                form_value = f"file=@{file.filename}"
                if file.content_type:
                    form_value = f"{form_value};type={file.content_type}"
                command.extend(["--form", form_value])
        return " ".join(shlex.quote(part) for part in command)

    def _encode_multipart(self, files: list[FileUpload]) -> tuple[bytes, str]:
        boundary = f"ragflow-{uuid.uuid4().hex}"
        body = bytearray()

        for file in files:
            content_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                (
                    f'Content-Disposition: form-data; name="file"; filename="{file.filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8")
            )
            body.extend(file.data)
            body.extend(b"\r\n")

        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        return bytes(body), f"multipart/form-data; boundary={boundary}"
