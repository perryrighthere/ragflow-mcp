import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragflow_service.config import _load_dotenv, Settings
from ragflow_service.http_server import ServiceRuntime


class ConfigTests(unittest.TestCase):
    def test_from_env_allows_missing_ragflow_settings_for_bootstrap(self):
        with patch.dict(os.environ, {}, clear=True):
            with tempfile.TemporaryDirectory() as tmpdir:
                env_path = Path(tmpdir) / ".env"
                env_path.write_text("SERVICE_PORT=9090\n", encoding="utf-8")
                with patch("ragflow_service.config.ENV_FILE", env_path):
                    settings = Settings.from_env()

        self.assertEqual(settings.ragflow_base_url, "")
        self.assertEqual(settings.ragflow_api_key, "")
        self.assertEqual(settings.server_port, 9090)
        self.assertFalse(settings.is_ragflow_configured())

    def test_load_dotenv_parses_key_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                'RAGFLOW_BASE_URL=http://127.0.0.1:9380\n'
                'export RAGFLOW_API_KEY="secret-key"\n'
                "# comment\n"
                "SERVICE_PORT=9090\n",
                encoding="utf-8",
            )

            values = _load_dotenv(env_path)

        self.assertEqual(values["RAGFLOW_BASE_URL"], "http://127.0.0.1:9380")
        self.assertEqual(values["RAGFLOW_API_KEY"], "secret-key")
        self.assertEqual(values["SERVICE_PORT"], "9090")

    def test_from_env_uses_dotenv_when_os_env_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "RAGFLOW_BASE_URL=http://127.0.0.1:9380\n"
                "RAGFLOW_API_KEY=dotenv-key\n"
                "SERVICE_PORT=9090\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                with patch("ragflow_service.config.ENV_FILE", env_path):
                    settings = Settings.from_env()

        self.assertEqual(settings.ragflow_base_url, "http://127.0.0.1:9380")
        self.assertEqual(settings.ragflow_api_key, "dotenv-key")
        self.assertEqual(settings.server_port, 9090)

    def test_os_env_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "RAGFLOW_BASE_URL=http://127.0.0.1:9380\n"
                "RAGFLOW_API_KEY=dotenv-key\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "RAGFLOW_BASE_URL": "http://override.local:9000",
                    "RAGFLOW_API_KEY": "env-key",
                },
                clear=True,
            ):
                with patch("ragflow_service.config.ENV_FILE", env_path):
                    settings = Settings.from_env()

        self.assertEqual(settings.ragflow_base_url, "http://override.local:9000")
        self.assertEqual(settings.ragflow_api_key, "env-key")

    def test_from_payload_keeps_secret_when_omitted(self):
        fallback = Settings(
            ragflow_base_url="http://127.0.0.1:9380",
            ragflow_api_key="secret-key",
            request_timeout=60.0,
            server_host="0.0.0.0",
            server_port=8080,
        )

        settings = Settings.from_payload(
            {"ragflow_base_url": "http://new-host:9000"},
            fallback=fallback,
        )

        self.assertEqual(settings.ragflow_base_url, "http://new-host:9000")
        self.assertEqual(settings.ragflow_api_key, "secret-key")

    def test_runtime_update_rebuilds_service_and_writes_env(self):
        initial = Settings(
            ragflow_base_url="",
            ragflow_api_key="",
            request_timeout=60.0,
            server_host="0.0.0.0",
            server_port=8080,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            runtime = ServiceRuntime(initial)
            self.assertIsNone(runtime._service)

            with patch("ragflow_service.http_server.ENV_FILE", env_path):
                updated = runtime.update_settings(
                    {
                        "ragflow_base_url": "http://new-host:9000",
                        "ragflow_api_key": "secret-key",
                        "request_timeout": 30,
                    }
                )

            env_text = env_path.read_text(encoding="utf-8")

        self.assertEqual(updated.ragflow_base_url, "http://new-host:9000")
        self.assertEqual(runtime.get_service().client.base_url, "http://new-host:9000")
        self.assertEqual(runtime.get_service().client.timeout, 30.0)
        self.assertIn("RAGFLOW_BASE_URL=http://new-host:9000", env_text)
        self.assertIn("RAGFLOW_API_KEY=secret-key", env_text)


if __name__ == "__main__":
    unittest.main()
