import unittest

from ragflow_service.document_service import RagflowDocumentService
from ragflow_service.exceptions import ValidationError
from ragflow_service.ragflow_client import FileUpload


class FakeClient:
    def __init__(self):
        self.upload_calls = []
        self.update_calls = []
        self.parse_calls = []
        self.retrieve_calls = []
        self.list_calls = []

    def upload_documents(self, dataset_id, files):
        self.upload_calls.append((dataset_id, files))
        return [
            {"id": "doc-1", "name": "a.txt", "location": "a.txt"},
            {"id": "doc-2", "name": "b.txt", "location": "b.txt"},
        ]

    def update_document(self, dataset_id, document_id, payload):
        self.update_calls.append((dataset_id, document_id, payload))
        return None

    def parse_documents(self, dataset_id, document_ids):
        self.parse_calls.append((dataset_id, document_ids))
        return {"started": True}

    def retrieve_chunks(self, payload):
        self.retrieve_calls.append(payload)
        return {"chunks": [], "total": 0}

    def list_documents(self, dataset_id, query):
        self.list_calls.append((dataset_id, query))
        return {"docs": []}

    def healthz(self):
        return {"status": "ok"}


class RagflowDocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.service = RagflowDocumentService(self.client)

    def test_upload_documents_updates_metadata_and_starts_parse(self):
        result = self.service.upload_documents(
            dataset_id="dataset-1",
            files=[
                FileUpload(filename="a.txt", data=b"a"),
                FileUpload(filename="b.txt", data=b"b"),
            ],
            shared_meta_fields={"tenant": "acme"},
            per_file_meta_fields={"b.txt": {"department": "legal"}},
            parse_after_upload=True,
            enabled=1,
        )

        self.assertEqual(result["dataset_id"], "dataset-1")
        self.assertEqual(len(self.client.update_calls), 2)
        self.assertEqual(
            self.client.update_calls[0],
            ("dataset-1", "doc-1", {"meta_fields": {"tenant": "acme"}, "enabled": 1}),
        )
        self.assertEqual(
            self.client.update_calls[1],
            (
                "dataset-1",
                "doc-2",
                {"meta_fields": {"tenant": "acme", "department": "legal"}, "enabled": 1},
            ),
        )
        self.assertEqual(self.client.parse_calls, [("dataset-1", ["doc-1", "doc-2"])])

    def test_retrieve_chunks_uses_metadata_condition_shape(self):
        result = self.service.retrieve_chunks(
            question="find policies",
            dataset_ids=["dataset-1"],
            metadata_condition={
                "conditions": [
                    {"name": "tenant", "comparison_operator": "=", "value": "acme"},
                    {"name": "department", "comparison_operator": "not empty"},
                ]
            },
            highlight=True,
        )

        self.assertEqual(result["total"], 0)
        self.assertEqual(
            self.client.retrieve_calls[0]["metadata_condition"],
            {
                "conditions": [
                    {"name": "tenant", "comparison_operator": "=", "value": "acme"},
                    {"name": "department", "comparison_operator": "not empty"},
                ]
            },
        )
        self.assertTrue(self.client.retrieve_calls[0]["highlight"])

    def test_update_document_metadata_requires_meta_object(self):
        with self.assertRaises(ValidationError):
            self.service.update_document_metadata(
                dataset_id="dataset-1",
                document_id="doc-1",
                meta_fields=None,
            )


if __name__ == "__main__":
    unittest.main()
