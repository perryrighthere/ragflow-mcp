from __future__ import annotations

import base64
import json
import logging
import shlex
import socket
import sys
from typing import Any
from urllib import error, parse, request

from .exceptions import KnowledgePortalAPIError
from .ragflow_client import UpstreamResponse


LOGGER = logging.getLogger("ragflow_service")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


class KnowledgePortalClient:
    def __init__(
        self,
        *,
        base_url: str,
        community_id: str,
        username: str,
        password: str,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.community_id = community_id
        self.username = username
        self.password = password
        self.timeout = timeout

    def list_documents(
        self,
        *,
        page_no: int,
        page_size: int,
        doc_type: str = "mutildoc",
        fd_cate_id: str | None = None,
        begin_time: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "communityId": self.community_id,
            "type": doc_type,
            "pageno": page_no,
            "pagesize": page_size,
        }
        if fd_cate_id:
            payload["fdCateId"] = fd_cate_id
        if begin_time:
            payload["beginTime"] = begin_time
        upstream = self._request_form("POST", "/api/knowledge/webservice/getBillList", payload, accept="application/json")
        return self._require_success_json_payload(upstream, "文件列表接口")

    def get_document_detail(self, *, fd_id: str | None = None, fd_no: str | None = None) -> dict[str, Any]:
        payload = {"communityId": self.community_id}
        if fd_id:
            payload["fdId"] = fd_id
        if fd_no:
            payload["fdNo"] = fd_no
        upstream = self._request_form("POST", "/api/knowledge/webservice/getBillDetail", payload, accept="application/json")
        return self._require_success_json_payload(upstream, "文件详情接口")

    def download_attachment(self, *, file_id: str) -> UpstreamResponse:
        payload = {
            "communityId": self.community_id,
            "fdId": file_id,
        }
        return self._request_form(
            "POST",
            "/api/knowledge/webservice/getAttachment",
            payload,
            accept="*/*",
            expect_json=False,
        )

    def _request_form(
        self,
        method: str,
        path: str,
        form: dict[str, Any],
        *,
        accept: str,
        expect_json: bool = True,
    ) -> UpstreamResponse:
        body_text = parse.urlencode([(key, str(value)) for key, value in form.items() if value is not None])
        body = body_text.encode("utf-8")
        headers = {
            "Accept": accept,
            "Authorization": self._build_basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        url = f"{self.base_url}{path}"
        self._log_request(method, url, headers, body_text)

        req = request.Request(url=url, data=body, method=method.upper())
        for key, value in headers.items():
            req.add_header(key, value)

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                upstream = UpstreamResponse(
                    status_code=response.status,
                    payload=self._parse_payload(raw, expect_json=expect_json),
                    headers=self._extract_headers(response),
                    body_was_empty=not raw,
                    reason_phrase=self._extract_reason_phrase(response),
                    raw_text=self._decode_raw_text(raw),
                )
        except error.HTTPError as exc:
            raw = exc.read()
            upstream = UpstreamResponse(
                status_code=exc.code,
                payload=self._parse_payload(raw, expect_json=expect_json),
                headers=self._extract_headers(exc),
                body_was_empty=not raw,
                reason_phrase=self._extract_reason_phrase(exc),
                raw_text=self._decode_raw_text(raw),
            )
        except error.URLError as exc:
            raise KnowledgePortalAPIError(f"Unable to connect to Knowledge Portal: {exc.reason}", status_code=502) from exc
        except (ConnectionError, TimeoutError, OSError, socket.timeout) as exc:
            raise KnowledgePortalAPIError(f"Unable to connect to Knowledge Portal: {exc}", status_code=502) from exc

        self._log_response(upstream)
        if upstream.status_code >= 400:
            raise KnowledgePortalAPIError(
                f"Knowledge Portal request failed with HTTP {upstream.status_code}",
                status_code=upstream.status_code,
                payload=upstream.payload if isinstance(upstream.payload, dict) else {"raw": upstream.raw_text or ""},
            )
        return upstream

    def _require_success_json_payload(self, upstream: UpstreamResponse, api_name: str) -> dict[str, Any]:
        if not isinstance(upstream.payload, dict):
            raise KnowledgePortalAPIError(
                f"{api_name} returned a non-JSON payload",
                status_code=502,
                payload={"raw": upstream.raw_text or ""},
            )
        if upstream.payload.get("code") != 200:
            raise KnowledgePortalAPIError(
                f"{api_name} returned an unsuccessful business code",
                status_code=502,
                payload=upstream.payload,
            )
        return upstream.payload

    def _build_basic_auth_header(self) -> str:
        raw = f"{self.username}:{self.password}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('ascii')}"

    def _parse_payload(self, raw: bytes, *, expect_json: bool) -> Any:
        if not raw:
            return {}
        if not expect_json:
            return raw
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

    def _log_request(self, method: str, url: str, headers: dict[str, str], body_text: str) -> None:
        LOGGER.info("Knowledge Portal request -> %s %s", method.upper(), url)
        LOGGER.info("Knowledge Portal request headers -> %s", json.dumps(headers, ensure_ascii=False))
        LOGGER.info("Knowledge Portal request raw body -> %s", body_text)
        LOGGER.info("Knowledge Portal request curl -> %s", self._build_curl_command(method, url, headers, body_text))

    def _log_response(self, response: UpstreamResponse) -> None:
        LOGGER.info("Knowledge Portal response <- HTTP %s", response.status_code)
        if response.reason_phrase:
            LOGGER.info("Knowledge Portal response reason <- %s", response.reason_phrase)
        if response.body_was_empty:
            LOGGER.info("Knowledge Portal response payload <- <empty body>")
            return
        if isinstance(response.payload, (dict, list)):
            LOGGER.info("Knowledge Portal response payload <- %s", json.dumps(response.payload, ensure_ascii=False))
            return
        if isinstance(response.payload, bytes):
            LOGGER.info("Knowledge Portal response payload <- <binary %s bytes>", len(response.payload))
            return
        LOGGER.info("Knowledge Portal response payload <- %s", str(response.payload))

    def _build_curl_command(self, method: str, url: str, headers: dict[str, str], body_text: str) -> str:
        command = ["curl", "--request", method.upper(), "--url", url]
        for key, value in headers.items():
            command.extend(["--header", f"{key}: {value}"])
        command.extend(["--data-raw", body_text])
        return " ".join(shlex.quote(part) for part in command)
