import json
import re
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import settings

# ---------------------------------------------------------------------------
# In-memory session store cho mapping (placeholder -> giá trị thật)
# Production: nên đổi sang Redis nếu chạy nhiều worker/process
# ---------------------------------------------------------------------------
_SESSIONS: dict[str, dict[str, Any]] = {}


def _cleanup_expired_sessions() -> None:
    now = time.time()
    expired = [
        sid
        for sid, data in _SESSIONS.items()
        if now - data["created_at"] > settings.SESSION_TTL_SECONDS
    ]
    for sid in expired:
        del _SESSIONS[sid]


SYSTEM_PROMPT = """Bạn là công cụ phát hiện dữ liệu nhạy cảm trong văn bản tiếng Việt. Nhiệm vụ: đọc văn bản, tìm TẤT CẢ thông tin có thể coi là nhạy cảm hoặc định danh cá nhân/tổ chức, bao gồm nhưng không giới hạn: tên người, tên công ty/tổ chức, số điện thoại, email, địa chỉ, số tiền, số tài khoản ngân hàng, số CMND/CCCD/hộ chiếu, mã số thuế, ngày sinh, và bất kỳ thông tin định danh nào khác.

Với mỗi thực thể tìm được, tự đặt một nhãn loại (type) ngắn gọn, viết hoa, không dấu, dùng gạch dưới (ví dụ: TEN_NGUOI, TEN_CONG_TY, SO_DIEN_THOAI, SO_TIEN, DIA_CHI, EMAIL, SO_TAI_KHOAN, CMND_CCCD, MA_SO_THUE, NGAY_SINH...).

QUAN TRỌNG: mỗi thực thể DUY NHẤT (unique) chỉ liệt kê MỘT LẦN trong kết quả, dù nó xuất hiện nhiều lần trong văn bản gốc. Không lặp lại cùng một entity nhiều lần trong JSON.

Trường "text" PHẢI sao chép NGUYÊN VĂN từ input, giữ đúng chữ hoa/thường, dấu tiếng Việt, khoảng trắng và dấu câu. Không được chuyển tên thành chữ in hoa không dấu hoặc thay khoảng trắng bằng dấu gạch dưới.

CHỈ trả về JSON đúng định dạng: {"entities": [{"text": "văn bản gốc chính xác", "type": "loại"}]}. Không viết giải thích, không phân tích, không liệt kê bằng văn xuôi."""


class LocalLLMError(Exception):
    pass


async def detect_entities(text: str) -> list[dict[str, str]]:
    """Gọi local vLLM để tìm các entity nhạy cảm trong văn bản."""
    payload = {
        "model": settings.LOCAL_LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": 2000,
        "repetition_penalty": 1.15,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        try:
            resp = await client.post(settings.LOCAL_LLM_URL, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LocalLLMError(f"Không gọi được local LLM: {e}") from e

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    return _parse_entities_json(content)


def split_detection_text(text: str, max_chars: int) -> list[str]:
    """Split long input at line/word boundaries for local LLM entity detection."""
    if max_chars < 1000:
        max_chars = 1000
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + max_chars, text_length)
        if end < text_length:
            search_from = start + max_chars // 2
            newline_boundary = text.rfind("\n", search_from, end)
            space_boundary = text.rfind(" ", search_from, end)
            boundary = max(newline_boundary, space_boundary)
            if boundary > start:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end

    return chunks


DetectionProgress = Callable[[int, int], Awaitable[None]]


async def detect_entities_chunked(
    text: str,
    progress_callback: DetectionProgress | None = None,
) -> list[dict[str, str]]:
    """Detect entities sequentially so long files do not overflow local LLM context."""
    chunks = split_detection_text(text, settings.LOCAL_LLM_CHUNK_CHARS)
    detected: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks, start=1):
        try:
            detected.extend(await detect_entities(chunk))
        except LocalLLMError as exc:
            if len(chunks) == 1:
                raise
            raise LocalLLMError(
                f"Local LLM không xử lý được phần {index}/{len(chunks)} của tài liệu: {exc}"
            ) from exc
        if progress_callback:
            await progress_callback(index, len(chunks))
    return detected


def _parse_entities_json(content: str) -> list[dict[str, str]]:
    """Parse JSON entities từ response, chịu lỗi nếu model trả JSON có dính text thừa
    hoặc bị cắt cụt giữa chừng."""
    try:
        parsed = json.loads(content)
        return parsed.get("entities", [])
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed.get("entities", [])
        except json.JSONDecodeError:
            pass

    # JSON bị cắt cụt — cố cắt tới object hoàn chỉnh cuối cùng rồi đóng lại
    last_complete = content.rfind("},")
    if last_complete > -1:
        truncated = content[: last_complete + 1] + "]}"
        try:
            parsed = json.loads(truncated)
            return parsed.get("entities", [])
        except json.JSONDecodeError:
            pass

    return []


def _normalized_text_with_positions(text: str) -> tuple[str, list[int]]:
    """Chuẩn hóa để so khớp gần đúng và giữ ánh xạ về vị trí chuỗi gốc."""
    normalized_chars: list[str] = []
    positions: list[int] = []

    for index, char in enumerate(text):
        decomposed = unicodedata.normalize("NFD", char)
        base_chars = [part for part in decomposed if unicodedata.category(part) != "Mn"]
        for base in base_chars:
            base = {"đ": "d", "Đ": "d"}.get(base, base).lower()
            if base.isalnum():
                normalized_chars.append(base)
                positions.append(index)
            elif normalized_chars and normalized_chars[-1] != " ":
                normalized_chars.append(" ")
                positions.append(index)

    while normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        positions.pop()

    return "".join(normalized_chars), positions


def _find_original_entity_text(original_text: str, detected_text: str) -> str | None:
    """Tìm lại chuỗi nguyên văn khi LLM làm mất dấu hoặc đổi khoảng trắng thành `_`."""
    exact_match = re.search(re.escape(detected_text), original_text, flags=re.IGNORECASE)
    if exact_match:
        return original_text[exact_match.start() : exact_match.end()]

    normalized_original, positions = _normalized_text_with_positions(original_text)
    normalized_entity, _ = _normalized_text_with_positions(detected_text)
    if not normalized_entity or not positions:
        return None

    match = re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_entity)}(?![a-z0-9])",
        normalized_original,
    )
    if not match:
        return None

    start = positions[match.start()]
    end = positions[match.end() - 1] + 1
    return original_text[start:end]


def reconcile_entities(
    original_text: str, entities: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Đối chiếu output LLM với input và loại entity không tồn tại hoặc trùng lặp."""
    reconciled: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for entity in entities:
        detected_text = str(entity.get("text", "")).strip()
        entity_type = str(entity.get("type", "UNKNOWN")).strip() or "UNKNOWN"
        if not detected_text:
            continue

        original_value = _find_original_entity_text(original_text, detected_text)
        if original_value is None:
            continue

        key = (entity_type, original_value.casefold())
        if key in seen:
            continue
        seen.add(key)
        reconciled.append({"text": original_value, "type": entity_type})

    return reconciled


def mask_text(original_text: str, entities: list[dict[str, str]]) -> tuple[str, dict[str, str]]:
    """Thay thế từng entity bằng placeholder, trả về (masked_text, mapping)."""
    masked_text = original_text
    mapping: dict[str, str] = {}
    counter: dict[str, int] = {}

    for ent in entities:
        text = ent.get("text", "")
        etype = ent.get("type", "UNKNOWN")
        if not text:
            continue

        counter[etype] = counter.get(etype, 0) + 1
        placeholder = f"[{etype}_{counter[etype]}]"
        mapping[placeholder] = text

        escaped = re.escape(text)
        masked_text = re.sub(escaped, placeholder, masked_text, flags=re.IGNORECASE)

    return masked_text, mapping


def unmask_text(text: str, mapping: dict[str, str]) -> str:
    """Thay ngược placeholder về giá trị thật."""
    result = text
    for placeholder, real_value in mapping.items():
        result = result.replace(placeholder, real_value)
    return result


async def process_mask(
    text: str,
    known_entities: list[dict[str, str]] | None = None,
    detection_text: str | None = None,
    progress_callback: DetectionProgress | None = None,
) -> dict[str, Any]:
    """Pipeline đầy đủ: detect -> mask -> lưu session -> trả về masked_text + session_id."""
    _cleanup_expired_sessions()

    detected_entities = await detect_entities_chunked(
        detection_text or text,
        progress_callback=progress_callback,
    )
    entities = reconcile_entities(text, [*(known_entities or []), *detected_entities])
    masked_text, mapping = mask_text(text, entities)

    session_id = str(uuid.uuid4())
    _SESSIONS[session_id] = {
        "mapping": mapping,
        "original_text": text,
        "created_at": time.time(),
    }

    return {
        "session_id": session_id,
        "masked_text": masked_text,
        "entities": entities,
        "entity_count": len(entities),
    }


def process_unmask(session_id: str, text: str) -> dict[str, Any]:
    """Thay placeholder trong `text` (thường là response từ Public LLM) về giá trị thật,
    dùng mapping đã lưu theo session_id."""
    session = _SESSIONS.get(session_id)
    if session is None:
        raise KeyError("session_id không tồn tại hoặc đã hết hạn")

    final_text = unmask_text(text, session["mapping"])
    return {"final_text": final_text}


def get_session_mapping(session_id: str) -> dict[str, str] | None:
    session = _SESSIONS.get(session_id)
    return session["mapping"] if session else None
