"""PostgreSQL persistence for conversations and messages.

API keys are intentionally never accepted or stored by this module.
"""

import asyncio
import json
import uuid
from typing import Any

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None
_connect_lock = asyncio.Lock()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    model VARCHAR(200) NOT NULL,
    api_url TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    entity_count INTEGER NOT NULL DEFAULT 0 CHECK (entity_count >= 0),
    entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    attachment_name TEXT,
    attachment_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS entities JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS attachment_name TEXT;
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS attachment_text TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages(conversation_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations(updated_at DESC);
"""


async def connect_database() -> None:
    """Create the shared connection pool and ensure the schema exists."""
    global _pool
    if _pool is not None:
        return

    async with _connect_lock:
        if _pool is not None:
            return
        pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=settings.DATABASE_MIN_POOL_SIZE,
            max_size=settings.DATABASE_MAX_POOL_SIZE,
            command_timeout=30,
        )
        try:
            async with pool.acquire() as connection:
                await connection.execute(SCHEMA_SQL)
        except Exception:
            await pool.close()
            raise
        _pool = pool


async def close_database() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("PostgreSQL chưa được kết nối")
    return _pool


def _record_to_dict(record: asyncpg.Record) -> dict[str, Any]:
    result = dict(record)
    if "id" in result:
        result["id"] = str(result["id"])
    if "conversation_id" in result:
        result["conversation_id"] = str(result["conversation_id"])
    if isinstance(result.get("entities"), str):
        result["entities"] = json.loads(result["entities"])
    return result


async def create_conversation(
    title: str,
    provider: str,
    model: str,
    api_url: str,
) -> dict[str, Any]:
    conversation_id = uuid.uuid4()
    row = await _get_pool().fetchrow(
        """
        INSERT INTO conversations (id, title, provider, model, api_url)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        conversation_id,
        title[:200],
        provider,
        model,
        api_url,
    )
    return _record_to_dict(row)


async def list_conversations() -> list[dict[str, Any]]:
    rows = await _get_pool().fetch(
        """
        SELECT
            c.id,
            c.title,
            c.provider,
            c.model,
            c.api_url,
            c.created_at,
            c.updated_at,
            COUNT(m.id)::INTEGER AS message_count,
            (
                SELECT recent.content
                FROM messages AS recent
                WHERE recent.conversation_id = c.id
                ORDER BY recent.created_at DESC, recent.id DESC
                LIMIT 1
            ) AS last_message
        FROM conversations AS c
        LEFT JOIN messages AS m ON m.conversation_id = c.id
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        """
    )
    return [_record_to_dict(row) for row in rows]


async def get_conversation(
    conversation_id: str,
    *,
    include_attachment_text: bool = False,
) -> dict[str, Any] | None:
    try:
        parsed_id = uuid.UUID(conversation_id)
    except ValueError:
        return None

    pool = _get_pool()
    conversation = await pool.fetchrow(
        "SELECT * FROM conversations WHERE id = $1",
        parsed_id,
    )
    if conversation is None:
        return None

    attachment_text_column = ", attachment_text" if include_attachment_text else ""
    messages = await pool.fetch(
        f"""
        SELECT id, conversation_id, role, content, entity_count, entities,
               attachment_name, created_at{attachment_text_column}
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at, id
        """,
        parsed_id,
    )
    result = _record_to_dict(conversation)
    result["messages"] = [_record_to_dict(row) for row in messages]
    return result


async def update_conversation_config(
    conversation_id: str,
    provider: str,
    model: str,
    api_url: str,
) -> None:
    await _get_pool().execute(
        """
        UPDATE conversations
        SET provider = $2, model = $3, api_url = $4, updated_at = NOW()
        WHERE id = $1
        """,
        uuid.UUID(conversation_id),
        provider,
        model,
        api_url,
    )


async def add_exchange(
    conversation_id: str,
    user_text: str,
    assistant_text: str,
    entity_count: int,
    entities: list[dict[str, str]],
    attachment_name: str | None = None,
    attachment_text: str | None = None,
) -> None:
    parsed_id = uuid.UUID(conversation_id)
    pool = _get_pool()
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.executemany(
                """
                INSERT INTO messages (
                    conversation_id, role, content, entity_count, entities,
                    attachment_name, attachment_text
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                """,
                [
                    (
                        parsed_id,
                        "user",
                        user_text,
                        entity_count,
                        json.dumps(entities),
                        attachment_name,
                        attachment_text,
                    ),
                    (parsed_id, "assistant", assistant_text, 0, json.dumps(entities), None, None),
                ],
            )
            await connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE id = $1",
                parsed_id,
            )


async def delete_conversation(conversation_id: str) -> bool:
    try:
        parsed_id = uuid.UUID(conversation_id)
    except ValueError:
        return False
    status = await _get_pool().execute(
        "DELETE FROM conversations WHERE id = $1",
        parsed_id,
    )
    return status == "DELETE 1"
