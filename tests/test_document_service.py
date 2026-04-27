import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ragflow_service.document_service import RagflowDocumentService
from ragflow_service.exceptions import RagInfoSyncError, RagflowAPIError, ValidationError
from ragflow_service.knowledge_portal_service import KnowledgePortalSyncService
from ragflow_service.ragflow_client import UpstreamResponse


class FakeKnowledgePortalService:
    def __init__(self, result, *, events=None):
        self.result = result
        self.calls = []
        self.cleanup_calls = []
        self.events = events if events is not None else []
        self._validator = KnowledgePortalSyncService()

    def sync_documents(self, payload):
        self.calls.append(payload)
        return self.result

    def start_sync(self, payload, *, collect_documents=True, should_stop=None):
        self.calls.append(payload)
        summary = {
            "base_url": self.result.get("base_url"),
            "output_dir": self.result.get("output_dir"),
            "total_documents": self.result.get("total_documents", 0),
            "downloaded_document_count": 0,
            "downloaded_file_count": 0,
            "max_download_files": self.result.get("max_download_files"),
            "download_limit_reached": self.result.get("download_limit_reached", False),
            "documents": [],
            "errors": list(self.result.get("errors") or []),
        }

        def iterator():
            for document in self.result.get("documents") or []:
                self.events.append(f"yield:{document['fdId']}")
                summary["downloaded_document_count"] += 1
                summary["downloaded_file_count"] += len(document.get("downloaded_files") or [])
                if collect_documents:
                    summary["documents"].append(document)
                yield document
                self.events.append(f"resumed:{document['fdId']}")

        return summary, iterator()

    def _validate_payload(self, payload):
        return self._validator._validate_payload(payload)

    def cleanup_document_cache(self, portal_document):
        self.cleanup_calls.append(portal_document["fdId"])
        self.events.append(f"cleanup:{portal_document['fdId']}")
        saved_dir = Path(portal_document.get("saved_dir") or "")
        if saved_dir.is_dir():
            shutil.rmtree(saved_dir)


class FakeRagflowClient:
    def __init__(self, *, events=None, upload_names=None, list_names=None, fail_update_once_names=None):
        self.api_key = "tenant-key"
        self.upload_calls = []
        self.update_calls = []
        self.list_calls = []
        self.parse_calls = []
        self.upload_names = upload_names or {}
        self.list_names = list_names or {}
        self.fail_update_once_names = set(fail_update_once_names or [])
        self.failed_update_names = set()
        self.documents_by_id = {}
        self._next_id = 1
        self.events = events if events is not None else []

    def upload_documents(self, dataset_id, files):
        self.upload_calls.append((dataset_id, files))
        self.events.append("upload:" + ",".join(file.filename for file in files))
        data = []
        for file in files:
            document_id = f"rf-doc-{self._next_id}"
            upload_name = self.upload_names.get(file.filename, file.filename)
            listed_name = self.list_names.get(file.filename, upload_name)
            listed_document = {
                "id": document_id,
                "name": listed_name,
                "location": listed_name,
                "type": "doc",
                "chunk_method": "naive",
                "parser_config": {"chunk_token_num": 256},
                "run": "UNSTART",
            }
            self.documents_by_id[document_id] = listed_document
            data.append(
                {
                    "id": document_id,
                    "name": upload_name,
                    "location": upload_name,
                    "type": "doc",
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
        self.events.append(f"update:{document_id}")
        document_name = self.documents_by_id.get(document_id, {}).get("name")
        if document_name in self.fail_update_once_names and document_name not in self.failed_update_names:
            self.failed_update_names.add(document_name)
            raise RagflowAPIError("update failed", status_code=502, payload={"code": 102})
        if document_id in self.documents_by_id:
            self.documents_by_id[document_id].update(payload)
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": {"id": document_id}})

    def list_documents(self, dataset_id, query):
        self.list_calls.append((dataset_id, query))
        document_id = str((query or {}).get("id") or "")
        docs = []
        if document_id in self.documents_by_id:
            docs.append(self.documents_by_id[document_id])
        return UpstreamResponse(status_code=200, payload={"code": 0, "data": {"docs": docs}})

    def parse_documents(self, dataset_id, payload):
        self.parse_calls.append((dataset_id, payload))
        self.events.append("parse:" + ",".join(payload.get("document_ids") or []))
        return UpstreamResponse(status_code=200, payload={"code": 0})


class FakeRagInfoSyncClient:
    def __init__(self, *, events=None, fail=False):
        self.calls = []
        self.events = events if events is not None else []
        self.fail = fail

    def sync_rag_info(self, payload):
        self.calls.append(payload)
        self.events.append(f"sync:{payload['ragFileId']}")
        if self.fail:
            raise RagInfoSyncError("sync failed", status_code=502, payload={"message": "bad gateway"})
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

            events = []
            rag_info_sync_client = FakeRagInfoSyncClient(events=events)
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
                },
                events=events,
            )
            ragflow_client = FakeRagflowClient(events=events)
            service = RagflowDocumentService(
                ragflow_client,
                portal_service,
                rag_info_sync_client=rag_info_sync_client,
                tenant_id="tenant-key",
            )

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
            self.assertEqual(
                events,
                [
                    "yield:doc-1",
                    "upload:manual.pdf",
                    "update:rf-doc-1",
                    "sync:rf-doc-1",
                    "cleanup:doc-1",
                    "resumed:doc-1",
                    "yield:doc-2",
                    "upload:content.md",
                    "update:rf-doc-2",
                    "sync:rf-doc-2",
                    "cleanup:doc-2",
                    "resumed:doc-2",
                    "parse:rf-doc-1,rf-doc-2",
                ],
            )
            self.assertEqual(len(ragflow_client.upload_calls), 2)
            self.assertEqual(ragflow_client.upload_calls[0][0], "kb_123")
            self.assertEqual(ragflow_client.upload_calls[0][1][0].filename, "manual.pdf")
            self.assertEqual(ragflow_client.upload_calls[1][1][0].filename, "content.md")
            self.assertFalse(first_dir.exists())
            self.assertFalse(second_dir.exists())
            self.assertEqual(portal_service.cleanup_calls, ["doc-1", "doc-2"])

            first_update = ragflow_client.update_calls[0]
            self.assertEqual(first_update[0], "kb_123")
            self.assertEqual(first_update[1], "rf-doc-1")
            self.assertEqual(first_update[2]["enabled"], 1)
            self.assertEqual(first_update[2]["meta_fields"]["source"], "knowledge_portal")
            self.assertEqual(first_update[2]["meta_fields"]["owner"], "search-team")
            self.assertEqual(first_update[2]["meta_fields"]["knowledge_portal_fd_id"], "doc-1")
            self.assertEqual(first_update[2]["meta_fields"]["knowledge_portal_file_kind"], "attachment")
            self.assertEqual(first_update[2]["meta_fields"]["knowledge_portal_file_name"], "manual.pdf")
            self.assertEqual(first_update[2]["meta_fields"]["knowledgeDatabaseId"], "kb_123")
            self.assertEqual(first_update[2]["meta_fields"]["ragFileId"], "rf-doc-1")
            self.assertEqual(first_update[2]["meta_fields"]["originFileId"], "doc-1")
            self.assertEqual(first_update[2]["meta_fields"]["tenantId"], "tenant-key")

            second_update = ragflow_client.update_calls[1]
            self.assertEqual(second_update[2]["meta_fields"]["knowledge_portal_fd_id"], "doc-2")
            self.assertEqual(second_update[2]["meta_fields"]["knowledge_portal_file_kind"], "content_markdown")
            self.assertEqual(second_update[2]["meta_fields"]["knowledge_portal_file_name"], "content.md")
            self.assertEqual(second_update[2]["meta_fields"]["knowledgeDatabaseId"], "kb_123")
            self.assertEqual(second_update[2]["meta_fields"]["ragFileId"], "rf-doc-2")
            self.assertEqual(second_update[2]["meta_fields"]["originFileId"], "doc-2")
            self.assertEqual(second_update[2]["meta_fields"]["tenantId"], "tenant-key")

            self.assertEqual(
                rag_info_sync_client.calls,
                [
                    {
                        "knowledgeDatabaseId": "kb_123",
                        "ragFileId": "rf-doc-1",
                        "originFileId": "doc-1",
                        "tenantId": "tenant-key",
                    },
                    {
                        "knowledgeDatabaseId": "kb_123",
                        "ragFileId": "rf-doc-2",
                        "originFileId": "doc-2",
                        "tenantId": "tenant-key",
                    },
                ],
            )

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

    def test_import_sets_presentation_chunk_method_for_pptx_uploads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_dir = root / "doc-1"
            doc_dir.mkdir()

            detail_path = doc_dir / "detail.json"
            detail_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            presentation_path = doc_dir / "quarterly.PPTX"
            presentation_path.write_bytes(b"pptx")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 1,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                            "saved_dir": str(doc_dir),
                            "detail_json_path": str(detail_path),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "quarterly.PPTX",
                                    "path": str(presentation_path),
                                }
                            ],
                        },
                    ],
                    "errors": [],
                },
            )
            ragflow_client = FakeRagflowClient()
            service = RagflowDocumentService(ragflow_client, portal_service)

            service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "parse_after_upload": True,
                    "include_cover_image": False,
                    "document_update": {
                        "enabled": 1,
                        "chunk_method": "naive",
                        "parser_config": {"chunk_token_num": 256},
                    },
                }
            )

            update_payload = ragflow_client.update_calls[0][2]
            self.assertEqual(update_payload["chunk_method"], "presentation")
            self.assertEqual(update_payload["parser_config"], {"raptor": {"use_raptor": False}})
            self.assertEqual(
                ragflow_client.parse_calls[0],
                ("kb_123", {"document_ids": ["rf-doc-1"]}),
            )

    def test_import_parses_presentation_uploads_in_a_separate_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_dir = root / "doc-1"
            doc_dir.mkdir()

            detail_path = doc_dir / "detail.json"
            detail_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdName": "混合附件",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            pdf_path = doc_dir / "manual.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")
            presentation_path = doc_dir / "slides.pptx"
            presentation_path.write_bytes(b"pptx")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 1,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "混合附件",
                            "saved_dir": str(doc_dir),
                            "detail_json_path": str(detail_path),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "manual.pdf",
                                    "path": str(pdf_path),
                                },
                                {
                                    "kind": "attachment",
                                    "file_id": "file-2",
                                    "file_name": "slides.pptx",
                                    "path": str(presentation_path),
                                },
                            ],
                        },
                    ],
                    "errors": [],
                },
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
                    "parse_after_upload": True,
                    "include_cover_image": False,
                    "document_update": {
                        "enabled": 1,
                        "chunk_method": "naive",
                        "parser_config": {"chunk_token_num": 256},
                    },
                }
            )

            self.assertEqual(
                ragflow_client.parse_calls,
                [
                    ("kb_123", {"document_ids": ["rf-doc-1"]}),
                    ("kb_123", {"document_ids": ["rf-doc-2"]}),
                ],
            )
            self.assertEqual(result["parsed_document_count"], 2)
            self.assertEqual(result["parse_result"]["batches"][0]["group"], "default")
            self.assertEqual(result["parse_result"]["batches"][1]["group"], "presentation")
            self.assertEqual(service._build_parse_group({"chunk_method": "presentation"}), "presentation")

    def test_import_detects_presentation_from_ragflow_uploaded_document_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_dir = root / "doc-1"
            doc_dir.mkdir()

            detail_path = doc_dir / "detail.json"
            detail_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            attachment_path = doc_dir / "attachment.bin"
            attachment_path.write_bytes(b"pptx")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 1,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                            "saved_dir": str(doc_dir),
                            "detail_json_path": str(detail_path),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "attachment.bin",
                                    "path": str(attachment_path),
                                }
                            ],
                        },
                    ],
                    "errors": [],
                },
            )
            ragflow_client = FakeRagflowClient(upload_names={"attachment.bin": "quarterly.pptx"})
            service = RagflowDocumentService(ragflow_client, portal_service)

            result = service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "parse_after_upload": True,
                    "include_cover_image": False,
                    "document_update": {
                        "enabled": 1,
                        "chunk_method": "naive",
                        "parser_config": {"chunk_token_num": 256},
                    },
                }
            )

            update_payload = ragflow_client.update_calls[0][2]
            self.assertEqual(update_payload["chunk_method"], "presentation")
            self.assertEqual(update_payload["parser_config"], {"raptor": {"use_raptor": False}})
            self.assertEqual(ragflow_client.parse_calls, [("kb_123", {"document_ids": ["rf-doc-1"]})])
            self.assertEqual(result["parse_result"], {"code": 0})

    def test_import_reclassifies_presentation_from_listed_ragflow_document_before_parse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_dir = root / "doc-1"
            doc_dir.mkdir()

            detail_path = doc_dir / "detail.json"
            detail_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            attachment_path = doc_dir / "attachment.bin"
            attachment_path.write_bytes(b"pptx")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 1,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                            "saved_dir": str(doc_dir),
                            "detail_json_path": str(detail_path),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "attachment.bin",
                                    "path": str(attachment_path),
                                }
                            ],
                        },
                    ],
                    "errors": [],
                },
            )
            ragflow_client = FakeRagflowClient(list_names={"attachment.bin": "quarterly.pptx"})
            service = RagflowDocumentService(ragflow_client, portal_service)

            result = service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "parse_after_upload": True,
                    "include_cover_image": False,
                    "document_update": {
                        "enabled": 1,
                        "meta_fields": {"source": "knowledge_portal"},
                    },
                }
            )

            self.assertEqual(ragflow_client.list_calls, [("kb_123", {"id": "rf-doc-1"})])
            self.assertEqual(len(ragflow_client.update_calls), 2)
            self.assertEqual(ragflow_client.update_calls[1][1], "rf-doc-1")
            self.assertEqual(ragflow_client.update_calls[1][2]["chunk_method"], "presentation")
            self.assertEqual(
                ragflow_client.update_calls[1][2]["parser_config"],
                {"raptor": {"use_raptor": False}},
            )
            self.assertEqual(ragflow_client.parse_calls, [("kb_123", {"document_ids": ["rf-doc-1"]})])
            self.assertEqual(result["parse_result"], {"code": 0})

    def test_import_parses_uploaded_presentation_when_initial_update_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_dir = root / "doc-1"
            doc_dir.mkdir()

            detail_path = doc_dir / "detail.json"
            detail_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            presentation_path = doc_dir / "slides.pptx"
            presentation_path.write_bytes(b"pptx")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 1,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                            "saved_dir": str(doc_dir),
                            "detail_json_path": str(detail_path),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "slides.pptx",
                                    "path": str(presentation_path),
                                }
                            ],
                        },
                    ],
                    "errors": [],
                },
            )
            ragflow_client = FakeRagflowClient(fail_update_once_names={"slides.pptx"})
            service = RagflowDocumentService(ragflow_client, portal_service)

            result = service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "parse_after_upload": True,
                    "include_cover_image": False,
                    "document_update": {"enabled": 1},
                }
            )

            self.assertEqual(len(ragflow_client.update_calls), 2)
            self.assertEqual(ragflow_client.update_calls[1][1], "rf-doc-1")
            self.assertEqual(ragflow_client.update_calls[1][2]["chunk_method"], "presentation")
            self.assertEqual(ragflow_client.parse_calls, [("kb_123", {"document_ids": ["rf-doc-1"]})])
            self.assertEqual(result["parsed_document_count"], 1)
            self.assertEqual(result["errors"][0]["stage"], "ragflow_update")

    def test_import_preserves_presentation_raptor_parser_config_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_dir = root / "doc-1"
            doc_dir.mkdir()

            detail_path = doc_dir / "detail.json"
            detail_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            presentation_path = doc_dir / "quarterly.pptx"
            presentation_path.write_bytes(b"pptx")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 1,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "季度汇报",
                            "saved_dir": str(doc_dir),
                            "detail_json_path": str(detail_path),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "quarterly.pptx",
                                    "path": str(presentation_path),
                                }
                            ],
                        },
                    ],
                    "errors": [],
                },
            )
            ragflow_client = FakeRagflowClient()
            service = RagflowDocumentService(ragflow_client, portal_service)

            service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "parse_after_upload": True,
                    "include_cover_image": False,
                    "document_update": {
                        "enabled": 1,
                        "chunk_method": "naive",
                        "parser_config": {
                            "chunk_token_num": 256,
                            "raptor": {"use_raptor": True},
                        },
                    },
                }
            )

            update_payload = ragflow_client.update_calls[0][2]
            self.assertEqual(update_payload["chunk_method"], "presentation")
            self.assertEqual(update_payload["parser_config"], {"raptor": {"use_raptor": True}})

    def test_import_records_rag_info_sync_errors_without_blocking_parse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_dir = root / "doc-1"
            doc_dir.mkdir()

            detail_path = doc_dir / "detail.json"
            detail_path.write_text(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "fdId": "doc-1",
                            "fdName": "制度文档",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            attachment_path = doc_dir / "manual.pdf"
            attachment_path.write_bytes(b"%PDF-1.4")

            portal_service = FakeKnowledgePortalService(
                {
                    "base_url": "https://km.seres.cn",
                    "output_dir": str(root),
                    "total_documents": 1,
                    "documents": [
                        {
                            "fdId": "doc-1",
                            "fdName": "制度文档",
                            "saved_dir": str(doc_dir),
                            "detail_json_path": str(detail_path),
                            "content_path": None,
                            "downloaded_files": [
                                {
                                    "kind": "attachment",
                                    "file_id": "file-1",
                                    "file_name": "manual.pdf",
                                    "path": str(attachment_path),
                                }
                            ],
                        }
                    ],
                    "errors": [],
                },
            )
            ragflow_client = FakeRagflowClient()
            rag_info_sync_client = FakeRagInfoSyncClient(fail=True)
            service = RagflowDocumentService(
                ragflow_client,
                portal_service,
                rag_info_sync_client=rag_info_sync_client,
                tenant_id="tenant-key",
            )

            result = service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "parse_after_upload": True,
                }
            )

            self.assertEqual(result["updated_document_count"], 1)
            self.assertEqual(result["parsed_document_count"], 1)
            self.assertEqual(ragflow_client.parse_calls, [("kb_123", {"document_ids": ["rf-doc-1"]})])
            self.assertEqual(result["errors"][0]["stage"], "rag_info_sync")
            self.assertEqual(result["errors"][0]["fdId"], "doc-1")
            self.assertEqual(result["errors"][0]["document_id"], "rf-doc-1")

    def test_import_stops_querying_portal_after_download_limit_when_markdown_fallback_disabled(self):
        class StreamingPortalClient:
            def __init__(self):
                self.list_calls = []
                self.detail_calls = []
                self.download_calls = []

            def list_documents(self, *, page_no, page_size, doc_type, fd_cate_id=None, begin_time=None):
                self.list_calls.append(page_no)
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
                self.detail_calls.append(fd_id)
                suffix = fd_id.split("-")[-1]
                return {
                    "code": 200,
                    "data": {
                        "fdId": fd_id,
                        "fdName": f"文档{suffix}",
                        "fdContent": f"正文{suffix}",
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

        fake_portal_client = StreamingPortalClient()

        def portal_factory(**kwargs):
            return fake_portal_client

        with tempfile.TemporaryDirectory() as tmpdir:
            portal_service = KnowledgePortalSyncService(
                output_dir=Path(tmpdir),
                client_factory=portal_factory,
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
                    "page_size": 2,
                    "max_download_files": 1,
                    "include_cover_image": False,
                    "fallback_to_content_markdown": False,
                }
            )

            self.assertEqual(fake_portal_client.list_calls, [1])
            self.assertEqual(fake_portal_client.detail_calls, ["doc-1"])
            self.assertEqual(fake_portal_client.download_calls, ["file-1"])
            self.assertEqual(result["total_documents"], 3)
            self.assertTrue(result["download_limit_reached"])
            self.assertEqual(result["imported_document_count"], 1)
            self.assertEqual(result["uploaded_file_count"], 1)
            self.assertEqual(result["updated_document_count"], 1)
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

    def test_import_knowledge_portal_documents_rejects_parser_confiog_typo(self):
        portal_service = FakeKnowledgePortalService({"documents": [], "errors": []})
        ragflow_client = FakeRagflowClient()
        service = RagflowDocumentService(ragflow_client, portal_service)

        with self.assertRaisesRegex(
            ValidationError,
            "document_update.parser_confiog is not supported; did you mean parser_config\\?",
        ):
            service.import_knowledge_portal_documents(
                {
                    "base_url": "https://km.seres.cn",
                    "community_id": "community",
                    "username": "user",
                    "password": "pass",
                    "dataset_id": "kb_123",
                    "document_update": {
                        "enabled": 1,
                        "chunk_method": "naive",
                        "parser_confiog": {"chunk_token_num": 512},
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
