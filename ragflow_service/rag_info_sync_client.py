from __future__ import annotations

import json
import logging
import shlex
import socket
import sys
from typing import Any
from urllib import error, request

from .exceptions import RagInfoSyncError
from .ragflow_client import UpstreamResponse


LOGGER = logging.getLogger("ragflow_service")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


class RagInfoSyncClient:
    def __init__(self, url: str, timeout: float = 60.0):
        self.url = url
        self.timeout = timeout

    def sync_rag_info(self, payload: dict[str, str]) -> UpstreamResponse:
        body_text = json.dumps(payload, ensure_ascii=False)
        body = body_text.encode("utf-8")
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
        }
        self._log_request("POST", self.url, headers, payload)

        req = request.Request(url=self.url, data=body, method="POST")
        for key, value in headers.items():
            req.add_header(key, value)

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                upstream = UpstreamResponse(
                    status_code=response.status,
                    payload=self._parse_payload(raw),
                    headers=self._extract_headers(response),
                    body_was_empty=not raw,
                    reason_phrase=self._extract_reason_phrase(response),
                    raw_text=self._decode_raw_text(raw),
                )
        except error.HTTPError as exc:
            raw = exc.read()
            payload_data = self._parse_payload(raw)
            raise RagInfoSyncError(
                f"RAG info sync failed with HTTP {exc.code}",
                status_code=exc.code,
                payload=payload_data if isinstance(payload_data, dict) else {"raw": self._decode_raw_text(raw) or ""},
            ) from exc
        except error.URLError as exc:
            raise RagInfoSyncError(f"Unable to connect to RAG info sync service: {exc.reason}", status_code=502) from exc
        except (ConnectionError, TimeoutError, OSError, socket.timeout) as exc:
            raise RagInfoSyncError(f"Unable to connect to RAG info sync service: {exc}", status_code=502) from exc

        self._log_response(upstream)
        if upstream.status_code >= 400:
            raise RagInfoSyncError(
                f"RAG info sync failed with HTTP {upstream.status_code}",
                status_code=upstream.status_code,
                payload=upstream.payload if isinstance(upstream.payload, dict) else {"raw": upstream.raw_text or ""},
            )
        return upstream

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

    def _log_request(self, method: str, url: str, headers: dict[str, str], payload: dict[str, str]) -> None:
        redacted_payload = self._redact_payload(payload)
        LOGGER.info("RAG info sync request -> %s %s", method.upper(), url)
        LOGGER.info("RAG info sync request headers -> %s", json.dumps(headers, ensure_ascii=False))
        LOGGER.info("RAG info sync request payload -> %s", json.dumps(redacted_payload, ensure_ascii=False))
        LOGGER.info(
            "RAG info sync request curl -> %s",
            self._build_curl_command(method, url, headers, redacted_payload),
        )

    def _log_response(self, response: UpstreamResponse) -> None:
        LOGGER.info("RAG info sync response <- HTTP %s", response.status_code)
        if response.reason_phrase:
            LOGGER.info("RAG info sync response reason <- %s", response.reason_phrase)
        if response.body_was_empty:
            LOGGER.info("RAG info sync response payload <- <empty body>")
            return
        if isinstance(response.payload, (dict, list)):
            LOGGER.info("RAG info sync response payload <- %s", json.dumps(response.payload, ensure_ascii=False))
            return
        LOGGER.info("RAG info sync response payload <- %s", str(response.payload))

    def _build_curl_command(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, str],
    ) -> str:
        command = ["curl", "--request", method.upper(), "--url", url]
        for key, value in headers.items():
            command.extend(["--header", f"{key}: {value}"])
        command.extend(["--data-raw", json.dumps(payload, ensure_ascii=False)])
        return " ".join(shlex.quote(part) for part in command)

    def _redact_payload(self, payload: dict[str, str]) -> dict[str, str]:
        return {
            **payload,
            "tenantId": "<redacted>" if payload.get("tenantId") else "",
        }
