from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .document_service import RagflowDocumentService
from .exceptions import ConfigError, KnowledgePortalAPIError, LLMAPIError, RagflowAPIError, ValidationError
from .knowledge_portal_service import KnowledgePortalSyncService
from .llm_client import OpenAICompatibleClient
from .qa_service import KnowledgeBaseQAService, get_prompt_template_metadata
from .ragflow_client import FileUpload, RagflowClient, UpstreamResponse

try:
    import uvicorn
    from fastapi import FastAPI, File, Query, Request, UploadFile
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, ConfigDict, Field
except ImportError as exc:  # pragma: no cover - exercised only when deps are missing
    uvicorn = None
    FastAPI = None
    File = Query = Request = UploadFile = None
    FileResponse = JSONResponse = PlainTextResponse = RedirectResponse = Response = None
    StaticFiles = None
    BaseModel = object
    ConfigDict = Field = None
    FASTAPI_IMPORT_ERROR = exc
else:
    FASTAPI_IMPORT_ERROR = None


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"


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


    class QuestionAnswerRequest(BaseModel):
        question: str = Field(..., description="The user question for the knowledge base agent.")
        dataset_ids: list[str] | None = None
        document_ids: list[str] | None = None
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
        temperature: float | None = None
        max_tokens: int | None = None
        system_prompt: str | None = None
        user_prompt_template: str | None = None

        model_config = ConfigDict(extra="allow")


    class KnowledgePortalSyncRequest(BaseModel):
        base_url: str = Field(..., description="Knowledge portal base URL, for example https://km.seres.cn")
        community_id: str = Field(..., description="Knowledge portal access key")
        username: str = Field(..., description="Knowledge portal username for Basic Auth")
        password: str = Field(..., description="Knowledge portal password for Basic Auth")
        type: str = Field(default="mutildoc", description="Portal document type")
        page_size: int = Field(default=100, description="Page size used when traversing the list API")
        max_download_files: int | None = Field(
            default=None,
            description="Maximum number of binary files to download from the attachment API",
        )
        begin_time: str | None = Field(default=None, description="Only fetch documents updated after this time")
        fd_cate_id: str | None = Field(default=None, description="Optional category ID filter")
        timeout: float | None = Field(default=None, description="Optional request timeout override in seconds")

        model_config = ConfigDict(extra="forbid")


    class KnowledgePortalImportRequest(KnowledgePortalSyncRequest):
        dataset_id: str = Field(..., description="RAGFlow dataset ID used for the uploaded portal documents")
        document_update: dict[str, Any] | None = Field(
            default=None,
            description=(
                "Shared payload sent to the RAGFlow document update API after upload. "
                "Supports fields such as meta_fields, chunk_method, parser_config, enabled, and name."
            ),
        )
        parse_after_upload: bool = Field(
            default=False,
            description="Whether to trigger the RAGFlow batch parse API after all document updates succeed",
        )
        include_attachments: bool = Field(
            default=True,
            description="Whether to upload binary attachment files from fdFile",
        )
        include_cover_image: bool = Field(
            default=False,
            description="Whether to upload the cover image file from fdCoverImg",
        )
        fallback_to_content_markdown: bool = Field(
            default=True,
            description="Upload generated content.md when no eligible binary file is available",
        )

        model_config = ConfigDict(extra="forbid")
else:  # pragma: no cover - import guard only
    RetrievalRequest = DocumentUpdateRequest = ParseDocumentsRequest = QuestionAnswerRequest = KnowledgePortalSyncRequest = KnowledgePortalImportRequest = object


class ServiceRuntime:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = self._build_client(settings)
        self._llm_client = self._build_llm_client(settings)
        self._knowledge_portal_service = self._build_knowledge_portal_service(settings)

    def get_client(self) -> RagflowClient:
        if self._client is None:
            raise ConfigError(
                "RAGFlow is not configured. Set RAGFLOW_BASE_URL and RAGFLOW_API_KEY in the environment or .env first."
            )
        return self._client

    def get_settings(self) -> Settings:
        return self._settings

    def get_llm_client(self) -> OpenAICompatibleClient:
        if self._llm_client is None:
            raise ConfigError(
                "LLM is not configured. Set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL in the environment or .env first."
            )
        return self._llm_client

    def build_qa_service(self) -> KnowledgeBaseQAService:
        return KnowledgeBaseQAService(self.get_client(), self.get_llm_client())

    def build_document_service(self) -> RagflowDocumentService:
        return RagflowDocumentService(self.get_client(), self.get_knowledge_portal_service())

    def get_knowledge_portal_service(self) -> KnowledgePortalSyncService:
        return self._knowledge_portal_service

    def _build_client(self, settings: Settings) -> RagflowClient | None:
        if not settings.is_ragflow_configured():
            return None
        return RagflowClient(
            base_url=settings.ragflow_base_url,
            api_key=settings.ragflow_api_key,
            timeout=settings.request_timeout,
        )

    def _build_llm_client(self, settings: Settings) -> OpenAICompatibleClient | None:
        if not settings.is_llm_configured():
            return None
        return OpenAICompatibleClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout,
        )

    def _build_knowledge_portal_service(self, settings: Settings) -> KnowledgePortalSyncService:
        return KnowledgePortalSyncService(default_timeout=settings.request_timeout)


def create_application(settings: Settings | None = None):
    _require_fastapi()
    settings = settings or Settings.from_env()
    runtime = ServiceRuntime(settings)

    app = FastAPI(
        title="RAGFlow Knowledge Base QA Service",
        summary="RAGFlow retrieval proxy with a knowledge base Q&A agent and simple web console",
        description=(
            "This service keeps the raw RAGFlow proxy routes and adds a simple knowledge base "
            "Q&A API backed by an OpenAI-compatible LLM."
        ),
        version="3.0.0",
    )
    app.state.runtime = runtime

    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.exception_handler(ConfigError)
    async def handle_config_error(request: Any, exc: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(RagflowAPIError)
    async def handle_ragflow_error(request: Any, exc: RagflowAPIError) -> JSONResponse:
        content: dict[str, Any] = {"detail": str(exc)}
        if exc.payload:
            content["payload"] = exc.payload
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(LLMAPIError)
    async def handle_llm_error(request: Any, exc: LLMAPIError) -> JSONResponse:
        content: dict[str, Any] = {"detail": str(exc)}
        if exc.payload:
            content["payload"] = exc.payload
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(KnowledgePortalAPIError)
    async def handle_knowledge_portal_error(request: Any, exc: KnowledgePortalAPIError) -> JSONResponse:
        content: dict[str, Any] = {"detail": str(exc)}
        if exc.payload:
            content["payload"] = exc.payload
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(ValidationError)
    async def handle_validation_error(request: Any, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def root() -> Response:
        if FRONTEND_INDEX.is_file():
            return FileResponse(FRONTEND_INDEX)
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

    @app.post("/api/v1/qa/answer", tags=["Knowledge Base QA"])
    async def answer_question(payload: QuestionAnswerRequest) -> JSONResponse:
        qa_service = runtime.build_qa_service()
        answer = qa_service.answer_question(payload.model_dump(exclude_none=True))
        return JSONResponse(status_code=200, content={"code": 0, "data": answer})

    @app.get("/api/v1/qa/prompt-templates", tags=["Knowledge Base QA"])
    async def get_prompt_templates() -> JSONResponse:
        return JSONResponse(status_code=200, content={"code": 0, "data": get_prompt_template_metadata()})

    @app.post("/api/v1/knowledge-portal/documents/sync", tags=["Knowledge Portal"])
    async def sync_knowledge_portal_documents(payload: KnowledgePortalSyncRequest) -> JSONResponse:
        service = runtime.get_knowledge_portal_service()
        result = service.sync_documents(payload.model_dump(exclude_none=True))
        return JSONResponse(status_code=200, content={"code": 0, "data": result})

    @app.post("/api/v1/knowledge-portal/documents/import", tags=["Knowledge Portal"])
    async def import_knowledge_portal_documents(payload: KnowledgePortalImportRequest) -> JSONResponse:
        service = runtime.build_document_service()
        result = service.import_knowledge_portal_documents(payload.model_dump(exclude_none=True))
        return JSONResponse(status_code=200, content={"code": 0, "data": result})

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
