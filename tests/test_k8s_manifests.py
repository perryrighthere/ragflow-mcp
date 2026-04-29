import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class K8sManifestTests(unittest.TestCase):
    def test_runtime_env_examples_are_exposed_in_configmap(self):
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        configmap = (PROJECT_ROOT / "k8s" / "ragflow-knowledge-portal-configmap.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("RAG_INFO_SYNC_URL=", env_example)
        self.assertIn("CORS_ALLOWED_ORIGINS=", env_example)
        self.assertIn('RAG_INFO_SYNC_URL: "${RAG_INFO_SYNC_URL}"', configmap)
        self.assertIn('CORS_ALLOWED_ORIGINS: "${CORS_ALLOWED_ORIGINS}"', configmap)


if __name__ == "__main__":
    unittest.main()
