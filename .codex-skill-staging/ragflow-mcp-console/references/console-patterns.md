# Console Patterns

## File Ownership

| File | Responsibility | Notes |
| --- | --- | --- |
| `frontend/index.html` | Form markup and result panes | Defines every operator workflow the console exposes. |
| `frontend/app.js` | Event wiring, parsing, fetch flow, previews, logs | Centralizes all request construction and client-side validation. |
| `frontend/app.css` | Layout, theme, responsive rules | Uses a warm serif-heavy visual system without external dependencies. |
| `ragflow_service/http_server.py` | Route contract for the console | Must stay in sync with frontend forms and payload shapes. |

## JavaScript Structure

- Register event listeners near the top of `app.js`.
- Keep one `handle*` function per form or action.
- Build payloads in the handler, then pass the request to `sendRequest()`.
- Reuse helpers such as:
  - `parseOptionalJsonObject()`
  - `parseOptionalInteger()`
  - `parseOptionalNumber()`
  - `parseCsv()`
  - `assignIfPresent()`
  - `appendOptionalFormField()`
  - `summarizeFormData()`
- Route all user-facing input errors through `throwUserError()`.

## HTML and CSS Structure

- Keep each major workflow in its own `.panel`.
- Use `.form-grid`, `.wide`, `.toggle`, `.actions`, and `.split-actions` for consistent spacing.
- Keep the sticky result pane intact so request and response previews remain visible during manual testing.
- Extend the existing palette and typography unless the task explicitly asks for a redesign.

## Extension Checklist

1. Add or update the form markup in `index.html`.
2. Add or update the matching event listener in `app.js`.
3. Implement or adjust the `handle*` function.
4. Reuse shared helpers before adding new parsing logic.
5. Confirm the backend route and payload fields in `http_server.py`.
6. Add CSS only if the existing utility classes do not cover the new UI.
