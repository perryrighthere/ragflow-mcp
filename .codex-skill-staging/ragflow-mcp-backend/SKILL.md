---
name: ragflow-mcp-backend
description: Extend, debug, or review the Python backend in the RAGFlow MCP service repository. Use when Codex needs to modify `main.py` or files under `ragflow_service/`, add or change HTTP routes, evolve RAGFlow client calls, adjust config or `.env` handling, preserve the upload-then-metadata orchestration, or update backend unit tests in `tests/`.
---

# RAGFlow MCP Backend

## Overview

Develop the backend as a three-layer service:

- `ragflow_service/ragflow_client.py` handles upstream RAGFlow HTTP requests and normalizes transport or API failures.
- `ragflow_service/document_service.py` validates inputs and orchestrates document workflows.
- `ragflow_service/http_server.py` parses local HTTP requests, owns runtime settings, serves static assets, and maps exceptions to local HTTP responses.

Read the closest source file and matching tests before editing. Keep changes in the layer that owns the behavior.

## Workflow

1. Read `README.md` plus the relevant backend files before editing.
2. If a feature touches upstream RAGFlow endpoints, change `ragflow_client.py` first, then `document_service.py`, then `http_server.py`.
3. If a feature changes externally visible payloads, update the frontend console only after the backend contract is settled.
4. Add or adjust unit tests in `tests/` for the behavior you changed.
5. Run targeted `unittest` coverage before finishing.

## Preserve These Invariants

- Allow bootstrap without RAGFlow credentials. `Settings.from_env()` must keep working when `RAGFLOW_BASE_URL` and `RAGFLOW_API_KEY` are absent so the UI can configure them later.
- Keep `ServiceRuntime` as the single owner of live settings, `.env` persistence, and service re-instantiation.
- Keep `/api/v1/settings` hot-applying RAGFlow client settings, while `SERVICE_HOST` and `SERVICE_PORT` still require a restart to take effect.
- Keep upload as a two-step orchestration: upload files first, then call the document update endpoint for `meta_fields` and optional document settings, then optionally trigger parsing.
- Validate request shapes in `RagflowDocumentService` before the client talks to upstream RAGFlow.
- Normalize `metadata_condition` so only supported operators pass through, and omit `value` for `empty` and `not empty`.
- Preserve the current error boundary: validation errors map to `400`, missing runtime configuration maps to `503`, upstream failures map to `502` or the upstream status code, and unknown failures map to `500`.

## Testing

Run targeted tests from the repo root:

```bash
python3 -m unittest tests.test_document_service tests.test_config tests.test_http_server_static
```

Add a focused test in the closest existing test module when you change:

- `document_service.py`: metadata orchestration, retrieval filters, validation
- `config.py` or runtime config flow: env precedence, `.env` persistence, bootstrap behavior
- `http_server.py`: route parsing, status-code mapping, static asset safety

## References

Read only what you need:

- `references/architecture.md` for file ownership, request flows, and common change paths
- `references/api-contracts.md` for local routes, upstream RAGFlow endpoints, and payload rules
