from __future__ import annotations

from typing import Any

from .config import Settings
from .exceptions import ConfigError, RagflowAPIError
from .ragflow_client import FileUpload, RagflowClient, UpstreamResponse

try:
    import uvicorn
    from fastapi import FastAPI, File, Query, Request, UploadFile
    from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
    from pydantic import BaseModel, ConfigDict, Field
except ImportError as exc:  # pragma: no cover - exercised only when deps are missing
    uvicorn = None
    FastAPI = None
    File = Query = Request = UploadFile = None
    JSONResponse = PlainTextResponse = RedirectResponse = Response = None
    BaseModel = object
    ConfigDict = Field = None
    FASTAPI_IMPORT_ERROR = exc
else:
    FASTAPI_IMPORT_ERROR = None


def _require_fastapi() -> None:
    if FASTAPI_IMPORT_ERROR is not None:
        raise RuntimeError(
            "FastAPI dependencies are not installed. Activate the project's .venv or install "
            "`fastapi`, `uvicorn`, and `python-multipart` first."
        ) from FASTAPI_IMPORT_ERROR


if FASTAPI_IMPORT_ERROR is None:
    class RetrievalRequest(BaseModel):
        question: str = Field(..., description="The user query.")
        dataset_ids: list[str] | None = Field(default=None, description="Dataset IDs to search.")
        document_ids: list[str] | None = Field(default=None, description="Document IDs to search.")
        page: int | None = None
        page_size: int | None = None
        similarity_threshold: float | None = None
        vector_similarity_weight: float | None = None
        top_k: int | None = None
        rerank_id: str | int | None = None
        keyword: bool | None = None
        highlight: bool | None = None
        cross_languages: list[str] | None = None
        metadata_condition: dict[str, Any] | None = None
        use_kg: bool | None = None

        model_config = ConfigDict(extra="allow")


    class DocumentUpdateRequest(BaseModel):
        model_config = ConfigDict(extra="allow")


    class ParseDocumentsRequest(BaseModel):
        document_ids: list[str]
        model_config = ConfigDict(extra="allow")
else:  # pragma: no cover - import guard only
    RetrievalRequest = DocumentUpdateRequest = ParseDocumentsRequest = object


class ServiceRuntime:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = self._build_client(settings)

    def get_client(self) -> RagflowClient:
        if self._client is None:
            raise ConfigError(
                "RAGFlow is not configured. Set RAGFLOW_BASE_URL and RAGFLOW_API_KEY in the environment or .env first."
            )
        return self._client

    def get_settings(self) -> Settings:
        return self._settings

    def _build_client(self, settings: Settings) -> RagflowClient | None:
        if not settings.is_ragflow_configured():
            return None
        return RagflowClient(
            base_url=settings.ragflow_base_url,
            api_key=settings.ragflow_api_key,
            timeout=settings.request_timeout,
        )


def create_application(settings: Settings | None = None):
    _require_fastapi()
    settings = settings or Settings.from_env()
    runtime = ServiceRuntime(settings)

    app = FastAPI(
        title="RAGFlow Raw API CLI Proxy",
        summary="Thin FastAPI layer over raw RAGFlow APIs",
        description=(
            "This service exposes the raw RAGFlow endpoints currently used in this repository. "
            "Use `/docs` for interactive requests or the CLI command in `main.py`."
        ),
        version="2.0.0",
    )
    app.state.runtime = runtime

    @app.exception_handler(ConfigError)
    async def handle_config_error(request: Any, exc: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(RagflowAPIError)
    async def handle_ragflow_error(request: Any, exc: RagflowAPIError) -> JSONResponse:
        content: dict[str, Any] = {"detail": str(exc)}
        if exc.payload:
            content["payload"] = exc.payload
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/v1/system/healthz", tags=["RAGFlow Raw APIs"])
    async def healthz() -> Response:
        client = runtime.get_client()
        upstream = client.healthz()
        return _response_from_upstream(upstream)

    @app.post("/api/v1/retrieval", tags=["RAGFlow Raw APIs"])
    async def retrieve_chunks(payload: RetrievalRequest) -> Response:
        client = runtime.get_client()
        upstream = client.retrieve_chunks(payload.model_dump(exclude_none=True))
        return _response_from_upstream(upstream)

    @app.get("/api/v1/datasets/{dataset_id}/documents", tags=["RAGFlow Raw APIs"])
    async def list_documents(
        dataset_id: str,
        page: int | None = Query(default=None),
        page_size: int | None = Query(default=None),
        keywords: str | None = Query(default=None),
        name: str | None = Query(default=None),
        run: list[str] | None = Query(default=None),
        suffix: list[str] | None = Query(default=None),
        orderby: str | None = Query(default=None),
        desc: bool | None = Query(default=None),
        id: str | None = Query(default=None),
    ) -> Response:
        client = runtime.get_client()
        query = _drop_none_values(
            {
                "page": page,
                "page_size": page_size,
                "keywords": keywords,
                "name": name,
                "run": run,
                "suffix": suffix,
                "orderby": orderby,
                "desc": desc,
                "id": id,
            }
        )
        upstream = client.list_documents(dataset_id, query)
        return _response_from_upstream(upstream)

    @app.post("/api/v1/datasets/{dataset_id}/documents", tags=["RAGFlow Raw APIs"])
    async def upload_documents(dataset_id: str, files: list[UploadFile] = File(...)) -> Response:
        client = runtime.get_client()
        uploads: list[FileUpload] = []
        for file in files:
            uploads.append(
                FileUpload(
                    filename=file.filename or "upload.bin",
                    data=await file.read(),
                    content_type=file.content_type,
                )
            )
        upstream = client.upload_documents(dataset_id, uploads)
        return _response_from_upstream(upstream)

    @app.put("/api/v1/datasets/{dataset_id}/documents/{document_id}", tags=["RAGFlow Raw APIs"])
    async def update_document(dataset_id: str, document_id: str, payload: DocumentUpdateRequest) -> Response:
        client = runtime.get_client()
        upstream = client.update_document(dataset_id, document_id, payload.model_dump())
        return _response_from_upstream(upstream)

    @app.post("/api/v1/datasets/{dataset_id}/chunks", tags=["RAGFlow Raw APIs"])
    async def parse_documents(dataset_id: str, payload: ParseDocumentsRequest) -> Response:
        client = runtime.get_client()
        upstream = client.parse_documents(dataset_id, payload.model_dump(exclude_none=True))
        return _response_from_upstream(upstream)

    return app


def serve(settings: Settings | None = None, *, reload: bool = False) -> None:
    _require_fastapi()
    settings = settings or Settings.from_env()
    app = create_application(settings)
    uvicorn.run(app, host=settings.server_host, port=settings.server_port, reload=reload)


def _response_from_upstream(upstream: UpstreamResponse) -> Response:
    if isinstance(upstream.payload, (dict, list)):
        return JSONResponse(status_code=upstream.status_code, content=upstream.payload)
    return PlainTextResponse(status_code=upstream.status_code, content=str(upstream.payload))


def _drop_none_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
