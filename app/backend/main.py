import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from typing import Annotated, Literal

import httpx
import asyncpg
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

from app.backend import auth, export_files, file_reader, masking, public_llm, storage
from app.backend.config import settings

logger = logging.getLogger(__name__)

ProgressReporter = Callable[[str, int, str], Awaitable[None]]
ChatRunner = Callable[[ProgressReporter], Awaitable[dict]]
_login_attempts: dict[str, list[float]] = {}
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_MAX_ATTEMPTS = 8


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await storage.connect_database()
    except Exception as exc:
        logger.warning("PostgreSQL chưa sẵn sàng khi khởi động: %s", exc)
    yield
    await storage.close_database()


app = FastAPI(
    title="Data Masking API",
    description="Mask dữ liệu nhạy cảm bằng local LLM trước khi gửi ra Public LLM",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS mở cho dev — production nên giới hạn origin cụ thể
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_PATHS = {
    "/",
    "/health",
    "/auth/register",
    "/auth/login",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


@app.middleware("http")
async def require_authenticated_session(request: Request, call_next):
    """Protect every application API by default and attach its current user."""
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    token = request.cookies.get(settings.AUTH_COOKIE_NAME, "")
    try:
        await storage.connect_database()
        user = await storage.get_user_by_session(auth.hash_session_token(token)) if token else None
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "PostgreSQL chưa sẵn sàng"})
    if user is None:
        return JSONResponse(status_code=401, content={"detail": "Bạn cần đăng nhập"})

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_origins = {
            f"{request.url.scheme}://{request.url.netloc}",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        }
        if origin and origin not in expected_origins:
            return JSONResponse(status_code=403, content={"detail": "Origin không được phép"})

    request.state.user = user
    return await call_next(request)


@app.middleware("http")
async def prevent_stale_static_assets(request, call_next):
    """Buộc trình duyệt kiểm tra lại CSS/JS sau mỗi lần cập nhật local app."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

APP_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = APP_DIR / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class MaskRequest(BaseModel):
    text: str


class MaskResponse(BaseModel):
    session_id: str
    masked_text: str
    entities: list[dict]
    entity_count: int


class UnmaskRequest(BaseModel):
    session_id: str
    text: str


class UnmaskResponse(BaseModel):
    final_text: str


class ProcessRequest(BaseModel):
    text: str
    # Cho phép override system prompt của Public LLM nếu cần
    public_system_prompt: str | None = None


class ProcessResponse(BaseModel):
    session_id: str
    original_text: str
    masked_text: str
    public_llm_response: str
    final_text: str
    entity_count: int


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    text: str
    api_url: HttpUrl
    api_key: str
    model: str
    provider: str = "openai_compatible"
    system_prompt: str | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    conversation_id: str | None = None
    project_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    project_id: str | None = None
    session_id: str
    masked_text: str
    public_llm_response: str
    final_text: str
    entities: list[dict]
    entity_count: int
    sources: list[dict] = Field(default_factory=list)


class ModelListRequest(BaseModel):
    provider: Literal["openai_compatible", "anthropic", "gemini", "xai"]
    api_url: HttpUrl
    api_key: str = Field(min_length=1)


class AvailableModel(BaseModel):
    id: str
    display_name: str


class ModelListResponse(BaseModel):
    models: list[AvailableModel]


class ExportSource(BaseModel):
    name: str = Field(default="Tài liệu", max_length=255)
    excerpt: str = Field(default="", max_length=12_000)


class ExportPayload(BaseModel):
    format: Literal["docx", "xlsx", "pdf"]
    title: str = Field(default="Tổng hợp AI", min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=300_000)
    sources: list[ExportSource] = Field(default_factory=list, max_length=100)


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    memory: str = Field(default="", max_length=20000)


class ConversationUpdatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    project_id: str | None = None


class AuthPayload(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class RegisterPayload(AuthPayload):
    password_confirm: str = Field(min_length=10, max_length=128)


def public_user(user: dict) -> dict:
    return {key: user[key] for key in ("id", "email", "role", "created_at") if key in user}


def set_session_cookie(response: Response, token: str) -> None:
    max_age = settings.AUTH_SESSION_DAYS * 24 * 60 * 60
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


async def issue_session(user: dict, response: Response) -> None:
    token = auth.new_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.AUTH_SESSION_DAYS)
    await storage.create_auth_session(user["id"], auth.hash_session_token(token), expires_at)
    set_session_cookie(response, token)


def login_attempt_key(request: Request, email: str) -> str:
    client_ip = request.client.host if request.client else "unknown"
    return f"{client_ip}:{email}"


def enforce_login_rate_limit(key: str) -> None:
    cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
    attempts = [value for value in _login_attempts.get(key, []) if value > cutoff]
    _login_attempts[key] = attempts
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Đăng nhập sai quá nhiều lần. Hãy thử lại sau 5 phút.",
        )


async def require_database() -> None:
    try:
        await storage.connect_database()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Không kết nối được PostgreSQL. Hãy kiểm tra DATABASE_URL và container database.",
        ) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def web_interface():
    """Giao diện chat bảo mật với Public LLM."""
    return FileResponse(
        FRONTEND_DIR / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/health")
async def health():
    try:
        await storage.connect_database()
        database_status = "ok"
    except Exception:
        database_status = "unavailable"
    return {"status": "ok", "database": database_status}


@app.post("/auth/register", status_code=201)
async def register(req: RegisterPayload, response: Response):
    await require_database()
    if req.password != req.password_confirm:
        raise HTTPException(status_code=400, detail="Mật khẩu nhập lại không khớp")
    try:
        email = auth.normalize_email(req.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    password_hash = await asyncio.to_thread(auth.hash_password, req.password)
    try:
        user, claimed_legacy_data = await storage.create_user(email, password_hash)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Email đã được sử dụng") from exc
    await issue_session(user, response)
    return {"user": public_user(user), "claimed_legacy_data": claimed_legacy_data}


@app.post("/auth/login")
async def login(req: AuthPayload, response: Response, request: Request):
    await require_database()
    try:
        email = auth.normalize_email(req.email)
    except ValueError:
        email = req.email.strip().casefold()
    attempt_key = login_attempt_key(request, email)
    enforce_login_rate_limit(attempt_key)
    user = await storage.get_user_by_email(email, include_password=True)
    password_valid = await asyncio.to_thread(
        auth.verify_password,
        req.password,
        user.get("password_hash") if user else None,
    )
    if user is None or not password_valid:
        _login_attempts.setdefault(attempt_key, []).append(time.monotonic())
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")
    _login_attempts.pop(attempt_key, None)
    await issue_session(user, response)
    return {"user": public_user(user)}


@app.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response):
    token = request.cookies.get(settings.AUTH_COOKIE_NAME, "")
    if token:
        await storage.delete_auth_session(auth.hash_session_token(token))
    response.delete_cookie(
        settings.AUTH_COOKIE_NAME,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )


@app.get("/auth/me")
async def current_account(request: Request):
    return {"user": public_user(request.state.user)}


@app.post("/llm/models", response_model=ModelListResponse)
async def list_llm_models(req: ModelListRequest):
    """Lấy danh sách model bằng API key nhưng không lưu key ở backend."""
    try:
        models = await public_llm.list_public_models(
            provider=req.provider,
            api_url=str(req.api_url),
            api_key=req.api_key.strip(),
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            detail = "API key không hợp lệ hoặc không có quyền xem danh sách model."
        elif status == 429:
            detail = "Nhà cung cấp đang giới hạn request. Hãy thử lại sau."
        else:
            detail = f"Không lấy được danh sách model (HTTP {status})."
        raise HTTPException(status_code=502, detail=detail) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Không kết nối được endpoint danh sách model của nhà cung cấp.",
        ) from exc

    if not models:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy model chat phù hợp. Bạn vẫn có thể nhập model thủ công.",
        )
    return {"models": models}


@app.post("/exports")
async def export_assistant_response(req: ExportPayload):
    """Build an export in memory; no generated document is persisted by the server."""
    try:
        data, media_type, filename = await asyncio.to_thread(
            export_files.build_export,
            req.format,
            req.title,
            req.content,
            [source.model_dump() for source in req.sources],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Không thể tạo file %s", req.format)
        raise HTTPException(status_code=500, detail="Không thể tạo file tải xuống") from exc

    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@app.get("/projects")
async def projects_list(request: Request):
    await require_database()
    return {"projects": await storage.list_projects(request.state.user["id"])}


@app.post("/projects", status_code=201)
async def project_create(req: ProjectPayload, request: Request):
    await require_database()
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Tên Project không được để trống")
    return await storage.create_project(
        request.state.user["id"], req.name, req.description, req.memory
    )


@app.get("/projects/{project_id}")
async def project_detail(project_id: str, request: Request):
    await require_database()
    project = await storage.get_project(project_id, request.state.user["id"])
    if project is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    return project


@app.put("/projects/{project_id}")
async def project_update(project_id: str, req: ProjectPayload, request: Request):
    await require_database()
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Tên Project không được để trống")
    project = await storage.update_project(
        project_id, request.state.user["id"], req.name, req.description, req.memory
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    return project


@app.delete("/projects/{project_id}", status_code=204)
async def project_delete(project_id: str, request: Request):
    await require_database()
    if not await storage.delete_project(project_id, request.state.user["id"]):
        raise HTTPException(status_code=404, detail="Project không tồn tại")


@app.get("/projects/{project_id}/documents")
async def project_documents_list(project_id: str, request: Request):
    await require_database()
    if await storage.get_project(project_id, request.state.user["id"]) is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    return {"documents": await storage.list_project_documents(project_id)}


@app.post("/projects/{project_id}/documents", status_code=201)
async def project_document_upload(
    project_id: str,
    request: Request,
    file: Annotated[UploadFile, File(description="Tài liệu dùng chung của Project")],
):
    await require_database()
    if await storage.get_project(project_id, request.state.user["id"]) is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    try:
        content = await file.read(settings.MAX_UPLOAD_BYTES + 1)
        document_name, document_text = file_reader.extract_text(
            file.filename,
            content,
            max_bytes=settings.MAX_UPLOAD_BYTES,
            max_chars=settings.MAX_EXTRACTED_CHARS,
        )
    except file_reader.FileExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return await storage.create_project_document(
        project_id,
        name=document_name,
        content=document_text,
        size_bytes=len(content),
    )


@app.delete("/projects/{project_id}/documents/{document_id}", status_code=204)
async def project_document_delete(project_id: str, document_id: str, request: Request):
    await require_database()
    if await storage.get_project(project_id, request.state.user["id"]) is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    if not await storage.delete_project_document(project_id, document_id):
        raise HTTPException(status_code=404, detail="Tài liệu không tồn tại trong Project")


@app.get("/conversations")
async def conversations_list(
    request: Request,
    project_id: str | None = None,
    unassigned_only: bool = False,
    search: str = "",
):
    await require_database()
    if project_id and unassigned_only:
        raise HTTPException(
            status_code=400,
            detail="Không thể lọc đồng thời theo Project và hội thoại độc lập",
        )
    if project_id and await storage.get_project(project_id, request.state.user["id"]) is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    return {
        "conversations": await storage.list_conversations(
            request.state.user["id"],
            project_id,
            unassigned_only=unassigned_only,
            search=search,
        )
    }


@app.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str, request: Request):
    await require_database()
    conversation = await storage.get_conversation(conversation_id, request.state.user["id"])
    if conversation is None:
        raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại")
    return conversation


@app.put("/conversations/{conversation_id}")
async def conversation_update(conversation_id: str, req: ConversationUpdatePayload, request: Request):
    await require_database()
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Tên hội thoại không được để trống")
    if req.project_id and await storage.get_project(req.project_id, request.state.user["id"]) is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    conversation = await storage.update_conversation(
        conversation_id,
        request.state.user["id"],
        title=req.title,
        project_id=req.project_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại")
    return conversation


@app.delete("/conversations/{conversation_id}", status_code=204)
async def conversation_delete(conversation_id: str, request: Request):
    await require_database()
    if not await storage.delete_conversation(conversation_id, request.state.user["id"]):
        raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại")


@app.post("/mask", response_model=MaskResponse)
async def mask_endpoint(req: MaskRequest):
    """Bước 1: detect entity nhạy cảm + mask text. Trả về session_id để dùng ở /unmask."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text không được để trống")

    try:
        result = await masking.process_mask(req.text)
    except masking.LocalLLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return result


@app.post("/unmask", response_model=UnmaskResponse)
async def unmask_endpoint(req: UnmaskRequest):
    """Bước 2: thay placeholder trong response của Public LLM về giá trị thật."""
    try:
        result = masking.process_unmask(req.session_id, req.text)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return result


@app.post("/process", response_model=ProcessResponse)
async def process_endpoint(req: ProcessRequest):
    """Pipeline đầy đủ 1 lần gọi: mask -> gọi Public LLM -> unmask.

    Yêu cầu đã cấu hình PUBLIC_LLM_URL / PUBLIC_LLM_API_KEY / PUBLIC_LLM_MODEL
    trong biến môi trường. Nếu chưa cấu hình, trả lỗi 501 (chưa triển khai).
    """
    if not settings.PUBLIC_LLM_URL:
        raise HTTPException(
            status_code=501,
            detail=(
                "Public LLM chưa được cấu hình. Đặt biến môi trường "
                "PUBLIC_LLM_URL, PUBLIC_LLM_API_KEY, PUBLIC_LLM_MODEL rồi thử lại, "
                "hoặc dùng riêng /mask và /unmask để tự ghép với Public LLM bạn chọn."
            ),
        )

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text không được để trống")

    try:
        mask_result = await masking.process_mask(req.text)
    except masking.LocalLLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # Bước 2: gọi Public LLM (import trễ để tránh lỗi nếu module chưa cấu hình xong)
    from app.backend.public_llm import call_public_llm

    try:
        public_response = await call_public_llm(
            mask_result["masked_text"], req.public_system_prompt
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Lỗi gọi Public LLM: {e}") from e

    # Bước 3: unmask
    unmask_result = masking.process_unmask(mask_result["session_id"], public_response)

    return {
        "session_id": mask_result["session_id"],
        "original_text": req.text,
        "masked_text": mask_result["masked_text"],
        "public_llm_response": public_response,
        "final_text": unmask_result["final_text"],
        "entity_count": mask_result["entity_count"],
    }


def _chat_stream(runner: ChatRunner) -> StreamingResponse:
    """Run the chat pipeline in a cancellable task and emit NDJSON progress events."""
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def report(stage: str, progress: int, message: str) -> None:
        await queue.put(
            {
                "type": "progress",
                "stage": stage,
                "progress": max(0, min(100, progress)),
                "message": message,
            }
        )

    async def worker() -> None:
        try:
            result = await runner(report)
            await queue.put({"type": "result", "data": result})
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            await queue.put(
                {
                    "type": "error",
                    "status": exc.status_code,
                    "message": str(exc.detail),
                }
            )
        except Exception:
            logger.exception("Lỗi không mong đợi trong luồng chat")
            await queue.put(
                {
                    "type": "error",
                    "status": 500,
                    "message": "Có lỗi không mong đợi khi xử lý tin nhắn.",
                }
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())

    async def events():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, request: Request):
    """Mask dữ liệu, gọi Public LLM bằng cấu hình tạm thời, rồi unmask."""
    return await _run_chat(req, user_id=request.state.user["id"])


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest, request: Request):
    """Stream progress while processing a regular chat message."""
    return _chat_stream(
        lambda report: _run_chat(req, user_id=request.state.user["id"], progress=report)
    )


@app.post("/chat/file", response_model=ChatResponse)
async def chat_file_endpoint(
    http_request: Request,
    file: Annotated[UploadFile, File(description="TXT, MD, CSV, JSON, PDF, DOCX, XLSX hoặc XLS")],
    api_url: Annotated[HttpUrl, Form()],
    api_key: Annotated[str, Form()],
    model: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "openai_compatible",
    text: Annotated[str, Form()] = "",
    system_prompt: Annotated[str | None, Form()] = None,
    conversation_id: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
):
    """Extract an upload locally, mask its text, then send only masked content outward."""
    try:
        content = await file.read(settings.MAX_UPLOAD_BYTES + 1)
        attachment_name, attachment_text = await asyncio.to_thread(
            file_reader.extract_text,
            file.filename,
            content,
            max_bytes=settings.MAX_UPLOAD_BYTES,
            max_chars=settings.MAX_EXTRACTED_CHARS,
        )
    except file_reader.FileExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()

    user_text = text.strip() or "Hãy đọc, phân tích và trả lời dựa trên nội dung tệp."
    chat_request = ChatRequest(
        text=user_text,
        api_url=api_url,
        api_key=api_key,
        model=model,
        provider=provider,
        system_prompt=system_prompt,
        conversation_id=conversation_id,
        project_id=project_id,
    )
    return await _run_chat(
        chat_request,
        user_id=http_request.state.user["id"],
        attachment_name=attachment_name,
        attachment_text=attachment_text,
    )


@app.post("/chat/file/stream")
async def chat_file_stream_endpoint(
    http_request: Request,
    file: Annotated[UploadFile, File(description="TXT, MD, CSV, JSON, PDF, DOCX, XLSX hoặc XLS")],
    api_url: Annotated[HttpUrl, Form()],
    api_key: Annotated[str, Form()],
    model: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "openai_compatible",
    text: Annotated[str, Form()] = "",
    system_prompt: Annotated[str | None, Form()] = None,
    conversation_id: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
):
    """Stream progress while extracting and processing an uploaded file."""
    try:
        content = await file.read(settings.MAX_UPLOAD_BYTES + 1)
        original_filename = file.filename
    finally:
        await file.close()

    async def run(report: ProgressReporter) -> dict:
        await report("extract", 4, "Đang đọc và trích xuất nội dung file...")
        try:
            attachment_name, attachment_text = await asyncio.to_thread(
                file_reader.extract_text,
                original_filename,
                content,
                max_bytes=settings.MAX_UPLOAD_BYTES,
                max_chars=settings.MAX_EXTRACTED_CHARS,
            )
        except file_reader.FileExtractionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await report("extract", 10, "Đã đọc file, đang chuẩn bị ngữ cảnh...")
        user_text = text.strip() or "Hãy đọc, phân tích và trả lời dựa trên nội dung tệp."
        chat_request = ChatRequest(
            text=user_text,
            api_url=api_url,
            api_key=api_key,
            model=model,
            provider=provider,
            system_prompt=system_prompt,
            conversation_id=conversation_id,
            project_id=project_id,
        )
        return await _run_chat(
            chat_request,
            user_id=http_request.state.user["id"],
            attachment_name=attachment_name,
            attachment_text=attachment_text,
            progress=report,
            initial_progress=12,
        )

    return _chat_stream(run)


async def _run_chat(
    req: ChatRequest,
    *,
    user_id: str,
    attachment_name: str | None = None,
    attachment_text: str | None = None,
    progress: ProgressReporter | None = None,
    initial_progress: int = 5,
) -> dict:
    async def emit(stage: str, percent: int, message: str) -> None:
        if progress:
            await progress(stage, percent, message)

    await emit("prepare", initial_progress, "Đang kiểm tra cấu hình và kết nối dữ liệu...")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text không được để trống")
    if not req.api_key.strip() or not req.model.strip():
        raise HTTPException(status_code=400, detail="API key và model không được để trống")
    if req.provider not in {"openai_compatible", "anthropic", "gemini", "xai"}:
        raise HTTPException(status_code=400, detail="Nhà cung cấp không được hỗ trợ")

    await require_database()

    await emit("context", max(initial_progress + 3, 10), "Đang tải lịch sử và bộ nhớ Project...")
    stored_messages: list[dict] = []
    effective_project_id = req.project_id
    if req.conversation_id:
        conversation = await storage.get_conversation(
            req.conversation_id,
            user_id,
            include_attachment_text=True,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại")
        conversation_project_id = conversation.get("project_id")
        if req.project_id and req.project_id != conversation_project_id:
            raise HTTPException(status_code=400, detail="Cuộc trò chuyện không thuộc Project đã chọn")
        effective_project_id = conversation_project_id
        stored_messages = conversation["messages"][-20:]
    else:
        stored_messages = [item.model_dump() for item in req.history[-20:]]

    project = None
    if effective_project_id:
        project = await storage.get_project(effective_project_id, user_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project không tồn tại")

    conversation_parts: list[str] = []
    detection_parts: list[str] = []
    known_entities: list[dict[str, str]] = []
    used_sources: list[dict[str, str]] = []

    if project:
        project_header = f"[Bối cảnh Project: {project['name']}]"
        if project.get("description"):
            project_header += f"\nMô tả: {project['description']}"
        if project.get("memory"):
            project_header += f"\nThông tin cần ghi nhớ: {project['memory']}"
        conversation_parts.append(project_header)
        detection_parts.append(project_header)

        document_chunks = await storage.get_project_document_context(
            effective_project_id,
            query=req.text,
        )
        if document_chunks:
            seen_document_ids: set[str] = set()
            for item in document_chunks:
                if item["id"] in seen_document_ids:
                    continue
                seen_document_ids.add(item["id"])
                excerpt = " ".join(item["content"].split())[:500]
                used_sources.append(
                    {"id": item["id"], "name": item["name"], "excerpt": excerpt}
                )
            document_context = "[Tài liệu dùng chung của Project]\n" + "\n\n".join(
                f"[Tài liệu: {item['name']}]\n{item['content']}"
                for item in document_chunks
            )
            conversation_parts.append(document_context)
            detection_parts.append(document_context)

        shared_messages = await storage.get_project_context(
            effective_project_id,
            user_id,
            query=req.text,
            exclude_conversation_id=req.conversation_id,
        )
        if shared_messages:
            shared_parts: list[str] = []
            for item in shared_messages:
                label = "Người dùng" if item["role"] == "user" else "Trợ lý"
                item_content = item["content"]
                if item.get("attachment_text"):
                    item_content += (
                        f"\n[Tệp: {item.get('attachment_name') or 'file'}]"
                        f"\n{item['attachment_text']}"
                    )
                shared_parts.append(f"[{item['title']}] {label}: {item_content}")
                known_entities.extend(item.get("entities", []))
            shared_context = "[Ký ức từ các hội thoại khác]\n" + "\n\n".join(shared_parts)
            conversation_parts.append(shared_context)
            detection_parts.append(shared_context)

    for item in stored_messages:
        label = "Người dùng" if item["role"] == "user" else "Trợ lý"
        item_content = item["content"]
        if item.get("attachment_text"):
            item_content += (
                f"\n\n[Tệp đính kèm: {item.get('attachment_name') or 'file'}]"
                f"\n{item['attachment_text']}"
            )
        conversation_parts.append(f"{label}: {item_content}")
        known_entities.extend(item.get("entities", []))

    current_user_content = req.text
    if attachment_text:
        current_user_content += f"\n\n[Tệp đính kèm: {attachment_name}]\n{attachment_text}"
    detection_parts.append(current_user_content)
    conversation_parts.append(f"Người dùng: {current_user_content}")
    response_instruction = "Trợ lý: Hãy trả lời tin nhắn cuối cùng của người dùng."
    if used_sources:
        response_instruction += (
            " Nếu sử dụng thông tin từ tài liệu Project, hãy ghi trích dẫn "
            "[Nguồn: tên file] ngay sau thông tin tương ứng."
        )
    conversation_parts.append(response_instruction)
    conversation_text = "\n\n".join(conversation_parts)

    await emit("mask", 22, "Local LLM đang tìm dữ liệu nhạy cảm...")

    async def report_mask_chunk(current: int, total: int) -> None:
        percent = 22 + round((current / total) * 43)
        await emit(
            "mask",
            percent,
            f"Local LLM đã xử lý phần {current}/{total} và đang che dữ liệu...",
        )

    try:
        mask_result = await masking.process_mask(
            conversation_text,
            known_entities=known_entities,
            detection_text="\n\n".join(detection_parts),
            progress_callback=report_mask_chunk,
        )
    except masking.LocalLLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    from app.backend.public_llm import call_public_llm

    await emit(
        "public_llm",
        72,
        f"Đã che {mask_result['entity_count']} entity, đang chờ Public LLM trả lời...",
    )
    try:
        public_response = await call_public_llm(
            mask_result["masked_text"],
            req.system_prompt,
            api_url=str(req.api_url),
            api_key=req.api_key,
            model=req.model,
            provider=req.provider,
        )
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        if req.provider == "gemini" and status_code == 503:
            detail = (
                "Gemini đang quá tải tạm thời sau 3 lần tự động thử lại. "
                "Hãy gửi lại sau ít phút hoặc đổi sang model gemini-3.5-flash-lite."
            )
        elif req.provider == "gemini" and status_code == 429:
            detail = "Gemini đã vượt giới hạn request/quota. Hãy kiểm tra quota của API key."
        else:
            detail = f"Public LLM trả lỗi HTTP {status_code}"
        try:
            provider_error = e.response.json().get("error", {})
            if (
                not (req.provider == "gemini" and status_code in {429, 503})
                and isinstance(provider_error, dict)
                and provider_error.get("message")
            ):
                detail += f": {provider_error['message']}"
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Không gọi được Public LLM: {e}") from e

    await emit("unmask", 88, "Đang khôi phục dữ liệu trong câu trả lời...")
    unmask_result = masking.process_unmask(mask_result["session_id"], public_response)
    final_text = unmask_result["final_text"]

    await emit("save", 94, "Đang lưu cuộc hội thoại vào PostgreSQL...")
    conversation_id = req.conversation_id
    if conversation_id is None:
        title_source = req.text if not attachment_name else f"{req.text} — {attachment_name}"
        title = " ".join(title_source.strip().split())[:60] or "Cuộc trò chuyện mới"
        conversation = await storage.create_conversation(
            user_id=user_id,
            title=title,
            provider=req.provider,
            model=req.model,
            api_url=str(req.api_url),
            project_id=effective_project_id,
        )
        conversation_id = conversation["id"]
    else:
        await storage.update_conversation_config(
            conversation_id,
            user_id,
            req.provider,
            req.model,
            str(req.api_url),
        )

    await storage.add_exchange(
        conversation_id,
        user_id,
        req.text,
        final_text,
        mask_result["entity_count"],
        mask_result["entities"],
        sources=used_sources,
        attachment_name=attachment_name,
        attachment_text=attachment_text,
    )
    await emit("done", 100, "Hoàn tất.")
    return {
        "conversation_id": conversation_id,
        "project_id": effective_project_id,
        "session_id": mask_result["session_id"],
        "masked_text": mask_result["masked_text"],
        "public_llm_response": public_response,
        "final_text": final_text,
        "entities": mask_result["entities"],
        "entity_count": mask_result["entity_count"],
        "sources": used_sources,
    }
