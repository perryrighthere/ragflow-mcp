---
name: ragflow-mcp-console
description: Extend, debug, or restyle the zero-dependency web console in the RAGFlow MCP service repository. Use when Codex needs to modify `frontend/index.html`, `frontend/app.js`, or `frontend/app.css`, add or change form flows tied to backend endpoints, adjust request serialization and previews, or keep the UI aligned with server routes and payload shapes.
---

# RAGFlow MCP Console

## Overview

Treat the console as a static operator tool with no build step. Keep the HTML form structure, the JavaScript request pipeline, and the CSS visual language aligned with the backend routes in `ragflow_service/http_server.py`.

## Workflow

1. Read the matching backend route before editing the UI for it.
2. Keep the existing form pattern: HTML fields in `index.html` feed a `handle*` function in `app.js`, which builds payloads and sends them through `sendRequest()`.
3. Reuse the shared parsing helpers in `app.js` instead of adding route-specific parsing unless the new field type truly needs it.
4. Keep request preview, response preview, and activity log behavior intact so the page remains useful for manual verification.
5. Add CSS only for new layout or interaction needs. Stay close to the current warm editorial theme unless the user asks for a redesign.

## Preserve These Invariants

- Serve the console as plain static files through `/` and `/static/*`. Do not introduce a build pipeline.
- Keep frontend validation lightweight and user-facing. Use `throwUserError()` for form issues and let backend validation remain authoritative.
- Keep `sendRequest()` as the shared place for fetch wiring, preview rendering, response parsing, and log messages.
- Keep file uploads using `FormData`, while JSON routes continue using `Content-Type: application/json`.
- Escape log content before injecting HTML.
- Keep the UI coupled to real backend paths. Do not invent frontend-only route shapes.

## Testing

After UI changes, manually verify the changed flow in the browser and run the backend tests that protect static asset serving:

```bash
python3 -m unittest tests.test_http_server_static
```

If the UI change depends on a backend contract change, also run the backend test module closest to that contract.

## References

Read only what you need:

- `references/console-patterns.md` for the current form, handler, helper, and styling structure
- `references/verification-checklist.md` for a quick manual test loop after UI edits
