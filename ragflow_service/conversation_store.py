from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .exceptions import ValidationError


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ConversationState:
    user_id: str
    conversation_id: str
    title: str
    summary: str
    recent_messages: list[ConversationMessage]
    created: bool = False


@dataclass(frozen=True)
class ConversationHistoryItem:
    conversation_id: str
    title: str
    summary: str
    recent_messages: list[ConversationMessage]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConversationHistoryPage:
    user_id: str
    total: int
    page: int
    page_size: int
    conversations: list[ConversationHistoryItem]


class ConversationStore:
    def __init__(self, db_path: str | Path, *, recent_turn_window: int = 6, summary_max_chars: int = 4000):
        self._db_path = Path(db_path).expanduser().resolve()
        self._recent_turn_window = max(1, int(recent_turn_window))
        self._summary_max_chars = max(200, int(summary_max_chars))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def list_conversations_by_user(
        self,
        *,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> ConversationHistoryPage:
        normalized_user_id = self._normalize_identifier(user_id, field_name="user_id")
        normalized_page = self._normalize_positive_integer(page, field_name="page")
        normalized_page_size = self._normalize_positive_integer(page_size, field_name="page_size", max_value=100)
        offset = (normalized_page - 1) * normalized_page_size

        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM conversations WHERE user_id = ?",
                (normalized_user_id,),
            ).fetchone()
            total = int(total_row["total"] or 0) if total_row else 0
            rows = conn.execute(
                """
                SELECT conversation_id, title, summary, created_at, updated_at
                FROM conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC, conversation_id ASC
                LIMIT ? OFFSET ?
                """,
                (normalized_user_id, normalized_page_size, offset),
            ).fetchall()
            conversations = [
                ConversationHistoryItem(
                    conversation_id=str(row["conversation_id"]),
                    title=str(row["title"] or ""),
                    summary=str(row["summary"] or ""),
                    recent_messages=self._fetch_messages(conn, str(row["conversation_id"])),
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                )
                for row in rows
            ]

        return ConversationHistoryPage(
            user_id=normalized_user_id,
            total=total,
            page=normalized_page,
            page_size=normalized_page_size,
            conversations=conversations,
        )

    def get_or_create_conversation(self, *, user_id: str, conversation_id: str | None = None) -> ConversationState:
        normalized_user_id = self._normalize_identifier(user_id, field_name="user_id")
        normalized_conversation_id = str(conversation_id or "").strip() or uuid.uuid4().hex
        created = False

        with self._connect() as conn:
            row = conn.execute(
                "SELECT conversation_id, user_id, title, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            ).fetchone()
            if row is None:
                now = _utc_now()
                conn.execute(
                    """
                    INSERT INTO conversations (conversation_id, user_id, title, summary, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (normalized_conversation_id, normalized_user_id, "", "", now, now),
                )
                created = True
                title = ""
                summary = ""
            else:
                if row["user_id"] != normalized_user_id:
                    raise ValidationError("conversation_id does not belong to the provided user_id")
                title = str(row["title"] or "")
                summary = str(row["summary"] or "")

            recent_messages = self._fetch_messages(conn, normalized_conversation_id)

        return ConversationState(
            user_id=normalized_user_id,
            conversation_id=normalized_conversation_id,
            title=title,
            summary=summary,
            recent_messages=recent_messages,
            created=created,
        )

    def append_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> ConversationState:
        normalized_user_id = self._normalize_identifier(user_id, field_name="user_id")
        normalized_conversation_id = self._normalize_identifier(conversation_id, field_name="conversation_id")
        normalized_user_message = str(user_message or "").strip()
        normalized_assistant_message = str(assistant_message or "").strip()
        if not normalized_user_message:
            raise ValidationError("user_message is required")
        if not normalized_assistant_message:
            raise ValidationError("assistant_message is required")

        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, title, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("conversation_id does not exist")
            if row["user_id"] != normalized_user_id:
                raise ValidationError("conversation_id does not belong to the provided user_id")

            now = _utc_now()
            conn.executemany(
                """
                INSERT INTO conversation_messages (conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (normalized_conversation_id, "user", normalized_user_message, now),
                    (normalized_conversation_id, "assistant", normalized_assistant_message, now),
                ],
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, normalized_conversation_id),
            )
            self._compress_history(conn, normalized_conversation_id)

            updated_row = conn.execute(
                "SELECT title, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            ).fetchone()
            recent_messages = self._fetch_messages(conn, normalized_conversation_id)

        return ConversationState(
            user_id=normalized_user_id,
            conversation_id=normalized_conversation_id,
            title=str(updated_row["title"] or "") if updated_row else "",
            summary=str(updated_row["summary"] or "") if updated_row else "",
            recent_messages=recent_messages,
            created=False,
        )

    def set_title(self, *, user_id: str, conversation_id: str, title: str) -> ConversationState:
        normalized_user_id = self._normalize_identifier(user_id, field_name="user_id")
        normalized_conversation_id = self._normalize_identifier(conversation_id, field_name="conversation_id")
        normalized_title = self._normalize_title(title)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("conversation_id does not exist")
            if row["user_id"] != normalized_user_id:
                raise ValidationError("conversation_id does not belong to the provided user_id")

            now = _utc_now()
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
                (normalized_title, now, normalized_conversation_id),
            )
            recent_messages = self._fetch_messages(conn, normalized_conversation_id)

        return ConversationState(
            user_id=normalized_user_id,
            conversation_id=normalized_conversation_id,
            title=normalized_title,
            summary=str(row["summary"] or ""),
            recent_messages=recent_messages,
            created=False,
        )

    def delete_conversation(self, *, user_id: str, conversation_id: str) -> None:
        normalized_user_id = self._normalize_identifier(user_id, field_name="user_id")
        normalized_conversation_id = self._normalize_identifier(conversation_id, field_name="conversation_id")

        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("conversation_id does not exist")
            if row["user_id"] != normalized_user_id:
                raise ValidationError("conversation_id does not belong to the provided user_id")

            conn.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)"
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_id
                ON conversation_messages(conversation_id, id)
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "title" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _fetch_messages(self, conn: sqlite3.Connection, conversation_id: str) -> list[ConversationMessage]:
        rows = conn.execute(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()
        return [
            ConversationMessage(role=str(row["role"]), content=str(row["content"]))
            for row in rows
            if str(row["content"]).strip()
        ]

    def _compress_history(self, conn: sqlite3.Connection, conversation_id: str) -> None:
        rows = conn.execute(
            """
            SELECT id, role, content
            FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()
        recent_message_limit = self._recent_turn_window * 2
        if len(rows) <= recent_message_limit:
            return

        stale_rows = rows[:-recent_message_limit]
        if not stale_rows:
            return

        conversation_row = conn.execute(
            "SELECT summary FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        existing_summary = str(conversation_row["summary"] or "") if conversation_row else ""
        appended_summary = _build_summary_block(stale_rows)
        merged_summary = _merge_summary(existing_summary, appended_summary, max_chars=self._summary_max_chars)
        now = _utc_now()

        conn.execute(
            "UPDATE conversations SET summary = ?, updated_at = ? WHERE conversation_id = ?",
            (merged_summary, now, conversation_id),
        )
        conn.executemany(
            "DELETE FROM conversation_messages WHERE id = ?",
            [(int(row["id"]),) for row in stale_rows],
        )

    def _normalize_identifier(self, value: str, *, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValidationError(f"{field_name} is required")
        return normalized

    def _normalize_title(self, value: str) -> str:
        normalized = " ".join(str(value or "").strip().split())
        if not normalized:
            raise ValidationError("title is required")
        return normalized[:120]

    def _normalize_positive_integer(self, value: int, *, field_name: str, max_value: int | None = None) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{field_name} must be a positive integer")
        if normalized < 1:
            raise ValidationError(f"{field_name} must be a positive integer")
        if max_value is not None and normalized > max_value:
            raise ValidationError(f"{field_name} must be less than or equal to {max_value}")
        return normalized


def _build_summary_block(rows: list[sqlite3.Row]) -> str:
    lines: list[str] = []
    for row in rows:
        role = str(row["role"])
        content = _normalize_summary_content(str(row["content"]))
        if not content:
            continue
        label = "用户" if role == "user" else "助手"
        limit = 180 if role == "user" else 260
        lines.append(f"{label}：{_truncate(content, limit)}")
    return "\n".join(lines)


def _merge_summary(existing: str, addition: str, *, max_chars: int) -> str:
    merged = "\n".join(part for part in [existing.strip(), addition.strip()] if part).strip()
    if len(merged) <= max_chars:
        return merged
    trimmed = merged[-(max_chars - 2) :].lstrip()
    return "…\n" + trimmed


def _normalize_summary_content(content: str) -> str:
    return " ".join(content.split())


def _truncate(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return content[: max(0, limit - 1)].rstrip() + "…"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
