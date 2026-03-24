import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragflow_service.config import Settings
from ragflow_service.exceptions import ConfigError


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

    def test_from_env_uses_dotenv_when_os_env_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "RAGFLOW_BASE_URL=http://127.0.0.1:9380\n"
                "RAGFLOW_API_KEY=dotenv-key\n"
                "RAGFLOW_TIMEOUT=45\n"
                "LLM_BASE_URL=https://llm.local/v1\n"
                "LLM_API_KEY=llm-key\n"
                "LLM_MODEL=test-model\n"
                "LLM_TIMEOUT=30\n"
                "SERVICE_PORT=9090\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                with patch("ragflow_service.config.ENV_FILE", env_path):
                    settings = Settings.from_env()

        self.assertEqual(settings.ragflow_base_url, "http://127.0.0.1:9380")
        self.assertEqual(settings.ragflow_api_key, "dotenv-key")
        self.assertEqual(settings.request_timeout, 45.0)
        self.assertEqual(settings.llm_base_url, "https://llm.local/v1")
        self.assertEqual(settings.llm_api_key, "llm-key")
        self.assertEqual(settings.llm_model, "test-model")
        self.assertEqual(settings.llm_timeout, 30.0)
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

    def test_with_overrides_updates_runtime_values(self):
        settings = Settings(
            ragflow_base_url="http://127.0.0.1:9380",
            ragflow_api_key="secret-key",
            llm_base_url="https://llm.local/v1",
            llm_api_key="llm-secret",
            llm_model="old-model",
            request_timeout=60.0,
            llm_timeout=60.0,
            server_host="0.0.0.0",
            server_port=8080,
        )

        updated = settings.with_overrides(
            ragflow_base_url="http://new-host:9000",
            llm_model="new-model",
            request_timeout=30.0,
            server_port=18080,
        )

        self.assertEqual(updated.ragflow_base_url, "http://new-host:9000")
        self.assertEqual(updated.ragflow_api_key, "secret-key")
        self.assertEqual(updated.llm_model, "new-model")
        self.assertEqual(updated.request_timeout, 30.0)
        self.assertEqual(updated.server_port, 18080)

    def test_require_ragflow_raises_when_missing(self):
        with self.assertRaises(ConfigError):
            Settings().require_ragflow()

    def test_require_llm_raises_when_missing(self):
        with self.assertRaises(ConfigError):
            Settings().require_llm()


if __name__ == "__main__":
    unittest.main()
