import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Local LLM (vLLM chạy trên VM 111) — dùng để detect entity nhạy cảm
    LOCAL_LLM_URL: str = os.getenv("LOCAL_LLM_URL", "http://192.168.210.212:8000/v1/chat/completions")
    LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "Qwen/Qwen2.5-3B-Instruct")

    # Public LLM — để trống, cắm vào sau khi bạn chọn (OpenAI / Anthropic / khác)
    # Ví dụ OpenAI: PUBLIC_LLM_URL=https://api.openai.com/v1/chat/completions
    # Ví dụ Anthropic: PUBLIC_LLM_URL=https://api.anthropic.com/v1/messages
    PUBLIC_LLM_URL: str = os.getenv("PUBLIC_LLM_URL", "")
    PUBLIC_LLM_API_KEY: str = os.getenv("PUBLIC_LLM_API_KEY", "")
    PUBLIC_LLM_MODEL: str = os.getenv("PUBLIC_LLM_MODEL", "")

    # Request timeout (giây) — văn bản dài cần timeout cao hơn mặc định
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "180"))

    # Bật/tắt lưu mapping trong bộ nhớ tạm (in-memory) theo session_id
    SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "3600"))

    # PostgreSQL lưu lịch sử hội thoại. Không lưu API key trong database.
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://masking:masking_dev_password@127.0.0.1:5434/masking_app",
    )
    DATABASE_MIN_POOL_SIZE: int = int(os.getenv("DATABASE_MIN_POOL_SIZE", "1"))
    DATABASE_MAX_POOL_SIZE: int = int(os.getenv("DATABASE_MAX_POOL_SIZE", "5"))

    # Phiên đăng nhập. Bật Secure khi ứng dụng được phục vụ qua HTTPS.
    AUTH_SESSION_DAYS: int = int(os.getenv("AUTH_SESSION_DAYS", "7"))
    AUTH_COOKIE_SECURE: bool = os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {
        "1", "true", "yes", "on"
    }
    AUTH_COOKIE_NAME: str = os.getenv("AUTH_COOKIE_NAME", "masking_session")

    # Giới hạn upload để bảo vệ RAM và context window của local/public LLM.
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    MAX_EXTRACTED_CHARS: int = int(os.getenv("MAX_EXTRACTED_CHARS", "200000"))
    LOCAL_LLM_CHUNK_CHARS: int = int(os.getenv("LOCAL_LLM_CHUNK_CHARS", "12000"))


settings = Settings()
