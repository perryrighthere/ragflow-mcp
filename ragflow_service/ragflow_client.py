from __future__ import annotations

import json
import mimetypes
import uuid
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from .exceptions import RagflowAPIError


@dataclass(frozen=True)
class FileUpload:
    filename: str
    data: bytes
    content_type: str | None = None


class RagflowClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def upload_documents(self, dataset_id: str, files: list[FileUpload]) -> list[dict[str, Any]]:
        if not files:
            raise RagflowAPIError("At least one file is required for upload.", status_code=400)

        body, content_type = self._encode_multipart(files)
        return self._request_json(
            "POST",
            f"/api/v1/datasets/{dataset_id}/documents",
            body=body,
            headers={"Content-Type": content_type},
        )

    def update_document(self, dataset_id: str, document_id: str, payload: dict[str, Any]) -> Any:
        return self._request_json(
            "PUT",
            f"/api/v1/datasets/{dataset_id}/documents/{document_id}",
            json_body=payload,
        )

    def list_documents(self, dataset_id: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents",
            query=query,
        )

    def parse_documents(self, dataset_id: str, document_ids: list[str]) -> Any:
        return self._request_json(
            "POST",
            f"/api/v1/datasets/{dataset_id}/chunks",
            json_body={"document_ids": document_ids},
        )

    def retrieve_chunks(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "/api/v1/retrieval", json_body=payload)

    def healthz(self) -> dict[str, Any]:
        return self._request_json("GET", "/v1/system/healthz", use_auth=False)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        use_auth: bool = True,
    ) -> Any:
        response_body = self._request(
            method,
            path,
            json_body=json_body,
            body=body,
            headers=headers,
            query=query,
            use_auth=use_auth,
        )

        try:
            payload = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RagflowAPIError("RAGFlow returned invalid JSON.", status_code=502) from exc

        if isinstance(payload, dict) and "code" in payload:
            if payload.get("code") != 0:
                raise RagflowAPIError(
                    payload.get("message", "RAGFlow API request failed."),
                    status_code=502,
                    code=payload.get("code"),
                    payload=payload,
                )
            return payload.get("data")

        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        use_auth: bool = True,
    ) -> bytes:
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if use_auth:
            request_headers["Authorization"] = f"Bearer {self.api_key}"

        data = body
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        url = self._build_url(path, query)
        req = request.Request(url=url, data=data, method=method.upper())
        for key, value in request_headers.items():
            req.add_header(key, value)

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return response.read()
        except error.HTTPError as exc:
            raw = exc.read()
            message = self._extract_error_message(raw)
            raise RagflowAPIError(message, status_code=exc.code) from exc
        except error.URLError as exc:
            raise RagflowAPIError(f"Unable to connect to RAGFlow: {exc.reason}", status_code=502) from exc

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

    def _extract_error_message(self, raw: bytes) -> str:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace") or "RAGFlow API request failed."
        if isinstance(payload, dict):
            return payload.get("message") or payload.get("detail") or "RAGFlow API request failed."
        return "RAGFlow API request failed."

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

