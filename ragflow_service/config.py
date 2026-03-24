from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from .exceptions import ConfigError

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


@dataclass(frozen=True)
class Settings:
    ragflow_base_url: str = ""
    ragflow_api_key: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    request_timeout: float = 60.0
    llm_timeout: float = 60.0
    server_host: str = "0.0.0.0"
    server_port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        file_values = _load_dotenv(ENV_FILE)
        return cls.from_sources(file_values)

    @classmethod
    def from_sources(cls, file_values: dict[str, str], *, prefer_os_env: bool = True) -> "Settings":
        base_url = _get_config_value("RAGFLOW_BASE_URL", file_values, prefer_os_env=prefer_os_env)
        api_key = _get_config_value("RAGFLOW_API_KEY", file_values, prefer_os_env=prefer_os_env)
        llm_base_url = _get_config_value("LLM_BASE_URL", file_values, prefer_os_env=prefer_os_env)
        llm_api_key = _get_config_value("LLM_API_KEY", file_values, prefer_os_env=prefer_os_env)
        llm_model = _get_config_value("LLM_MODEL", file_values, prefer_os_env=prefer_os_env)
        timeout_raw = _get_config_value("RAGFLOW_TIMEOUT", file_values, default="60", prefer_os_env=prefer_os_env)
        llm_timeout_raw = _get_config_value("LLM_TIMEOUT", file_values, default="60", prefer_os_env=prefer_os_env)
        host = _get_config_value("SERVICE_HOST", file_values, default="0.0.0.0", prefer_os_env=prefer_os_env) or "0.0.0.0"
        port_raw = _get_config_value("SERVICE_PORT", file_values, default="8080", prefer_os_env=prefer_os_env)

        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise ConfigError("RAGFLOW_TIMEOUT must be a number") from exc

        try:
            llm_timeout = float(llm_timeout_raw)
        except ValueError as exc:
            raise ConfigError("LLM_TIMEOUT must be a number") from exc

        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError("SERVICE_PORT must be an integer") from exc

        return cls(
            ragflow_base_url=base_url.rstrip("/"),
            ragflow_api_key=api_key,
            llm_base_url=llm_base_url.rstrip("/"),
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            request_timeout=timeout,
            llm_timeout=llm_timeout,
            server_host=host,
            server_port=port,
        )

    def with_overrides(
        self,
        *,
        ragflow_base_url: str | None = None,
        ragflow_api_key: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        request_timeout: float | None = None,
        llm_timeout: float | None = None,
        server_host: str | None = None,
        server_port: int | None = None,
    ) -> "Settings":
        return replace(
            self,
            ragflow_base_url=(ragflow_base_url or self.ragflow_base_url).rstrip("/"),
            ragflow_api_key=ragflow_api_key if ragflow_api_key is not None else self.ragflow_api_key,
            llm_base_url=(llm_base_url or self.llm_base_url).rstrip("/"),
            llm_api_key=llm_api_key if llm_api_key is not None else self.llm_api_key,
            llm_model=llm_model if llm_model is not None else self.llm_model,
            request_timeout=request_timeout if request_timeout is not None else self.request_timeout,
            llm_timeout=llm_timeout if llm_timeout is not None else self.llm_timeout,
            server_host=server_host or self.server_host,
            server_port=server_port if server_port is not None else self.server_port,
        )

    def require_ragflow(self) -> "Settings":
        if not self.ragflow_base_url:
            raise ConfigError("Missing required env var: RAGFLOW_BASE_URL")
        if not self.ragflow_api_key:
            raise ConfigError("Missing required env var: RAGFLOW_API_KEY")
        return self

    def is_ragflow_configured(self) -> bool:
        return bool(self.ragflow_base_url and self.ragflow_api_key)

    def require_llm(self) -> "Settings":
        if not self.llm_base_url:
            raise ConfigError("Missing required env var: LLM_BASE_URL")
        if not self.llm_api_key:
            raise ConfigError("Missing required env var: LLM_API_KEY")
        if not self.llm_model:
            raise ConfigError("Missing required env var: LLM_MODEL")
        return self

    def is_llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


def _get_config_value(
    name: str,
    file_values: dict[str, str],
    *,
    default: str = "",
    prefer_os_env: bool = True,
) -> str:
    if prefer_os_env:
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
