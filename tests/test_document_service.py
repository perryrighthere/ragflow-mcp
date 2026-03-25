import json
import tempfile
import unittest
from pathlib import Path

from ragflow_service.document_service import RagflowDocumentService
from ragflow_service.exceptions import ValidationError
from ragflow_service.knowledge_portal_service import KnowledgePortalSyncService
from ragflow_service.ragflow_client import UpstreamResponse


class FakeKnowledgePortalService:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self._validator = KnowledgePortalSyncService()

    def sync_documents(self, payload):
        self.calls.append(payload)
        return self.result

    def _validate_payload(self, payload):
        return self._validator._validate_payload(payload)


class FakeRagflowClient:
    def __init__(self):
        self.upload_calls = []
        self.update_calls = []
        self.parse_calls = []
        self._next_id = 1

    def upload_documents(self, dataset_id, files):
        self.upload_calls.append((dataset_id, files))
        data = []
        for file in files:
            data.append(
                {
                    "id": f"rf-doc-{self._next_id}",
                    "name": file.filename,
                }
            )
            self._next_id += 1
        return UpstreamResponse(
            status_code=200,
            payload={
                "code": 0,
                "data": data,
            },
        )

    def update_document(self, dataset_id, document_id, payload):
        self.update_calls.append((dataset_id, document_id, payload))
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": {"id": document_id}})

    def parse_documents(self, dataset_id, payload):
        self.parse_calls.append((dataset_id, payload))
        return UpstreamResponse(status_code=200, payload={"code": 0})


class RagflowDocumentServiceTests(unittest.TestCase):
    def test_import_knowledge_portal_documents_uploads_updates_and_parses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first_dir = root / "doc-1"
            second_dir = root / "doc-2"
            first_dir.mkdir()
            second_dir.mkdir()

            detail_one = first_dir / "detail.json"
            detail_one.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdNo": "NO-1",
                            "fdName": "制度文档",
                            "fdCateId": "cate-1",
                            "fdPublishTime": "2025-09-03 16:52:31",
                            "fdCreatorNo": "0999",
                            "fdCreatorName": "管理员",
                            "fdLink": "https://km.seres.cn/doc-1",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            detail_two = second_dir / "detail.json"
            detail_two.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-2",
                            "fdName": "纯正文文档",
                            "fdCreatorName": "测试人",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            attachment_path = first_dir / "manual.pdf"
            attachment_path.write_bytes(b"%PDF-1.4")
            content_path = second_dir / "content.md"
            content_path.write_text("# 纯正文文档\n\n正文", encoding="utf-8")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 2,
                    "downloaded_document_count": 2,
                    "downloaded_file_count": 1,
                    "max_download_files": 1,
                    "download_limit_reached": True,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "制度文档",
                            "saved_dir": str(first_dir),
                            "detail_json_path": str(detail_one),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "manual.pdf",
                                    "path": str(attachment_path),
                                }
                            ],
                        },
                        {
                            "fdId": "doc-2",
                            "fdName": "纯正文文档",
                            "saved_dir": str(second_dir),
                            "detail_json_path": str(detail_two),
                            "content_path": str(content_path),
                            "downloaded_files": [],
                        },
                    ],
                    "errors": [],
                }
            )
            ragflow_client = FakeRagflowClient()
            service = RagflowDocumentService(ragflow_client, portal_service)

            result = service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "max_download_files": 1,
                    "parse_after_upload": True,
                    "include_cover_image": False,
                    "document_update": {
                        "enabled": 1,
                        "meta_fields": {
                            "source": "knowledge_portal",
                            "owner": "search-team",
                        },
                    },
                }
            )

            self.assertEqual(
                portal_service.calls[0],
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "type": "mutildoc",
                    "page_size": 100,
                    "max_download_files": 1,
                    "begin_time": None,
                    "fd_cate_id": None,
                    "timeout": 60.0,
                    "include_attachments": True,
                    "include_cover_image": False,
                },
            )
            self.assertEqual(len(ragflow_client.upload_calls), 2)
            self.assertEqual(ragflow_client.upload_calls[0][0], "kb_123")
            self.assertEqual(ragflow_client.upload_calls[0][1][0].filename, "manual.pdf")
            self.assertEqual(ragflow_client.upload_calls[1][1][0].filename, "content.md")

            first_update = ragflow_client.update_calls[0]
            self.assertEqual(first_update[0], "kb_123")
            self.assertEqual(first_update[1], "rf-doc-1")
            self.assertEqual(first_update[2]["enabled"], 1)
            self.assertEqual(first_update[2]["meta_fields"]["source"], "knowledge_portal")
            self.assertEqual(first_update[2]["meta_fields"]["owner"], "search-team")
            self.assertEqual(first_update[2]["meta_fields"]["knowledge_portal_fd_id"], "doc-1")
            self.assertEqual(first_update[2]["meta_fields"]["knowledge_portal_file_kind"], "attachment")
            self.assertEqual(first_update[2]["meta_fields"]["knowledge_portal_file_name"], "manual.pdf")

            second_update = ragflow_client.update_calls[1]
            self.assertEqual(second_update[2]["meta_fields"]["knowledge_portal_fd_id"], "doc-2")
            self.assertEqual(second_update[2]["meta_fields"]["knowledge_portal_file_kind"], "content_markdown")
            self.assertEqual(second_update[2]["meta_fields"]["knowledge_portal_file_name"], "content.md")

            self.assertEqual(
                ragflow_client.parse_calls[0],
                ("kb_123", {"document_ids": ["rf-doc-1", "rf-doc-2"]}),
            )
            self.assertEqual(result["dataset_id"], "kb_123")
            self.assertEqual(result["imported_document_count"], 2)
            self.assertEqual(result["uploaded_file_count"], 2)
            self.assertEqual(result["updated_document_count"], 2)
            self.assertEqual(result["parsed_document_count"], 2)
            self.assertEqual(result["errors"], [])

    def test_import_knowledge_portal_documents_requires_at_least_one_upload_source(self):
        portal_service = FakeKnowledgePortalService({"documents": [], "errors": []})
        ragflow_client = FakeRagflowClient()
        service = RagflowDocumentService(ragflow_client, portal_service)

        with self.assertRaisesRegex(ValidationError, "At least one upload source must be enabled"):
            service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "include_attachments": False,
                    "include_cover_image": False,
                    "fallback_to_content_markdown": False,
                }
            )


if __name__ == "__main__":
    unittest.main()
