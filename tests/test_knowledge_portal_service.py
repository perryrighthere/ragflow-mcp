import json
import tempfile
import unittest
from pathlib import Path

from ragflow_service.knowledge_portal_service import KnowledgePortalSyncService


class FakeKnowledgePortalClient:
    def __init__(self):
        self.list_calls = []
        self.detail_calls = []
        self.download_calls = []

    def list_documents(self, *, page_no, page_size, doc_type, fd_cate_id=None, begin_time=None):
        self.list_calls.append(
            {
                "page_no": page_no,
                "page_size": page_size,
                "doc_type": doc_type,
                "fd_cate_id": fd_cate_id,
                "begin_time": begin_time,
            }
        )
        if page_no == 1:
            return {
                "code": 200,
                "data": {
                    "currPage": 1,
                    "pagesize": 2,
                    "totalRows": 3,
                    "data": [
                        {"fdId": "doc-1", "fdName": "文档一"},
                        {"fdId": "doc-2", "fdName": "文档二"},
                    ],
                },
            }
        return {
            "code": 200,
            "data": {
                "currPage": 2,
                "pagesize": 2,
                "totalRows": 3,
                "data": [
                    {"fdId": "doc-3", "fdName": "文档三"},
                ],
            },
        }

    def get_document_detail(self, *, fd_id=None, fd_no=None):
        self.detail_calls.append({"fd_id": fd_id, "fd_no": fd_no})
        suffix = fd_id.split("-")[-1]
        return {
            "code": 200,
            "data": {
                "fdId": fd_id,
                "fdName": f"文档{suffix}",
                "fdContent": f"正文{suffix}",
                "fdCoverImg": {"fileId": f"cover-{suffix}", "fileName": f"cover-{suffix}.png"},
                "fdFile": [{"fileId": f"file-{suffix}", "fileName": f"file-{suffix}.pdf"}],
            },
        }

    def download_attachment(self, *, file_id):
        self.download_calls.append(file_id)
        return type(
            "Resp",
            (),
            {
                "payload": f"binary:{file_id}".encode("utf-8"),
                "headers": {"Content-Type": "application/octet-stream"},
            },
        )()


class KnowledgePortalSyncServiceTests(unittest.TestCase):
    def test_sync_documents_traverses_all_pages_and_saves_files(self):
        fake_client = FakeKnowledgePortalClient()
        captured_kwargs = {}

        def factory(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_client

        with tempfile.TemporaryDirectory() as tmpdir:
            service = KnowledgePortalSyncService(
                output_dir=Path(tmpdir),
                default_timeout=12.0,
                client_factory=factory,
            )
            result = service.sync_documents(
                {
                    "base_url": "km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "page_size": 2,
                }
            )

            self.assertEqual(captured_kwargs["base_url"], "https://km.seres.cn")
            self.assertEqual(captured_kwargs["community_id"], "community")
            self.assertEqual(captured_kwargs["timeout"], 12.0)
            self.assertEqual([call["page_no"] for call in fake_client.list_calls], [1, 2])
            self.assertEqual(len(fake_client.detail_calls), 3)
            self.assertEqual(result["total_documents"], 3)
            self.assertEqual(result["downloaded_document_count"], 3)
            self.assertEqual(result["downloaded_file_count"], 6)
            self.assertEqual(result["errors"], [])

            first_document = result["documents"][0]
            detail_path = Path(first_document["detail_json_path"])
            content_path = Path(first_document["content_path"])
            attachment_path = Path(first_document["downloaded_files"][0]["path"])
            self.assertTrue(detail_path.is_file())
            self.assertTrue(content_path.is_file())
            self.assertTrue(attachment_path.is_file())
            self.assertIn("正文1", content_path.read_text(encoding="utf-8"))

            payload = json.loads(detail_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["data"]["fdId"], "doc-1")

    def test_sync_documents_respects_max_download_files_without_wasting_budget_on_cover_images(self):
        fake_client = FakeKnowledgePortalClient()

        def factory(**kwargs):
            return fake_client

        with tempfile.TemporaryDirectory() as tmpdir:
            service = KnowledgePortalSyncService(
                output_dir=Path(tmpdir),
                client_factory=factory,
            )
            result = service.sync_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "page_size": 2,
                    "max_download_files": 3,
                    "include_cover_image": False,
                }
            )

            self.assertEqual(result["max_download_files"], 3)
            self.assertTrue(result["download_limit_reached"])
            self.assertEqual(result["downloaded_file_count"], 3)
            self.assertEqual(len(result["documents"]), 3)
            self.assertEqual(len(result["documents"][0]["downloaded_files"]), 1)
            self.assertEqual(len(result["documents"][1]["downloaded_files"]), 1)
            self.assertEqual(len(result["documents"][2]["downloaded_files"]), 1)
            self.assertEqual(len(fake_client.download_calls), 3)
            self.assertEqual(fake_client.download_calls, ["file-1", "file-2", "file-3"])


if __name__ == "__main__":
    unittest.main()
