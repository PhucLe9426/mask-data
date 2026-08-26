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


settings = Settings()
