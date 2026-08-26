# Data Masking

Ứng dụng FastAPI giúp che dữ liệu nhạy cảm bằng local LLM trước khi gửi nội
dung tới Public LLM. Câu trả lời từ Public LLM được khôi phục placeholder về
giá trị ban đầu trước khi hiển thị cho người dùng.

Ứng dụng có giao diện web, chế độ sáng/tối và hỗ trợ chat trực tiếp với API
OpenAI-compatible, Anthropic hoặc Google Gemini.

## Luồng xử lý

```text
Văn bản gốc
    ↓
Local LLM phát hiện entity
    ↓
Thay dữ liệu nhạy cảm bằng placeholder
    ↓
Gửi nội dung đã che tới Public LLM
    ↓
Khôi phục placeholder trong câu trả lời
    ↓
Hiển thị kết quả cho người dùng
```

Ví dụ:

```text
Lê Trọng Phúc, số điện thoại 0913885457
↓
[TEN_NGUOI_1], số điện thoại [SO_DIEN_THOAI_1]
```

## Tính năng

- Phát hiện tên người, công ty, số điện thoại, email, địa chỉ, số tiền, số tài
  khoản, CCCD và các dữ liệu định danh khác.
- Đối chiếu entity với văn bản gốc, kể cả khi local LLM làm mất dấu tiếng Việt
  hoặc thay khoảng trắng bằng dấu gạch dưới.
- Che và khôi phục dữ liệu theo `session_id`.
- Chat trực tiếp với OpenAI-compatible, Anthropic và Google Gemini.
- Che toàn bộ lịch sử trò chuyện trước khi gửi tới Public LLM.
- Lưu, mở lại và xóa cuộc trò chuyện bằng PostgreSQL; API key không được lưu.
- Tạo Project chứa nhiều cuộc trò chuyện, mô tả và bộ nhớ chung.
- Đính kèm tài liệu dùng chung cho Project; backend chỉ lưu văn bản đã trích xuất và lấy các đoạn liên quan làm ngữ cảnh.
- Lưu và hiển thị nguồn tài liệu cùng câu trả lời; có thể mở tên file để xem đoạn trích đã đưa vào ngữ cảnh.
- Đổi tên hội thoại, chuyển hội thoại vào Project khác hoặc đưa về danh sách hội thoại riêng.
- Tìm kiếm hội thoại theo tên trong Project đang chọn hoặc danh sách hội thoại riêng.
- Tự lấy các tin nhắn liên quan và gần đây từ hội thoại khác trong cùng Project,
  sau đó mask toàn bộ context trước khi gọi Public LLM.
- Tự thử lại tối đa 3 lần khi Gemini gặp lỗi tạm thời `429`, `500`, `502`,
  `503` hoặc `504`.
- Giao diện responsive, hỗ trợ sáng/tối và ghi nhớ theme trên trình duyệt.
- Hiển thị tiến trình theo từng bước khi gửi tin nhắn hoặc xử lý file; có thể hủy yêu cầu đang chạy mà không lưu lượt chat dở dang.
- Swagger UI để thử API trực tiếp.

## Yêu cầu

- Python 3.10 trở lên.
- Docker Desktop để chạy PostgreSQL cục bộ, hoặc một PostgreSQL bên ngoài.
- Local LLM có API tương thích OpenAI Chat Completions.
- Local LLM mặc định của dự án:
  `http://192.168.210.212:8000/v1/chat/completions`.
- API key của Public LLM nếu sử dụng chức năng chat.

## Cài đặt

### Windows PowerShell

```powershell
cd C:\Users\admin\Downloads\masking-app\masking-app
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Nếu máy không có lệnh `python` nhưng đã cài `uv`:

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
```

### Linux hoặc macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Mở `.env` và kiểm tra `LOCAL_LLM_URL`, `LOCAL_LLM_MODEL` cùng timeout phù hợp
với môi trường của bạn.

## Khởi động PostgreSQL

Dự án cung cấp `docker-compose.yml` chạy PostgreSQL 18 trên port `5434` để
không xung đột với các PostgreSQL khác thường dùng `5432` hoặc `5433`.

```powershell
docker compose up -d postgres
docker compose ps
```

Khi cột trạng thái hiển thị `healthy`, có thể chạy FastAPI. Dữ liệu được giữ
trong Docker volume `masking-app_masking_postgres_data`, nên vẫn còn sau khi
restart container hoặc máy tính.

Để dừng database mà vẫn giữ dữ liệu:

```powershell
docker compose stop postgres
```

Không chạy `docker compose down -v` nếu bạn muốn giữ lịch sử; tùy chọn `-v` sẽ
xóa volume chứa toàn bộ cuộc trò chuyện.

## Chạy toàn bộ bằng Docker

Đây là cách khuyến nghị vì cả FastAPI và PostgreSQL được quản lý chung bằng
Docker Compose:

```powershell
cd C:\Users\admin\Downloads\masking-app\masking-app
docker compose up -d --build
docker compose ps
```

Hai service phải hiển thị `healthy`:

- `masking-api`: FastAPI tại <http://127.0.0.1:8080/>
- `masking-postgres`: PostgreSQL, publish ra host ở port `5434`

Xem log FastAPI:

```powershell
docker compose logs -f app
```

Sau khi sửa code, build và chạy lại app:

```powershell
docker compose up -d --build app
```

Dừng stack nhưng vẫn giữ dữ liệu:

```powershell
docker compose stop
```

FastAPI trong Docker kết nối database qua hostname nội bộ `postgres:5432`.
Compose tự override `DATABASE_URL`; giá trị port `5434` trong `.env.example`
chỉ dùng khi chạy FastAPI trực tiếp trên Windows.

## Chạy FastAPI trực tiếp trên máy

Chỉ dùng cách này khi muốn phát triển/debug ngoài Docker. PostgreSQL vẫn cần
được khởi động trước bằng `docker compose up -d postgres`.

### Windows PowerShell

```powershell
cd C:\Users\admin\Downloads\masking-app\masking-app
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### Linux hoặc macOS

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Sau khi server khởi động:

- Giao diện web: <http://127.0.0.1:8080/>
- Swagger UI: <http://127.0.0.1:8080/docs>
- Kiểm tra trạng thái: <http://127.0.0.1:8080/health>

Giữ terminal mở trong lúc sử dụng. Nhấn `Ctrl+C` để dừng server.

## Chat với Public LLM

Mở giao diện, sau đó nhập API URL, model và API key. Key chỉ tồn tại trong ô
nhập trên trang và được gửi tới backend cục bộ trong từng request; ứng dụng
không ghi key vào file, PostgreSQL hoặc `localStorage`.

Sidebar bên trái hiển thị các cuộc trò chuyện đã lưu và có thể đóng/mở bằng nút
**☰**. Trên điện thoại, sidebar trượt từ cạnh trái và tự đóng sau khi chọn hội
thoại. Nút **+ Mới** bắt đầu hội thoại mới; bấm vào một mục để tải lại lịch sử
hoặc nút **×** để xóa. PostgreSQL lưu nội dung, provider, model, API URL và entity
mapping để placeholder được khôi phục ổn định giữa nhiều lượt chat.

### Gửi file trong chat

Nhấn **Đính kèm file** bên dưới ô nhập tin nhắn rồi chọn một trong các định dạng:
`TXT`, `MD`, `CSV`, `JSON`, `XML`, `YAML`, `LOG`, `PDF` hoặc `DOCX`. Có thể nhập câu
hỏi kèm theo hoặc chỉ gửi file để yêu cầu AI phân tích nội dung.

Luồng xử lý file:

```text
File → FastAPI trích xuất text trong RAM → Local LLM phát hiện và mask dữ liệu
     → Chỉ text đã mask được gửi tới Public LLM → Khôi phục câu trả lời
```

File gốc không được lưu. PostgreSQL chỉ lưu tên file và nội dung văn bản đã trích
xuất để cuộc trò chuyện tiếp tục có ngữ cảnh; API key không được lưu. Mặc định file
tối đa 10 MB và nội dung trích xuất tối đa 200.000 ký tự. Nội dung dài được chia
thành các đoạn 12.000 ký tự để local LLM phát hiện entity tuần tự trước khi gộp và
mask toàn bộ tài liệu. Các ô gộp trong bảng Word chỉ được đọc một lần để tránh lặp
nội dung. PDF scan không có lớp text chưa được hỗ trợ OCR.

### Project và bộ nhớ liên hội thoại

Nhấn nút **+** ở mục **Projects** trong sidebar để tạo Project. Mỗi Project có:

- Tên và mô tả mục tiêu.
- Ô **Thông tin cần ghi nhớ** dành cho quy ước, quyết định và yêu cầu dùng lâu dài.
- Nhiều cuộc hội thoại riêng nhưng dùng chung bối cảnh Project.

Khi gửi tin nhắn trong Project, backend kết hợp bộ nhớ cố định với tối đa 12 tin
nhắn liên quan hoặc gần đây từ các hội thoại khác. Context dùng chung được giới
hạn 30.000 ký tự, được local LLM phát hiện entity và mask cùng hội thoại hiện tại
rồi mới gửi tới Public LLM. Chọn **Tất cả hội thoại** để chat ngoài Project và
không dùng bộ nhớ chung.

| Loại API | API URL mẫu | Model |
| --- | --- | --- |
| OpenAI-compatible | `https://api.openai.com/v1/chat/completions` | Nhập model được nhà cung cấp hỗ trợ |
| Anthropic | `https://api.anthropic.com/v1/messages` | Nhập model được tài khoản hỗ trợ |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/models` | `gemini-3.6-flash` |

Với các dịch vụ tương thích OpenAI như OpenRouter, Groq hoặc Together, thay API
URL và model bằng thông tin do dịch vụ đó cung cấp.

## API endpoints

### `GET /health`

Kiểm tra trạng thái FastAPI.

```json
{"status": "ok"}
```

### `POST /mask`

Phát hiện và che entity, sau đó trả về `session_id` dùng cho `/unmask`.

```bash
curl -X POST http://127.0.0.1:8080/mask \
  -H "Content-Type: application/json" \
  -d '{"text":"Lê Trọng Phúc, số điện thoại 0913885457"}'
```

Response mẫu:

```json
{
  "session_id": "a1b2c3d4-...",
  "masked_text": "[TEN_NGUOI_1], số điện thoại [SO_DIEN_THOAI_1]",
  "entities": [
    {"text": "Lê Trọng Phúc", "type": "TEN_NGUOI"},
    {"text": "0913885457", "type": "SO_DIEN_THOAI"}
  ],
  "entity_count": 2
}
```

### `POST /unmask`

Khôi phục placeholder bằng mapping của `session_id`.

```bash
curl -X POST http://127.0.0.1:8080/unmask \
  -H "Content-Type: application/json" \
  -d '{"session_id":"a1b2c3d4-...","text":"Đã ghi nhận [TEN_NGUOI_1]."}'
```

### `POST /chat`

Thực hiện pipeline mask → Public LLM → unmask. Các giá trị `provider` được hỗ
trợ là `openai_compatible`, `anthropic` và `gemini`.

```json
{
  "text": "Soạn email cho Công ty ABC",
  "api_url": "https://generativelanguage.googleapis.com/v1beta/models",
  "api_key": "API_KEY_CUA_BAN",
  "model": "gemini-3.6-flash",
  "provider": "gemini",
  "conversation_id": null
}
```

Response trả thêm `conversation_id`. Gửi lại ID này ở lượt tiếp theo để backend
lấy lịch sử từ PostgreSQL, mask toàn bộ lịch sử rồi lưu cặp tin nhắn mới.

### `GET /conversations`

Liệt kê các cuộc trò chuyện theo thời gian cập nhật gần nhất.
Có thể truyền `project_id` để chỉ lấy hội thoại thuộc một Project.

### `GET /conversations/{conversation_id}`

Lấy cấu hình và toàn bộ tin nhắn của một cuộc trò chuyện.

### `DELETE /conversations/{conversation_id}`

Xóa cuộc trò chuyện cùng các tin nhắn liên quan.

### Project endpoints

- `GET /projects`: liệt kê Project và số lượng hội thoại.
- `POST /projects`: tạo Project.
- `GET /projects/{project_id}`: lấy mô tả và bộ nhớ Project.
- `PUT /projects/{project_id}`: cập nhật Project.
- `DELETE /projects/{project_id}`: xóa Project; hội thoại vẫn được giữ và chuyển
  thành hội thoại không thuộc Project.

### `POST /process`

Pipeline mask → Public LLM → unmask sử dụng `PUBLIC_LLM_URL`,
`PUBLIC_LLM_API_KEY` và `PUBLIC_LLM_MODEL` trong `.env`. Nhánh này sử dụng định
dạng OpenAI-compatible. Nếu chưa cấu hình, endpoint trả về `501`.

## Cấu hình `.env`

```dotenv
LOCAL_LLM_URL=http://192.168.210.212:8000/v1/chat/completions
LOCAL_LLM_MODEL=Qwen/Qwen2.5-3B-Instruct

PUBLIC_LLM_URL=
PUBLIC_LLM_API_KEY=
PUBLIC_LLM_MODEL=

REQUEST_TIMEOUT=180
SESSION_TTL_SECONDS=3600

DATABASE_URL=postgresql://masking:masking_dev_password@127.0.0.1:5434/masking_app
DATABASE_MIN_POOL_SIZE=1
DATABASE_MAX_POOL_SIZE=5

MAX_UPLOAD_BYTES=10485760
MAX_EXTRACTED_CHARS=200000
LOCAL_LLM_CHUNK_CHARS=12000
```

Không commit file `.env`. Repository đã có `.gitignore` để loại trừ secrets,
môi trường Python, cache và log runtime.

## Xử lý lỗi thường gặp

### Không kết nối được API

- Kiểm tra Uvicorn còn chạy hay không.
- Mở <http://127.0.0.1:8080/health> và xác nhận `status` cùng `database` đều
  là `ok`.
- Tải lại trang bằng `Ctrl+Shift+R`.

### Không gọi được local LLM

- Kiểm tra máy `192.168.210.212` có thể truy cập từ máy chạy ứng dụng.
- Kiểm tra port `8000`, URL và tên model trong `.env`.

### Gemini trả lỗi `503 high demand`

Ứng dụng sẽ tự thử lại tối đa 3 lần. Nếu vẫn lỗi, chờ vài phút rồi gửi lại hoặc
đổi sang model nhẹ hơn như `gemini-3.5-flash-lite`.

### Public LLM trả lỗi `401` hoặc `403`

Kiểm tra API key, model, quyền truy cập và API URL của nhà cung cấp.

## Lưu ý khi triển khai production

- Session mapping hiện được lưu trong RAM và mất khi restart. Nhiều worker cũng
  không dùng chung mapping; hội thoại vẫn được giữ trong PostgreSQL, nhưng nên
  chuyển mapping tạm sang Redis nếu triển khai nhiều worker.
- CORS đang mở `*`; cần giới hạn origin được phép truy cập.
- Endpoint `/chat` nhận API URL từ người dùng. Nếu public ứng dụng ra Internet,
  cần whitelist domain nhà cung cấp để tránh SSRF.
- Chỉ triển khai qua HTTPS để bảo vệ API key trên đường truyền.
- Không ghi request body, API key hoặc dữ liệu đã khôi phục vào log.
- Giới hạn kích thước văn bản, rate limit và xác thực người dùng trước khi đưa
  ứng dụng ra ngoài mạng nội bộ.

## Cấu trúc dự án

```text
app/
├── __init__.py
├── config.py       # Đọc cấu hình môi trường
├── file_reader.py  # Trích xuất text cục bộ từ file upload
├── main.py         # FastAPI endpoints
├── masking.py      # Detect, mask, unmask và session mapping
├── public_llm.py   # OpenAI-compatible, Anthropic và Gemini clients
├── storage.py      # PostgreSQL pool, schema và CRUD hội thoại
└── web.html        # Giao diện web
```
