from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ragflow_service.config import Settings
from ragflow_service.exceptions import ConfigError, RagflowAPIError
from ragflow_service.http_server import serve
from ragflow_service.ragflow_client import FileUpload, RagflowClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interact with raw RAGFlow APIs from the CLI or run the FastAPI docs server.",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI docs server.")
    serve_parser.add_argument("--host", help="Override SERVICE_HOST.")
    serve_parser.add_argument("--port", type=int, help="Override SERVICE_PORT.")
    serve_parser.add_argument("--reload", action="store_true", help="Enable Uvicorn reload mode.")

    request_parser = subparsers.add_parser("request", help="Send a raw request to RAGFlow.")
    request_parser.add_argument("method", help="HTTP method, for example GET or POST.")
    request_parser.add_argument("path", help="Raw RAGFlow path, for example /api/v1/retrieval.")
    request_parser.add_argument("--json", dest="json_body", help="JSON request body.")
    request_parser.add_argument("--query", dest="query_json", help="JSON object of query parameters.")
    request_parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="File path for multipart upload. Repeat this flag to upload multiple files.",
    )
    request_parser.add_argument("--no-auth", action="store_true", help="Skip the Authorization header.")
    request_parser.add_argument("--base-url", help="Override RAGFLOW_BASE_URL for this command.")
    request_parser.add_argument("--api-key", help="Override RAGFLOW_API_KEY for this command.")
    request_parser.add_argument("--timeout", type=float, help="Override RAGFLOW_TIMEOUT for this command.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    try:
        if args.command in (None, "serve"):
            serve(
                settings.with_overrides(server_host=getattr(args, "host", None), server_port=getattr(args, "port", None)),
                reload=getattr(args, "reload", False),
            )
            return 0

        if args.command == "request":
            return run_request_command(settings, args)
    except (ConfigError, RagflowAPIError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


def run_request_command(settings: Settings, args: argparse.Namespace) -> int:
    settings = settings.with_overrides(
        ragflow_base_url=args.base_url,
        ragflow_api_key=args.api_key,
        request_timeout=args.timeout,
    )
    if not args.no_auth:
        settings.require_ragflow()
    if not settings.ragflow_base_url:
        raise ConfigError("Missing required env var: RAGFLOW_BASE_URL")

    client = RagflowClient(
        base_url=settings.ragflow_base_url,
        api_key=settings.ragflow_api_key,
        timeout=settings.request_timeout,
    )

    query = _load_json_option(args.query_json, option_name="--query")
    json_body = _load_json_option(args.json_body, option_name="--json")
    use_auth = not args.no_auth

    if args.file:
        uploads = [_load_file(path) for path in args.file]
        response = client.request_multipart(
            args.method,
            args.path,
            files=uploads,
            query=query,
            use_auth=use_auth,
        )
    else:
        response = client.request_json(
            args.method,
            args.path,
            json_body=json_body,
            query=query,
            use_auth=use_auth,
        )

    print(f"HTTP {response.status_code}")
    if isinstance(response.payload, (dict, list)):
        print(json.dumps(response.payload, ensure_ascii=False, indent=2))
    else:
        print(response.payload)
    return 0 if response.status_code < 400 else 1


def _load_json_option(raw: str | None, *, option_name: str) -> dict[str, Any] | None:
    if raw in (None, ""):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option_name} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{option_name} must be a JSON object.")
    return value


def _load_file(path: str) -> FileUpload:
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        raise ValueError(f"File not found: {file_path}")
    return FileUpload(filename=file_path.name, data=file_path.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
