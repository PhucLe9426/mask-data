"""
Module gọi Public LLM (OpenAI / Anthropic / khác).

Bạn CHƯA chọn Public LLM cụ thể, nên file này để dạng khung sẵn.
Khi quyết định dùng nhà cung cấp nào, mở comment phần tương ứng bên dưới
và điền PUBLIC_LLM_URL / PUBLIC_LLM_API_KEY / PUBLIC_LLM_MODEL vào .env
"""

import asyncio

import httpx

from app.config import settings

DEFAULT_SYSTEM_PROMPT = """Bạn là trợ lý AI hỗ trợ công việc. Một số thông tin nhạy cảm trong đoạn hội thoại đã được thay thế bằng các placeholder có định dạng [LOAI_DU_LIEU_SO_THU_TU], ví dụ: [TEN_CONG_TY_1], [SO_TIEN_1], [SO_DIEN_THOAI_1].

QUY TẮC BẮT BUỘC:
1. Coi các placeholder này là một khối văn bản cố định, không thể chia tách hay diễn giải. Không đoán, không suy luận, không thay thế nội dung thật của chúng.
2. Khi trả lời, nếu cần nhắc đến thông tin đó, PHẢI giữ nguyên placeholder y hệt như trong input (không đổi định dạng, không thêm bớt ký tự, không dịch nghĩa).
3. Không được tự bịa ra giá trị thay cho placeholder.
4. Xử lý yêu cầu của người dùng bình thường như thể placeholder là tên thật, chỉ khác là bạn không biết giá trị cụ thể của nó.

Bây giờ hãy trả lời yêu cầu của người dùng bên dưới, tuân thủ đúng các quy tắc trên."""


async def call_public_llm(
    masked_text: str,
    system_prompt: str | None = None,
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    provider: str = "openai_compatible",
) -> str:
    """Gọi Public LLM với text đã mask. Trả về response text (còn nguyên placeholder,
    sẽ được unmask ở bước sau)."""

    prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    resolved_url = api_url or settings.PUBLIC_LLM_URL
    resolved_key = api_key or settings.PUBLIC_LLM_API_KEY
    resolved_model = model or settings.PUBLIC_LLM_MODEL

    if not resolved_url or not resolved_key or not resolved_model:
        raise ValueError("Thiếu API URL, API key hoặc tên model của Public LLM")

    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        if provider == "anthropic":
            payload = {
                "model": resolved_model,
                "max_tokens": 2048,
                "system": prompt,
                "messages": [{"role": "user", "content": masked_text}],
            }
            headers = {
                "x-api-key": resolved_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            response = await client.post(resolved_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )

        if provider == "gemini":
            gemini_url = resolved_url
            if not gemini_url.rstrip("/").endswith(":generateContent"):
                gemini_url = f"{gemini_url.rstrip('/')}/{resolved_model}:generateContent"
            payload = {
                "systemInstruction": {"parts": [{"text": prompt}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": masked_text}],
                    }
                ],
            }
            headers = {
                "x-goog-api-key": resolved_key,
                "Content-Type": "application/json",
            }
            retryable_statuses = {429, 500, 502, 503, 504}
            response = None
            for attempt in range(3):
                response = await client.post(gemini_url, json=payload, headers=headers)
                if response.status_code not in retryable_statuses or attempt == 2:
                    response.raise_for_status()
                    break

                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after is not None else 2**attempt
                except ValueError:
                    delay = 2**attempt
                await asyncio.sleep(min(max(delay, 0), 10))

            assert response is not None
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("Gemini không trả về candidate nào")
            return "".join(
                part.get("text", "")
                for part in candidates[0].get("content", {}).get("parts", [])
                if part.get("text")
            )

        payload = {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": masked_text},
            ],
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {resolved_key}",
            "Content-Type": "application/json",
        }
        response = await client.post(resolved_url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
