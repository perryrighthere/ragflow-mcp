# API Contracts

## Local Service Routes

### `GET /`

- Serve `frontend/index.html`.

### `GET /static/<asset>`

- Serve files from `frontend/`.
- Reject path traversal through `resolve_static_path()`.

### `GET /api/v1/settings`

- Return masked runtime settings plus the `.env` file path.

### `PUT /api/v1/settings`

- Accept a JSON object with any of:
  - `ragflow_base_url`
  - `ragflow_api_key`
  - `request_timeout`
  - `server_host`
  - `server_port`
- Apply RAGFlow client changes immediately.
- Persist settings to `.env`.
- Require restart for `server_host` and `server_port` changes to affect the running server.

### `GET /health`

- Proxy `GET /v1/system/healthz` through the configured `RagflowClient`.
- Return `503` until RAGFlow settings exist.

### `GET /api/v1/datasets/{dataset_id}/documents`

- Pass query parameters through to upstream RAGFlow.
- Normalize repeated query keys into lists and single keys into scalars.

### `POST /api/v1/documents/upload`

- Require `multipart/form-data`.
- Require `dataset_id`.
- Accept repeated `files` or `file` parts.
- Accept optional JSON object fields:
  - `shared_meta_fields`
  - `per_file_meta_fields`
  - `parser_config`
- Accept optional scalar fields:
  - `parse_after_upload`
  - `enabled`
  - `chunk_method`

### `PUT /api/v1/documents/{dataset_id}/{document_id}/metadata`

- Accept JSON with required `meta_fields` object.
- Accept optional `enabled`, `name`, `chunk_method`, and `parser_config`.

### `PUT /api/v1/documents/metadata`

- Accept JSON with `dataset_id` and a non-empty `documents` array.
- Loop through `update_document_metadata()` for each item.

### `POST /api/v1/retrieval`

- Accept JSON with required `question`.
- Require at least one of `dataset_ids` or `document_ids`.
- Support optional numeric, boolean, and list fields already wired through `http_server.py` and `document_service.py`.
- Accept `metadata_condition.conditions[]` items with:
  - `name`
  - `comparison_operator`
  - optional `value` only for operators other than `empty` and `not empty`

## Upstream RAGFlow Endpoints Wrapped Today

- `POST /api/v1/datasets/{dataset_id}/documents`
- `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}`
- `GET /api/v1/datasets/{dataset_id}/documents`
- `POST /api/v1/datasets/{dataset_id}/chunks`
- `POST /api/v1/retrieval`
- `GET /v1/system/healthz`

## Response and Error Conventions

- Successful local responses use `{ "success": true, "data": ... }`.
- Error responses use `{ "success": false, "error": { "message": ... } }`.
- `RagflowClient` expects upstream JSON in the `{ "code": 0, "data": ... }` pattern.
- Any upstream non-zero `code` becomes `RagflowAPIError`.
- Invalid upstream JSON becomes a `502`.
