import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
import json
import logging
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, HttpUrl

from app import file_reader, masking, storage
from app.config import settings

logger = logging.getLogger(__name__)

ProgressReporter = Callable[[str, int, str], Awaitable[None]]
ChatRunner = Callable[[ProgressReporter], Awaitable[dict]]


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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parent


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


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    memory: str = Field(default="", max_length=20000)


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
    """Giao diện web đơn giản để sử dụng các chức năng mask/unmask."""
    return FileResponse(
        WEB_DIR / "web.html",
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


@app.get("/projects")
async def projects_list():
    await require_database()
    return {"projects": await storage.list_projects()}


@app.post("/projects", status_code=201)
async def project_create(req: ProjectPayload):
    await require_database()
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Tên Project không được để trống")
    return await storage.create_project(req.name, req.description, req.memory)


@app.get("/projects/{project_id}")
async def project_detail(project_id: str):
    await require_database()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    return project


@app.put("/projects/{project_id}")
async def project_update(project_id: str, req: ProjectPayload):
    await require_database()
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Tên Project không được để trống")
    project = await storage.update_project(project_id, req.name, req.description, req.memory)
    if project is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    return project


@app.delete("/projects/{project_id}", status_code=204)
async def project_delete(project_id: str):
    await require_database()
    if not await storage.delete_project(project_id):
        raise HTTPException(status_code=404, detail="Project không tồn tại")


@app.get("/conversations")
async def conversations_list(
    project_id: str | None = None,
    unassigned_only: bool = False,
):
    await require_database()
    if project_id and unassigned_only:
        raise HTTPException(
            status_code=400,
            detail="Không thể lọc đồng thời theo Project và hội thoại độc lập",
        )
    if project_id and await storage.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại")
    return {
        "conversations": await storage.list_conversations(
            project_id,
            unassigned_only=unassigned_only,
        )
    }


@app.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str):
    await require_database()
    conversation = await storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại")
    return conversation


@app.delete("/conversations/{conversation_id}", status_code=204)
async def conversation_delete(conversation_id: str):
    await require_database()
    if not await storage.delete_conversation(conversation_id):
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
    from app.public_llm import call_public_llm

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
async def chat_endpoint(req: ChatRequest):
    """Mask dữ liệu, gọi Public LLM bằng cấu hình tạm thời, rồi unmask."""
    return await _run_chat(req)


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    """Stream progress while processing a regular chat message."""
    return _chat_stream(lambda report: _run_chat(req, progress=report))


@app.post("/chat/file", response_model=ChatResponse)
async def chat_file_endpoint(
    file: Annotated[UploadFile, File(description="TXT, MD, CSV, JSON, PDF hoặc DOCX")],
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
        attachment_name, attachment_text = file_reader.extract_text(
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
    request = ChatRequest(
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
        request,
        attachment_name=attachment_name,
        attachment_text=attachment_text,
    )


@app.post("/chat/file/stream")
async def chat_file_stream_endpoint(
    file: Annotated[UploadFile, File(description="TXT, MD, CSV, JSON, PDF hoặc DOCX")],
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
            attachment_name, attachment_text = file_reader.extract_text(
                original_filename,
                content,
                max_bytes=settings.MAX_UPLOAD_BYTES,
                max_chars=settings.MAX_EXTRACTED_CHARS,
            )
        except file_reader.FileExtractionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await report("extract", 10, "Đã đọc file, đang chuẩn bị ngữ cảnh...")
        user_text = text.strip() or "Hãy đọc, phân tích và trả lời dựa trên nội dung tệp."
        request = ChatRequest(
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
            request,
            attachment_name=attachment_name,
            attachment_text=attachment_text,
            progress=report,
            initial_progress=12,
        )

    return _chat_stream(run)


async def _run_chat(
    req: ChatRequest,
    *,
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
    if req.provider not in {"openai_compatible", "anthropic", "gemini"}:
        raise HTTPException(status_code=400, detail="Nhà cung cấp không được hỗ trợ")

    await require_database()

    await emit("context", max(initial_progress + 3, 10), "Đang tải lịch sử và bộ nhớ Project...")
    stored_messages: list[dict] = []
    effective_project_id = req.project_id
    if req.conversation_id:
        conversation = await storage.get_conversation(
            req.conversation_id,
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
        project = await storage.get_project(effective_project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project không tồn tại")

    conversation_parts: list[str] = []
    detection_parts: list[str] = []
    known_entities: list[dict[str, str]] = []

    if project:
        project_header = f"[Bối cảnh Project: {project['name']}]"
        if project.get("description"):
            project_header += f"\nMô tả: {project['description']}"
        if project.get("memory"):
            project_header += f"\nThông tin cần ghi nhớ: {project['memory']}"
        conversation_parts.append(project_header)
        detection_parts.append(project_header)

        shared_messages = await storage.get_project_context(
            effective_project_id,
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
    conversation_parts.append("Trợ lý: Hãy trả lời tin nhắn cuối cùng của người dùng.")
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

    from app.public_llm import call_public_llm

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
            req.provider,
            req.model,
            str(req.api_url),
        )

    await storage.add_exchange(
        conversation_id,
        req.text,
        final_text,
        mask_result["entity_count"],
        mask_result["entities"],
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
    }
