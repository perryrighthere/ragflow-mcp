# RAGFlow Document Import Orchestration

Use this reference when changing `RagflowDocumentService.import_knowledge_portal_documents()` or debugging knowledge-portal imports where files upload but do not parse.

## Core Flow

The import flow is upload-first, metadata-second, parse-last:

1. Stream portal documents from `KnowledgePortalSyncService.start_sync(...)`.
2. Build upload sources from downloaded attachments or fallback `content.md`.
3. Upload files to RAGFlow with `POST /api/v1/datasets/{dataset_id}/documents`.
4. Update each returned RAGFlow document with `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}`.
5. Sync `knowledgeDatabaseId`, `ragFileId`, `originFileId`, and `tenantId` through `RAG_INFO_SYNC_URL`.
6. If `parse_after_upload=true`, trigger parsing with `POST /api/v1/datasets/{dataset_id}/chunks`.

Do not assume that every uploaded document was successfully updated. RAGFlow may show an uploaded document in the UI even when this service's update step failed.

## RAGFlow State Is The Source Of Truth Before Parse

Before deciding parse groups, re-check each uploaded candidate with:

```http
GET /api/v1/datasets/{dataset_id}/documents?id={document_id}
```

Use the listed RAGFlow document fields to classify parse behavior:

- `name`
- `location`
- `type`
- `chunk_method`
- `parser_config`
- `run`

This matters because portal filenames, local saved filenames, upload response fields, and RAGFlow's final document record may differ. If a file appears in the RAGFlow UI as `.pptx` / `presentation`, trust the listed RAGFlow document over the earlier upload-source guess.

## PPTX / Presentation Parsing Lessons

If PPTX documents upload but do not parse:

- Do not conclude RAGFlow cannot parse PPTX until manual `POST /chunks` has been tried with only PPTX document IDs.
- Check whether the service actually sent a presentation parse batch. The expected log is:
  `RAGFlow parse batch -> group=presentation count=...`
- If only `group=default` appears, the PPTX was not in this service's parse candidates or was misclassified.
- UI evidence can be decisive: if RAGFlow shows the document as `presentation` with chunk count `0`, the issue is usually orchestration, not file support.

Correct presentation update payload:

```json
{
  "chunk_method": "presentation",
  "parser_config": {
    "raptor": {
      "use_raptor": false
    }
  }
}
```

Do not send `parser_config: {}` for `presentation`. In RAGFlow's HTTP API reference, `{}` applies to methods like `table`, `picture`, `one`, and `email`. `presentation` belongs with methods whose parser config contains only `raptor`.

When the listed RAGFlow document is PPTX/presentation but its current `chunk_method` is not `presentation`, patch it before parsing with `PUT /documents/{document_id}` using the payload above.

## Parse Candidate Rules

Include all successfully updated documents in parse candidates.

Also include uploaded documents whose update failed when they may be presentation documents. On re-check:

- If the RAGFlow document is `.pptx` or `presentation`, ensure presentation config and parse it.
- If it is not presentation and update failed, skip parsing to avoid parsing ordinary files with incomplete metadata/config.

Separate parse batches by method:

- `default`: general documents such as PDF, DOCX, XLSX, Markdown
- `presentation`: PPTX/presentation documents

Call `POST /api/v1/datasets/{dataset_id}/chunks` once per non-empty group. This mirrors successful manual PPTX-only parse requests and makes logs easier to audit.

## Logging To Keep

Use logs that make the classification auditable:

- Initial upload/update classification:
  `RAGFlow import document classified -> document_id=... name=... chunk_method=... parse_group=... upload_source=...`
- Re-checked RAGFlow state before parse:
  `RAGFlow parse candidate classified -> document_id=... upload_name=... ragflow_name=... ragflow_location=... ragflow_type=... ragflow_chunk_method=... parse_group=...`
- Config repair:
  `RAGFlow presentation config ensured -> document_id=...`
- Actual parse request grouping:
  `RAGFlow parse batch -> group=... count=... document_ids=...`

Never log raw `Authorization` tokens. Redact RAGFlow auth headers in both structured request logs and generated curl commands.

## Tests To Add Or Preserve

Use `tests/test_document_service.py` for orchestration behavior. Cover these cases:

- Normal upload-update-parse for non-presentation files.
- PPTX gets `chunk_method=presentation` and `parser_config={"raptor": {"use_raptor": false}}`.
- Mixed non-PPTX and PPTX imports produce separate parse calls.
- RAGFlow upload response or list response names a document as `.pptx` even if the local upload source does not.
- Initial update fails, but the uploaded RAGFlow document is listed as PPTX; the service should ensure presentation config and parse it.

Use `tests/test_ragflow_client.py` for request logging and auth redaction.
