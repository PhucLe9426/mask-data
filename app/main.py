from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl

from app import masking
from app.config import settings

app = FastAPI(
    title="Data Masking API",
    description="Mask dữ liệu nhạy cảm bằng local LLM trước khi gửi ra Public LLM",
    version="1.0.0",
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


class ChatResponse(BaseModel):
    session_id: str
    masked_text: str
    public_llm_response: str
    final_text: str
    entities: list[dict]
    entity_count: int


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
    return {"status": "ok"}


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

    # Bước 1: mask
    conversation_parts = []
    for item in req.history[-10:]:
        label = "Người dùng" if item.role == "user" else "Trợ lý"
        conversation_parts.append(f"{label}: {item.content}")
    conversation_parts.append(f"Người dùng: {req.text}")
    conversation_parts.append("Trợ lý: Hãy trả lời tin nhắn cuối cùng của người dùng.")
    conversation_text = "\n\n".join(conversation_parts)

    try:
        mask_result = await masking.process_mask(conversation_text)
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
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text không được để trống")
    if not req.api_key.strip() or not req.model.strip():
        raise HTTPException(status_code=400, detail="API key và model không được để trống")
    if req.provider not in {"openai_compatible", "anthropic", "gemini"}:
        raise HTTPException(status_code=400, detail="Nhà cung cấp không được hỗ trợ")

    try:
        mask_result = await masking.process_mask(req.text)
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
    return {
        "session_id": mask_result["session_id"],
        "masked_text": mask_result["masked_text"],
        "public_llm_response": public_response,
        "final_text": unmask_result["final_text"],
        "entities": mask_result["entities"],
        "entity_count": mask_result["entity_count"],
    }
