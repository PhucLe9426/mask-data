# Data Masking API

Backend FastAPI cho pipeline mask dữ liệu nhạy cảm bằng local LLM trước khi
gửi ra Public LLM, sau đó de-mask response về giá trị thật.

## Cài đặt

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Sửa `.env`, đặt đúng `LOCAL_LLM_URL` (mặc định đã trỏ tới VM 111:
`http://192.168.210.212:8000/v1/chat/completions`).

## Chạy

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Server chạy tại `http://localhost:8080`. Xem docs tự động (Swagger UI) tại
`http://localhost:8080/docs`.

## Các endpoint

### `POST /mask`
Detect entity nhạy cảm + mask text. Trả về `session_id` (dùng để unmask sau)
và `masked_text` (gửi đi Public LLM).

```bash
curl -X POST http://localhost:8080/mask \
  -H "Content-Type: application/json" \
  -d '{"text": "Công ty ABC Trading vừa ký hợp đồng trị giá 12,5 tỷ đồng, SĐT 0901234567."}'
```

Response:
```json
{
  "session_id": "a1b2c3d4-...",
  "masked_text": "[TEN_CONG_TY_1] vừa ký hợp đồng trị giá [SO_TIEN_1], SĐT [SO_DIEN_THOAI_1].",
  "entities": [...],
  "entity_count": 3
}
```

### `POST /unmask`
Thay placeholder trong response của Public LLM về giá trị thật, dùng
`session_id` đã lấy từ `/mask`.

```bash
curl -X POST http://localhost:8080/unmask \
  -H "Content-Type: application/json" \
  -d '{"session_id": "a1b2c3d4-...", "text": "Đã ghi nhận [TEN_CONG_TY_1]..."}'
```

### `POST /process`
Pipeline đầy đủ 1 lần gọi: mask → gọi Public LLM → unmask. **Cần cấu hình
Public LLM trước** (xem bên dưới), nếu chưa cấu hình sẽ trả lỗi 501.

## Cấu hình Public LLM

File `app/public_llm.py` để dạng khung sẵn với ví dụ code cho OpenAI và
Anthropic (đang comment). Khi bạn chọn nhà cung cấp:

1. Mở `app/public_llm.py`, bỏ comment đoạn tương ứng (OpenAI hoặc Anthropic)
2. Điền `PUBLIC_LLM_URL`, `PUBLIC_LLM_API_KEY`, `PUBLIC_LLM_MODEL` vào `.env`
3. Nếu dùng nhà cung cấp khác (Gemini, Azure OpenAI...), viết thêm nhánh
   tương tự trong `call_public_llm()`

Cho đến lúc đó, vẫn dùng riêng `/mask` và `/unmask` để tự ghép với bất kỳ
Public LLM nào từ phía client/frontend của bạn.

## Lưu ý production

- **Session store hiện tại là in-memory** (`_SESSIONS` dict trong
  `app/masking.py`) — mất khi restart server, và không share được giữa
  nhiều worker process. Nếu deploy với nhiều worker hoặc cần persistent,
  đổi sang Redis.
- **CORS đang mở `*`** trong `app/main.py` — giới hạn lại origin cụ thể
  trước khi đưa ra ngoài môi trường nội bộ.
- System prompt detect entity đã được tinh chỉnh qua nhiều lần test với
  văn bản hợp đồng tiếng Việt thực tế (xem `SYSTEM_PROMPT` trong
  `app/masking.py`) — có `repetition_penalty: 1.15` để tránh model bị lặp
  vô hạn với văn bản có tên/entity lặp lại nhiều lần.
