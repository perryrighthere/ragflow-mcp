# Backend Architecture

## File Ownership

| File | Responsibility | Notes |
| --- | --- | --- |
| `main.py` | Startup entrypoint | Calls `serve()` only. |
| `ragflow_service/config.py` | Environment loading and persistence | Allows empty RAGFlow config at bootstrap and writes `.env` in a stable key order. |
| `ragflow_service/ragflow_client.py` | Raw upstream RAGFlow HTTP calls | Uses `urllib`, builds multipart uploads, and converts transport or API failures into `RagflowAPIError`. |
| `ragflow_service/document_service.py` | Validation and orchestration | Owns upload-plus-metadata behavior, metadata filter normalization, and batch update looping. |
| `ragflow_service/http_server.py` | Local HTTP server and runtime state | Parses requests, serves static assets, maps exceptions to status codes, and updates live settings. |
| `tests/test_document_service.py` | Service orchestration tests | Uses a fake client to assert payload shapes and validation behavior. |
| `tests/test_config.py` | Config and runtime tests | Covers dotenv precedence, bootstrap without RAGFlow config, and runtime setting updates. |
| `tests/test_http_server_static.py` | Static asset safety tests | Verifies asset availability and path traversal protection. |

## Main Request Flows

### Startup and Runtime Settings

1. `main.py` calls `serve()`.
2. `serve()` loads `Settings.from_env()`.
3. `create_application()` builds `ServiceRuntime`.
4. `ServiceRuntime._build_service()` returns `None` until both `RAGFLOW_BASE_URL` and `RAGFLOW_API_KEY` exist.
5. `runtime.get_service()` raises `ConfigError` when unconfigured, which becomes a `503` response.
6. `runtime.update_settings()` validates the payload, writes `.env`, and rebuilds the live `RagflowDocumentService`.

### Upload With Metadata

1. `/api/v1/documents/upload` accepts multipart form data in `http_server.py`.
2. `_parse_upload_form()` converts uploaded parts into `FileUpload` objects plus optional JSON fields.
3. `RagflowDocumentService.upload_documents()` calls `client.upload_documents()`.
4. The service merges `shared_meta_fields` with any filename-specific override from `per_file_meta_fields`.
5. The service calls `client.update_document()` once per returned document when metadata or document options are present.
6. The service optionally calls `client.parse_documents()` after all updates.

### Retrieval With Metadata Filters

1. `/api/v1/retrieval` parses JSON and coerces booleans, integers, and floats in `http_server.py`.
2. `RagflowDocumentService.retrieve_chunks()` validates that `question` exists and at least one of `dataset_ids` or `document_ids` is present.
3. `_normalize_metadata_condition()` enforces the current operator allowlist and strips `value` for `empty` and `not empty`.
4. `RagflowClient.retrieve_chunks()` sends the normalized payload upstream.

## Change Checklist

- Add upstream endpoint wrappers in `ragflow_client.py` before touching local routes.
- Keep business rules out of `RagflowClient`; put validation and orchestration in `RagflowDocumentService`.
- Reuse the existing `ValidationError`, `ConfigError`, and `RagflowAPIError` types instead of adding ad hoc exception paths.
- Keep request coercion helpers in `http_server.py` consistent when adding new fields.
- Update the frontend console only after the backend contract is stable.
