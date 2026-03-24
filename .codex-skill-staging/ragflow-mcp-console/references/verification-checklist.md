# Verification Checklist

## Before Testing

- Start the service from the repo root with `python3 main.py`.
- Open the local console in a browser.
- If RAGFlow credentials are not configured yet, use the settings panel first.

## After Backend-Linked UI Changes

1. Load the page and confirm the edited panel renders without layout breakage.
2. Submit one valid request through the changed flow.
3. Confirm the request preview matches the intended payload shape.
4. Confirm the response preview shows parsed JSON or a useful error.
5. Confirm the activity log records success or failure clearly.
6. Submit one invalid input case and confirm the UI reports the validation issue cleanly.

## High-Risk Areas

- File upload routes must keep using `FormData`.
- JSON textareas must continue using the shared JSON parsing helpers.
- Query-string routes must keep repeated keys encoded correctly.
- Any new log text must remain HTML-escaped.
