from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl

from app import file_reader, masking, storage
from app.config import settings

logger = logging.getLogger(__name__)


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


class ChatResponse(BaseModel):
    conversation_id: str
    session_id: str
    masked_text: str
    public_llm_response: str
    final_text: str
    entities: list[dict]
    entity_count: int


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


@app.get("/conversations")
async def conversations_list():
    await require_database()
    return {"conversations": await storage.list_conversations()}


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


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    """Mask dữ liệu, gọi Public LLM bằng cấu hình tạm thời, rồi unmask."""
    return await _run_chat(req)


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
    )
    return await _run_chat(
        request,
        attachment_name=attachment_name,
        attachment_text=attachment_text,
    )


async def _run_chat(
    req: ChatRequest,
    *,
    attachment_name: str | None = None,
    attachment_text: str | None = None,
) -> dict:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text không được để trống")
    if not req.api_key.strip() or not req.model.strip():
        raise HTTPException(status_code=400, detail="API key và model không được để trống")
    if req.provider not in {"openai_compatible", "anthropic", "gemini"}:
        raise HTTPException(status_code=400, detail="Nhà cung cấp không được hỗ trợ")

    await require_database()

    stored_messages: list[dict] = []
    if req.conversation_id:
        conversation = await storage.get_conversation(
            req.conversation_id,
            include_attachment_text=True,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại")
        stored_messages = conversation["messages"][-20:]
    else:
        stored_messages = [item.model_dump() for item in req.history[-20:]]

    conversation_parts = []
    known_entities: list[dict[str, str]] = []
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
    conversation_parts.append(f"Người dùng: {current_user_content}")
    conversation_parts.append("Trợ lý: Hãy trả lời tin nhắn cuối cùng của người dùng.")
    conversation_text = "\n\n".join(conversation_parts)

    try:
        mask_result = await masking.process_mask(
            conversation_text,
            known_entities=known_entities,
            detection_text=current_user_content,
        )
    except masking.LocalLLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    from app.public_llm import call_public_llm

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

    unmask_result = masking.process_unmask(mask_result["session_id"], public_response)
    final_text = unmask_result["final_text"]

    conversation_id = req.conversation_id
    if conversation_id is None:
        title_source = req.text if not attachment_name else f"{req.text} — {attachment_name}"
        title = " ".join(title_source.strip().split())[:60] or "Cuộc trò chuyện mới"
        conversation = await storage.create_conversation(
            title=title,
            provider=req.provider,
            model=req.model,
            api_url=str(req.api_url),
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
    return {
        "conversation_id": conversation_id,
        "session_id": mask_result["session_id"],
        "masked_text": mask_result["masked_text"],
        "public_llm_response": public_response,
        "final_text": final_text,
        "entities": mask_result["entities"],
        "entity_count": mask_result["entity_count"],
    }
