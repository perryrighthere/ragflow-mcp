from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import ConfigError

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


@dataclass(frozen=True)
class Settings:
    ragflow_base_url: str = ""
    ragflow_api_key: str = ""
    request_timeout: float = 60.0
    server_host: str = "0.0.0.0"
    server_port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        file_values = _load_dotenv(ENV_FILE)
        return cls.from_sources(file_values, require_ragflow=False)

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        fallback: "Settings | None" = None,
    ) -> "Settings":
        fallback = fallback or cls.from_env()
        values = {
            "RAGFLOW_BASE_URL": str(payload.get("ragflow_base_url") or fallback.ragflow_base_url),
            "RAGFLOW_API_KEY": str(payload.get("ragflow_api_key") or fallback.ragflow_api_key),
            "RAGFLOW_TIMEOUT": str(payload.get("request_timeout", fallback.request_timeout)),
            "SERVICE_HOST": str(payload.get("server_host") or fallback.server_host),
            "SERVICE_PORT": str(payload.get("server_port", fallback.server_port)),
        }
        return cls.from_sources(values, prefer_os_env=False, require_ragflow=True)

    @classmethod
    def from_sources(
        cls,
        file_values: dict[str, str],
        *,
        prefer_os_env: bool = True,
        require_ragflow: bool = True,
    ) -> "Settings":
        if prefer_os_env:
            base_url = _get_config_value("RAGFLOW_BASE_URL", file_values)
            api_key = _get_config_value("RAGFLOW_API_KEY", file_values)
            timeout_raw = _get_config_value("RAGFLOW_TIMEOUT", file_values, default="60")
            host = _get_config_value("SERVICE_HOST", file_values, default="0.0.0.0") or "0.0.0.0"
            port_raw = _get_config_value("SERVICE_PORT", file_values, default="8080")
        else:
            base_url = file_values.get("RAGFLOW_BASE_URL", "").strip()
            api_key = file_values.get("RAGFLOW_API_KEY", "").strip()
            timeout_raw = file_values.get("RAGFLOW_TIMEOUT", "60").strip()
            host = file_values.get("SERVICE_HOST", "0.0.0.0").strip() or "0.0.0.0"
            port_raw = file_values.get("SERVICE_PORT", "8080").strip()

        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise ConfigError("RAGFLOW_TIMEOUT must be a number") from exc

        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError("SERVICE_PORT must be an integer") from exc

        if require_ragflow:
            if not base_url:
                raise ConfigError("Missing required env var: RAGFLOW_BASE_URL")
            if not api_key:
                raise ConfigError("Missing required env var: RAGFLOW_API_KEY")

        return cls(
            ragflow_base_url=base_url.rstrip("/"),
            ragflow_api_key=api_key,
            request_timeout=timeout,
            server_host=host,
            server_port=port,
        )

    def to_payload(self, *, mask_secret: bool = True) -> dict[str, Any]:
        return {
            "ragflow_base_url": self.ragflow_base_url,
            "ragflow_api_key": _mask_secret(self.ragflow_api_key) if mask_secret else self.ragflow_api_key,
            "request_timeout": self.request_timeout,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "configured": self.is_ragflow_configured(),
        }

    def to_env_mapping(self) -> dict[str, str]:
        return {
            "RAGFLOW_BASE_URL": self.ragflow_base_url,
            "RAGFLOW_API_KEY": self.ragflow_api_key,
            "RAGFLOW_TIMEOUT": str(self.request_timeout),
            "SERVICE_HOST": self.server_host,
            "SERVICE_PORT": str(self.server_port),
        }

    def is_ragflow_configured(self) -> bool:
        return bool(self.ragflow_base_url and self.ragflow_api_key)


def _get_config_value(name: str, file_values: dict[str, str], default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    return file_values.get(name, default).strip()


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        values[key] = value

    return values


def write_dotenv(path: Path, values: dict[str, str]) -> None:
    ordered_keys = [
        "RAGFLOW_BASE_URL",
        "RAGFLOW_API_KEY",
        "RAGFLOW_TIMEOUT",
        "SERVICE_HOST",
        "SERVICE_PORT",
    ]
    lines = []
    for key in ordered_keys:
        if key in values:
            lines.append(f"{key}={_quote_env_value(values[key])}")
    for key in sorted(values):
        if key not in ordered_keys:
            lines.append(f"{key}={_quote_env_value(values[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quote_env_value(value: Any) -> str:
    text = str(value)
    if text == "":
        return '""'
    if any(ch.isspace() for ch in text) or any(ch in text for ch in {'"', "'", "#"}):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"
