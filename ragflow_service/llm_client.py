from __future__ import annotations

import json
import socket
from typing import Any, Iterator
from urllib import error, request

from .exceptions import LLMAPIError


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        raw = self._request_json("/chat/completions", payload)
        if not isinstance(raw, dict):
            raise LLMAPIError(
                "LLM response is not a JSON object.",
                status_code=502,
                payload={"raw_response": raw},
            )
        return raw

    def stream_chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url=f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                for chunk in self._iter_sse_payloads(response):
                    if not isinstance(chunk, dict):
                        raise LLMAPIError(
                            "LLM stream chunk is not a JSON object.",
                            status_code=502,
                            payload={"raw_response": chunk},
                        )
                    yield chunk
        except error.HTTPError as exc:
            raw = exc.read()
            payload = self._parse_payload(raw)
            raise LLMAPIError(
                f"LLM request failed with status {exc.code}.",
                status_code=exc.code,
                payload=payload if isinstance(payload, dict) else {"raw_response": payload},
            ) from exc
        except error.URLError as exc:
            raise LLMAPIError(f"Unable to connect to the LLM API: {exc.reason}", status_code=502) from exc
        except (ConnectionError, TimeoutError, OSError, socket.timeout) as exc:
            raise LLMAPIError(f"Unable to connect to the LLM API: {exc}", status_code=502) from exc

    def extract_message_content(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMAPIError("LLM response does not contain any choices.", status_code=502, payload=payload)

        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMAPIError("LLM response choice is not a JSON object.", status_code=502, payload=payload)

        message = choice.get("message")
        if isinstance(message, dict):
            content = self._content_to_text(message.get("content"))
            if content:
                return content

        content = self._content_to_text(choice.get("text"))
        if content:
            return content

        raise LLMAPIError("LLM response does not contain assistant text.", status_code=502, payload=payload)

    def extract_stream_delta(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return ""

        for choice in choices:
            if not isinstance(choice, dict):
                continue

            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = self._delta_content_to_text(delta.get("content"))
                if content:
                    return content

            content = self._delta_content_to_text(choice.get("text"))
            if content:
                return content

        return ""

    def _request_json(self, path: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            url=f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return self._parse_payload(response.read())
        except error.HTTPError as exc:
            raw = exc.read()
            payload = self._parse_payload(raw)
            raise LLMAPIError(
                f"LLM request failed with status {exc.code}.",
                status_code=exc.code,
                payload=payload if isinstance(payload, dict) else {"raw_response": payload},
            ) from exc
        except error.URLError as exc:
            raise LLMAPIError(f"Unable to connect to the LLM API: {exc.reason}", status_code=502) from exc
        except (ConnectionError, TimeoutError, OSError, socket.timeout) as exc:
            raise LLMAPIError(f"Unable to connect to the LLM API: {exc}", status_code=502) from exc

    def _parse_payload(self, raw: bytes) -> Any:
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

    def _iter_sse_payloads(self, response: Any) -> Iterator[Any]:
        data_lines: list[str] = []

        while True:
            raw_line = response.readline()
            if not raw_line:
                break

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                if not data_lines:
                    continue

                payload_text = "\n".join(data_lines)
                data_lines = []
                if payload_text == "[DONE]":
                    break
                yield self._parse_payload(payload_text.encode("utf-8"))
                continue

            if line.startswith(":"):
                continue

            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if data_lines:
            payload_text = "\n".join(data_lines)
            if payload_text != "[DONE]":
                yield self._parse_payload(payload_text.encode("utf-8"))

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    item_text = item.strip()
                elif isinstance(item, dict):
                    item_text = str(item.get("text", "")).strip()
                else:
                    item_text = ""
                if item_text:
                    parts.append(item_text)
            return "\n".join(parts).strip()

        return ""

    def _delta_content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    item_text = item
                elif isinstance(item, dict):
                    text = item.get("text")
                    item_text = str(text) if text is not None else ""
                else:
                    item_text = ""
                if item_text:
                    parts.append(item_text)
            return "".join(parts)

        return ""
