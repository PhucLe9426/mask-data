"""PostgreSQL persistence for conversations and messages.

API keys are intentionally never accepted or stored by this module.
"""

import asyncio
import json
import re
import uuid
from typing import Any

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None
_connect_lock = asyncio.Lock()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    memory TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_documents (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    title VARCHAR(200) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    model VARCHAR(200) NOT NULL,
    api_url TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS project_id UUID;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'conversations_project_id_fkey'
    ) THEN
        ALTER TABLE conversations
            ADD CONSTRAINT conversations_project_id_fkey
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    entity_count INTEGER NOT NULL DEFAULT 0 CHECK (entity_count >= 0),
    entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    attachment_name TEXT,
    attachment_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS entities JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS sources JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS attachment_name TEXT;
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS attachment_text TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages(conversation_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_project_updated
    ON conversations(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_documents_project_created
    ON project_documents(project_id, created_at DESC);
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
    if result.get("project_id") is not None:
        result["project_id"] = str(result["project_id"])
    if isinstance(result.get("entities"), str):
        result["entities"] = json.loads(result["entities"])
    if isinstance(result.get("sources"), str):
        result["sources"] = json.loads(result["sources"])
    return result


async def create_project(name: str, description: str = "", memory: str = "") -> dict[str, Any]:
    project_id = uuid.uuid4()
    row = await _get_pool().fetchrow(
        """
        INSERT INTO projects (id, name, description, memory)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        project_id,
        name.strip()[:160],
        description.strip()[:4000],
        memory.strip()[:20000],
    )
    return _record_to_dict(row)


async def list_projects() -> list[dict[str, Any]]:
    rows = await _get_pool().fetch(
        """
        SELECT
            p.*,
            (
                SELECT COUNT(*)::INTEGER
                FROM conversations AS c
                WHERE c.project_id = p.id
            ) AS conversation_count,
            (
                SELECT COUNT(*)::INTEGER
                FROM project_documents AS d
                WHERE d.project_id = p.id
            ) AS document_count
        FROM projects AS p
        ORDER BY p.updated_at DESC, p.name
        """
    )
    return [_record_to_dict(row) for row in rows]


async def get_project(project_id: str) -> dict[str, Any] | None:
    try:
        parsed_id = uuid.UUID(project_id)
    except ValueError:
        return None
    row = await _get_pool().fetchrow("SELECT * FROM projects WHERE id = $1", parsed_id)
    return _record_to_dict(row) if row else None


async def update_project(
    project_id: str,
    name: str,
    description: str = "",
    memory: str = "",
) -> dict[str, Any] | None:
    try:
        parsed_id = uuid.UUID(project_id)
    except ValueError:
        return None
    row = await _get_pool().fetchrow(
        """
        UPDATE projects
        SET name = $2, description = $3, memory = $4, updated_at = NOW()
        WHERE id = $1
        RETURNING *
        """,
        parsed_id,
        name.strip()[:160],
        description.strip()[:4000],
        memory.strip()[:20000],
    )
    return _record_to_dict(row) if row else None


async def delete_project(project_id: str) -> bool:
    try:
        parsed_id = uuid.UUID(project_id)
    except ValueError:
        return False
    status = await _get_pool().execute("DELETE FROM projects WHERE id = $1", parsed_id)
    return status == "DELETE 1"


async def create_project_document(
    project_id: str,
    *,
    name: str,
    content: str,
    size_bytes: int,
) -> dict[str, Any]:
    row = await _get_pool().fetchrow(
        """
        INSERT INTO project_documents (id, project_id, name, content, size_bytes)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, project_id, name, size_bytes, created_at
        """,
        uuid.uuid4(),
        uuid.UUID(project_id),
        name[:255],
        content,
        size_bytes,
    )
    await _get_pool().execute(
        "UPDATE projects SET updated_at = NOW() WHERE id = $1",
        uuid.UUID(project_id),
    )
    return _record_to_dict(row)


async def list_project_documents(project_id: str) -> list[dict[str, Any]]:
    rows = await _get_pool().fetch(
        """
        SELECT id, project_id, name, size_bytes, created_at
        FROM project_documents
        WHERE project_id = $1
        ORDER BY created_at DESC, name
        """,
        uuid.UUID(project_id),
    )
    return [_record_to_dict(row) for row in rows]


async def delete_project_document(project_id: str, document_id: str) -> bool:
    try:
        parsed_project_id = uuid.UUID(project_id)
        parsed_document_id = uuid.UUID(document_id)
    except ValueError:
        return False
    status = await _get_pool().execute(
        "DELETE FROM project_documents WHERE id = $1 AND project_id = $2",
        parsed_document_id,
        parsed_project_id,
    )
    if status == "DELETE 1":
        await _get_pool().execute(
            "UPDATE projects SET updated_at = NOW() WHERE id = $1",
            parsed_project_id,
        )
        return True
    return False


async def get_project_document_context(
    project_id: str,
    *,
    query: str,
    max_chars: int = 20000,
) -> list[dict[str, str]]:
    """Return relevant bounded text chunks from documents attached to a project."""
    rows = await _get_pool().fetch(
        """
        SELECT id, name, content, created_at
        FROM project_documents
        WHERE project_id = $1
        ORDER BY created_at DESC
        """,
        uuid.UUID(project_id),
    )
    terms = list(dict.fromkeys(re.findall(r"\w{3,}", query.casefold())))[:12]
    ranked: list[tuple[int, int, str, str, str]] = []
    position = 0
    for row in rows:
        content = row["content"]
        start = 0
        while start < len(content):
            end = min(start + 4000, len(content))
            if end < len(content):
                boundary = max(
                    content.rfind("\n", start + 2000, end),
                    content.rfind(" ", start + 2000, end),
                )
                if boundary > start:
                    end = boundary + 1
            chunk = content[start:end].strip()
            if chunk:
                haystack = chunk.casefold()
                score = sum(haystack.count(term) for term in terms)
                ranked.append((score, -position, str(row["id"]), row["name"], chunk))
                position += 1
            start = end

    selected: list[dict[str, str]] = []
    used_chars = 0
    for _, _, document_id, name, chunk in sorted(ranked, reverse=True):
        if selected and used_chars + len(chunk) > max_chars:
            continue
        selected.append({"id": document_id, "name": name, "content": chunk})
        used_chars += len(chunk)
        if used_chars >= max_chars or len(selected) >= 6:
            break
    return selected


async def create_conversation(
    title: str,
    provider: str,
    model: str,
    api_url: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    conversation_id = uuid.uuid4()
    parsed_project_id = uuid.UUID(project_id) if project_id else None
    row = await _get_pool().fetchrow(
        """
        INSERT INTO conversations (id, title, provider, model, api_url, project_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        conversation_id,
        title[:200],
        provider,
        model,
        api_url,
        parsed_project_id,
    )
    return _record_to_dict(row)


async def list_conversations(
    project_id: str | None = None,
    *,
    unassigned_only: bool = False,
    search: str = "",
) -> list[dict[str, Any]]:
    parsed_project_id = uuid.UUID(project_id) if project_id else None
    normalized_search = search.strip()[:200]
    rows = await _get_pool().fetch(
        """
        SELECT
            c.id,
            c.title,
            c.provider,
            c.model,
            c.api_url,
            c.project_id,
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
        WHERE (
            ($2::boolean AND c.project_id IS NULL)
            OR (
                NOT $2::boolean
                AND ($1::uuid IS NULL OR c.project_id = $1)
            )
        )
          AND ($3::text = '' OR c.title ILIKE '%' || $3 || '%')
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        """,
        parsed_project_id,
        unassigned_only,
        normalized_search,
    )
    return [_record_to_dict(row) for row in rows]


async def get_project_context(
    project_id: str,
    *,
    query: str,
    exclude_conversation_id: str | None = None,
    limit: int = 12,
    max_chars: int = 30000,
) -> list[dict[str, Any]]:
    """Return relevant and recent messages from other conversations in a project."""
    parsed_project_id = uuid.UUID(project_id)
    parsed_excluded_id = uuid.UUID(exclude_conversation_id) if exclude_conversation_id else None
    rows = await _get_pool().fetch(
        """
        SELECT m.id, m.conversation_id, m.role, m.content, m.entities,
               m.attachment_name, m.attachment_text, m.created_at, c.title
        FROM messages AS m
        JOIN conversations AS c ON c.id = m.conversation_id
        WHERE c.project_id = $1
          AND ($2::uuid IS NULL OR c.id <> $2)
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT 100
        """,
        parsed_project_id,
        parsed_excluded_id,
    )

    terms = list(dict.fromkeys(re.findall(r"\w{3,}", query.casefold())))[:10]
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for position, row in enumerate(rows):
        item = _record_to_dict(row)
        haystack = f"{item['content']} {item.get('attachment_text') or ''}".casefold()
        score = sum(1 for term in terms if term in haystack)
        ranked.append((score, -position, item))

    relevant = [item for score, _, item in sorted(ranked, reverse=True) if score > 0][:8]
    chosen_ids = {item["id"] for item in relevant}
    for _, _, item in ranked:
        if len(relevant) >= limit:
            break
        if item["id"] not in chosen_ids:
            relevant.append(item)
            chosen_ids.add(item["id"])

    selected: list[dict[str, Any]] = []
    used_chars = 0
    for item in sorted(relevant, key=lambda value: (value["created_at"], value["id"])):
        attachment_text = item.get("attachment_text") or ""
        if attachment_text:
            attachment_text = attachment_text[:4000]
        item["attachment_text"] = attachment_text
        item_size = len(item["content"]) + len(attachment_text)
        if selected and used_chars + item_size > max_chars:
            continue
        selected.append(item)
        used_chars += item_size
    return selected


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
        SELECT id, conversation_id, role, content, entity_count, entities, sources,
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


async def update_conversation(
    conversation_id: str,
    *,
    title: str,
    project_id: str | None,
) -> dict[str, Any] | None:
    """Rename a conversation and move it into or out of a project."""
    try:
        parsed_id = uuid.UUID(conversation_id)
        parsed_project_id = uuid.UUID(project_id) if project_id else None
    except ValueError:
        return None

    pool = _get_pool()
    async with pool.acquire() as connection:
        async with connection.transaction():
            previous_project_id = await connection.fetchval(
                "SELECT project_id FROM conversations WHERE id = $1",
                parsed_id,
            )
            row = await connection.fetchrow(
                """
                UPDATE conversations
                SET title = $2, project_id = $3, updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                parsed_id,
                title.strip()[:200],
                parsed_project_id,
            )
            if row is None:
                return None
            project_ids = [
                value
                for value in {previous_project_id, parsed_project_id}
                if value is not None
            ]
            if project_ids:
                await connection.execute(
                    "UPDATE projects SET updated_at = NOW() WHERE id = ANY($1::uuid[])",
                    project_ids,
                )
    return _record_to_dict(row)


async def add_exchange(
    conversation_id: str,
    user_text: str,
    assistant_text: str,
    entity_count: int,
    entities: list[dict[str, str]],
    sources: list[dict[str, Any]] | None = None,
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
                    conversation_id, role, content, entity_count, entities, sources,
                    attachment_name, attachment_text
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8)
                """,
                [
                    (
                        parsed_id,
                        "user",
                        user_text,
                        entity_count,
                        json.dumps(entities),
                        json.dumps([]),
                        attachment_name,
                        attachment_text,
                    ),
                    (
                        parsed_id,
                        "assistant",
                        assistant_text,
                        0,
                        json.dumps(entities),
                        json.dumps(sources or []),
                        None,
                        None,
                    ),
                ],
            )
            await connection.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE id = $1",
                parsed_id,
            )
            await connection.execute(
                """
                UPDATE projects
                SET updated_at = NOW()
                WHERE id = (SELECT project_id FROM conversations WHERE id = $1)
                """,
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
