from __future__ import annotations

import json
import re
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .exceptions import ConfigError, ValidationError


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
    referenced_documents: list[dict[str, Any]] = field(default_factory=list)


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


MYSQL_EXPECTED_TABLES = ("conversations", "conversation_messages")
MYSQL_EXPECTED_COLUMNS = {
    "conversations": {
        "conversation_id": {"definition": "VARCHAR(64) NOT NULL", "data_type": "varchar", "max_length": 64},
        "user_id": {"definition": "VARCHAR(255) NOT NULL", "data_type": "varchar", "max_length": 255},
        "title": {"definition": "VARCHAR(120) NOT NULL DEFAULT ''", "data_type": "varchar", "max_length": 120},
        "summary": {"definition": "TEXT NOT NULL", "data_type": "text", "max_length": None},
        "created_at": {"definition": "VARCHAR(64) NOT NULL", "data_type": "varchar", "max_length": 64},
        "updated_at": {"definition": "VARCHAR(64) NOT NULL", "data_type": "varchar", "max_length": 64},
    },
    "conversation_messages": {
        "id": {
            "definition": "BIGINT NOT NULL AUTO_INCREMENT",
            "data_type": "bigint",
            "max_length": None,
            "extra": "auto_increment",
        },
        "conversation_id": {"definition": "VARCHAR(64) NOT NULL", "data_type": "varchar", "max_length": 64},
        "role": {"definition": "VARCHAR(32) NOT NULL", "data_type": "varchar", "max_length": 32},
        "content": {"definition": "LONGTEXT NOT NULL", "data_type": "longtext", "max_length": None},
        "referenced_documents": {"definition": "LONGTEXT NOT NULL", "data_type": "longtext", "max_length": None},
        "created_at": {"definition": "VARCHAR(64) NOT NULL", "data_type": "varchar", "max_length": 64},
    },
}
MYSQL_EXPECTED_INDEXES = {
    ("conversations", "idx_conversations_user_id"): ("user_id",),
    ("conversations", "idx_conversations_updated_at"): ("updated_at",),
    ("conversation_messages", "idx_conversation_messages_conversation_id"): ("conversation_id", "id"),
}


class ConversationStoreProtocol(Protocol):
    def list_conversations_by_user(
        self,
        *,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> ConversationHistoryPage:
        ...

    def get_or_create_conversation(self, *, user_id: str, conversation_id: str | None = None) -> ConversationState:
        ...

    def append_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
        referenced_documents: Any = None,
    ) -> ConversationState:
        ...

    def set_title(self, *, user_id: str, conversation_id: str, title: str) -> ConversationState:
        ...

    def delete_conversation(self, *, user_id: str, conversation_id: str) -> None:
        ...


class _BaseConversationStore:
    def __init__(self, *, recent_turn_window: int = 6, summary_max_chars: int = 4000):
        self._recent_turn_window = max(1, int(recent_turn_window))
        self._summary_max_chars = max(200, int(summary_max_chars))
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

        with self._session() as conn:
            total_row = self._fetchone(
                conn,
                "SELECT COUNT(*) AS total FROM conversations WHERE user_id = ?",
                (normalized_user_id,),
            )
            total = int(total_row["total"] or 0) if total_row else 0
            rows = self._fetchall(
                conn,
                """
                SELECT conversation_id, title, summary, created_at, updated_at
                FROM conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC, conversation_id ASC
                LIMIT ? OFFSET ?
                """,
                (normalized_user_id, normalized_page_size, offset),
            )
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

        with self._session() as conn:
            row = self._fetchone(
                conn,
                "SELECT conversation_id, user_id, title, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            )
            if row is None:
                now = _utc_now()
                self._execute(
                    conn,
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
        referenced_documents: Any = None,
    ) -> ConversationState:
        normalized_user_id = self._normalize_identifier(user_id, field_name="user_id")
        normalized_conversation_id = self._normalize_identifier(conversation_id, field_name="conversation_id")
        normalized_user_message = str(user_message or "").strip()
        normalized_assistant_message = str(assistant_message or "").strip()
        normalized_referenced_documents = _normalize_referenced_documents(referenced_documents)
        if not normalized_user_message:
            raise ValidationError("user_message is required")
        if not normalized_assistant_message:
            raise ValidationError("assistant_message is required")

        with self._session() as conn:
            row = self._fetchone(
                conn,
                "SELECT user_id, title, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            )
            if row is None:
                raise ValidationError("conversation_id does not exist")
            if row["user_id"] != normalized_user_id:
                raise ValidationError("conversation_id does not belong to the provided user_id")

            now = _utc_now()
            self._executemany(
                conn,
                """
                INSERT INTO conversation_messages (conversation_id, role, content, referenced_documents, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (normalized_conversation_id, "user", normalized_user_message, "[]", now),
                    (
                        normalized_conversation_id,
                        "assistant",
                        normalized_assistant_message,
                        json.dumps(normalized_referenced_documents, ensure_ascii=False),
                        now,
                    ),
                ],
            )
            self._execute(
                conn,
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, normalized_conversation_id),
            )
            self._compress_history(conn, normalized_conversation_id)

            updated_row = self._fetchone(
                conn,
                "SELECT title, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            )
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

        with self._session() as conn:
            row = self._fetchone(
                conn,
                "SELECT user_id, summary FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            )
            if row is None:
                raise ValidationError("conversation_id does not exist")
            if row["user_id"] != normalized_user_id:
                raise ValidationError("conversation_id does not belong to the provided user_id")

            now = _utc_now()
            self._execute(
                conn,
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

        with self._session() as conn:
            row = self._fetchone(
                conn,
                "SELECT user_id FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            )
            if row is None:
                raise ValidationError("conversation_id does not exist")
            if row["user_id"] != normalized_user_id:
                raise ValidationError("conversation_id does not belong to the provided user_id")

            self._execute(
                conn,
                "DELETE FROM conversations WHERE conversation_id = ?",
                (normalized_conversation_id,),
            )

    def _fetch_messages(self, conn: Any, conversation_id: str) -> list[ConversationMessage]:
        rows = self._fetchall(
            conn,
            """
            SELECT role, content, referenced_documents
            FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        )
        return [
            ConversationMessage(
                role=str(row["role"]),
                content=str(row["content"]),
                referenced_documents=_load_referenced_documents(row["referenced_documents"]),
            )
            for row in rows
            if str(row["content"]).strip()
        ]

    def _compress_history(self, conn: Any, conversation_id: str) -> None:
        rows = self._fetchall(
            conn,
            """
            SELECT id, role, content
            FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        )
        recent_message_limit = self._recent_turn_window * 2
        if len(rows) <= recent_message_limit:
            return

        stale_rows = rows[:-recent_message_limit]
        if not stale_rows:
            return

        conversation_row = self._fetchone(
            conn,
            "SELECT summary FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        existing_summary = str(conversation_row["summary"] or "") if conversation_row else ""
        appended_summary = _build_summary_block(stale_rows)
        merged_summary = _merge_summary(existing_summary, appended_summary, max_chars=self._summary_max_chars)
        now = _utc_now()

        self._execute(
            conn,
            "UPDATE conversations SET summary = ?, updated_at = ? WHERE conversation_id = ?",
            (merged_summary, now, conversation_id),
        )
        self._executemany(
            conn,
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

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            yield conn
            self._commit(conn)
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._close(conn)

    def _initialize(self) -> None:
        raise NotImplementedError

    def _connect(self) -> Any:
        raise NotImplementedError

    def _execute(self, conn: Any, sql: str, params: tuple[Any, ...] | list[tuple[Any, ...]] = ()) -> None:
        raise NotImplementedError

    def _executemany(self, conn: Any, sql: str, params: list[tuple[Any, ...]]) -> None:
        raise NotImplementedError

    def _fetchone(self, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
        raise NotImplementedError

    def _fetchall(self, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        raise NotImplementedError

    def _commit(self, conn: Any) -> None:
        conn.commit()

    def _rollback(self, conn: Any) -> None:
        conn.rollback()

    def _close(self, conn: Any) -> None:
        conn.close()


class ConversationStore(_BaseConversationStore):
    def __init__(self, db_path: str | Path, *, recent_turn_window: int = 6, summary_max_chars: int = 4000):
        self._db_path = Path(db_path).expanduser().resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(recent_turn_window=recent_turn_window, summary_max_chars=summary_max_chars)

    def _initialize(self) -> None:
        with self._session() as conn:
            self._execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            self._execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    referenced_documents TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                )
                """,
            )
            self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)")
            self._execute(
                conn,
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_id
                ON conversation_messages(conversation_id, id)
                """,
            )
            columns = {str(row["name"]) for row in self._fetchall(conn, "PRAGMA table_info(conversations)")}
            if "title" not in columns:
                self._execute(conn, "ALTER TABLE conversations ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            message_columns = {
                str(row["name"]) for row in self._fetchall(conn, "PRAGMA table_info(conversation_messages)")
            }
            if "referenced_documents" not in message_columns:
                self._execute(
                    conn,
                    "ALTER TABLE conversation_messages ADD COLUMN referenced_documents TEXT NOT NULL DEFAULT '[]'",
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _execute(self, conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] | list[tuple[Any, ...]] = ()) -> None:
        conn.execute(sql, params)

    def _executemany(self, conn: sqlite3.Connection, sql: str, params: list[tuple[Any, ...]]) -> None:
        conn.executemany(sql, params)

    def _fetchone(self, conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
        return conn.execute(sql, params).fetchone()

    def _fetchall(self, conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        return conn.execute(sql, params).fetchall()


class MySQLConversationStore(_BaseConversationStore):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        charset: str = "utf8mb4",
        recent_turn_window: int = 6,
        summary_max_chars: int = 4000,
        connect_timeout: int = 10,
        schema_repair_prompt: Callable[[list[str]], bool] | None = None,
    ):
        self._host = str(host or "").strip()
        self._port = int(port)
        self._user = str(user or "").strip()
        self._password = str(password or "")
        self._database = _normalize_mysql_identifier(database, field_name="CONVERSATION_MYSQL_DATABASE")
        self._charset = str(charset or "utf8mb4").strip() or "utf8mb4"
        self._connect_timeout = int(connect_timeout)
        self._schema_repair_prompt = schema_repair_prompt
        super().__init__(recent_turn_window=recent_turn_window, summary_max_chars=summary_max_chars)

    def _initialize(self) -> None:
        database_created = self._ensure_database_exists()
        with self._session() as conn:
            if database_created:
                self._apply_schema_repair(conn)
                return

            schema_issues = self._collect_schema_issues(conn)
            if schema_issues and not self._confirm_schema_repair(schema_issues):
                raise ConfigError(
                    "MySQL conversation database schema is not compatible. "
                    "Run the service in an interactive terminal and confirm schema repair, "
                    "or repair it manually. Issues: "
                    + "; ".join(schema_issues)
                )
            if schema_issues:
                self._apply_schema_repair(conn)

    def _apply_schema_repair(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                title VARCHAR(120) NOT NULL DEFAULT '',
                summary TEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                INDEX idx_conversations_user_id (user_id),
                INDEX idx_conversations_updated_at (updated_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
        )
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                conversation_id VARCHAR(64) NOT NULL,
                role VARCHAR(32) NOT NULL,
                content LONGTEXT NOT NULL,
                referenced_documents LONGTEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL,
                CONSTRAINT fk_conversation_messages_conversation
                    FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE,
                INDEX idx_conversation_messages_conversation_id (conversation_id, id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """,
        )

        tables = self._load_mysql_tables(conn)
        columns = self._load_mysql_columns(conn)
        for table_name, expected_columns in MYSQL_EXPECTED_COLUMNS.items():
            if table_name not in tables:
                continue
            table_columns = columns.get(table_name, {})
            for column_name, expected in expected_columns.items():
                definition = str(expected["definition"])
                if column_name not in table_columns:
                    self._execute(
                        conn,
                        f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {definition}",
                    )
                elif not _mysql_column_matches(table_columns[column_name], expected):
                    self._execute(
                        conn,
                        f"ALTER TABLE `{table_name}` MODIFY COLUMN `{column_name}` {definition}",
                    )

        indexes = self._load_mysql_indexes(conn)
        for (table_name, index_name), index_columns in MYSQL_EXPECTED_INDEXES.items():
            actual_columns = indexes.get((table_name, index_name))
            if table_name in tables and actual_columns != index_columns:
                if actual_columns is not None:
                    self._execute(
                        conn,
                        f"DROP INDEX `{index_name}` ON `{table_name}`",
                    )
                self._execute(
                    conn,
                    f"CREATE INDEX `{index_name}` ON `{table_name}` ({_mysql_index_columns(index_columns)})",
                )

        if "conversation_messages" in tables and self._load_mysql_foreign_key(conn) is None:
            self._execute(
                conn,
                """
                ALTER TABLE conversation_messages
                ADD CONSTRAINT fk_conversation_messages_conversation
                    FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
                """,
            )

    def _ensure_database_exists(self) -> bool:
        conn = self._connect_mysql(database=None)
        try:
            row = self._fetchone(
                conn,
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = ?",
                (self._database,),
            )
            if row is not None:
                self._commit(conn)
                return False

            self._execute(
                conn,
                f"CREATE DATABASE {_quote_mysql_identifier(self._database)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
            )
            self._commit(conn)
            return True
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._close(conn)

    def _collect_schema_issues(self, conn: Any) -> list[str]:
        issues: list[str] = []
        tables = self._load_mysql_tables(conn)
        for table_name in MYSQL_EXPECTED_TABLES:
            if table_name not in tables:
                issues.append(f"Missing table: {table_name}")

        columns = self._load_mysql_columns(conn)
        for table_name, expected_columns in MYSQL_EXPECTED_COLUMNS.items():
            if table_name not in tables:
                continue
            table_columns = columns.get(table_name, {})
            for column_name, expected in expected_columns.items():
                actual = table_columns.get(column_name)
                if actual is None:
                    issues.append(f"Missing column: {table_name}.{column_name}")
                elif not _mysql_column_matches(actual, expected):
                    issues.append(f"Invalid column: {table_name}.{column_name}")

        indexes = self._load_mysql_indexes(conn)
        for index_key, expected_columns in MYSQL_EXPECTED_INDEXES.items():
            table_name, index_name = index_key
            if table_name in tables and indexes.get(index_key) != expected_columns:
                issues.append(f"Missing or invalid index: {table_name}.{index_name}")

        if "conversation_messages" in tables and self._load_mysql_foreign_key(conn) is None:
            issues.append("Missing foreign key: fk_conversation_messages_conversation")

        return issues

    def _load_mysql_tables(self, conn: Any) -> set[str]:
        rows = self._fetchall(
            conn,
            """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = ?
              AND TABLE_NAME IN (?, ?)
            """,
            (self._database, "conversations", "conversation_messages"),
        )
        return {str(row["TABLE_NAME"]) for row in rows}

    def _load_mysql_columns(self, conn: Any) -> dict[str, dict[str, dict[str, Any]]]:
        rows = self._fetchall(
            conn,
            """
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, EXTRA
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ?
              AND TABLE_NAME IN (?, ?)
            """,
            (self._database, "conversations", "conversation_messages"),
        )
        columns: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            table_name = str(row["TABLE_NAME"])
            column_name = str(row["COLUMN_NAME"])
            columns.setdefault(table_name, {})[column_name] = dict(row)
        return columns

    def _load_mysql_indexes(self, conn: Any) -> dict[tuple[str, str], tuple[str, ...]]:
        rows = self._fetchall(
            conn,
            """
            SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = ?
              AND TABLE_NAME IN (?, ?)
            ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
            """,
            (self._database, "conversations", "conversation_messages"),
        )
        indexes: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            index_key = (str(row["TABLE_NAME"]), str(row["INDEX_NAME"]))
            indexes.setdefault(index_key, []).append(str(row["COLUMN_NAME"]))
        return {index_key: tuple(columns) for index_key, columns in indexes.items()}

    def _load_mysql_foreign_key(self, conn: Any) -> Any:
        return self._fetchone(
            conn,
            """
            SELECT CONSTRAINT_NAME
            FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = ?
              AND CONSTRAINT_NAME = ?
            """,
            (self._database, "fk_conversation_messages_conversation"),
        )

    def _confirm_schema_repair(self, issues: list[str]) -> bool:
        if self._schema_repair_prompt is not None:
            return bool(self._schema_repair_prompt(list(issues)))
        if not sys.stdin.isatty():
            return False

        print("MySQL conversation database schema is not compatible:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        answer = input("Repair the MySQL conversation schema now? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    def _connect(self) -> Any:
        return self._connect_mysql(database=self._database)

    def _connect_mysql(self, *, database: str | None) -> Any:
        try:
            import pymysql
        except ImportError as exc:
            from .exceptions import ConfigError

            raise ConfigError("PyMySQL is required when CONVERSATION_STORE_BACKEND=mysql") from exc

        return pymysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            **({"database": database} if database else {}),
            charset=self._charset,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=self._connect_timeout,
        )

    def _execute(self, conn: Any, sql: str, params: tuple[Any, ...] | list[tuple[Any, ...]] = ()) -> None:
        with conn.cursor() as cursor:
            try:
                cursor.execute(_mysql_sql(sql), params)
            except Exception as exc:
                _raise_mysql_permission_error(exc, sql)
                raise

    def _executemany(self, conn: Any, sql: str, params: list[tuple[Any, ...]]) -> None:
        with conn.cursor() as cursor:
            try:
                cursor.executemany(_mysql_sql(sql), params)
            except Exception as exc:
                _raise_mysql_permission_error(exc, sql)
                raise

    def _fetchone(self, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
        with conn.cursor() as cursor:
            try:
                cursor.execute(_mysql_sql(sql), params)
            except Exception as exc:
                _raise_mysql_permission_error(exc, sql)
                raise
            return cursor.fetchone()

    def _fetchall(self, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        with conn.cursor() as cursor:
            try:
                cursor.execute(_mysql_sql(sql), params)
            except Exception as exc:
                _raise_mysql_permission_error(exc, sql)
                raise
            return list(cursor.fetchall())


def _mysql_sql(sql: str) -> str:
    return sql.replace("?", "%s")


def _normalize_mysql_identifier(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ConfigError(f"{field_name} is required")
    if not re.fullmatch(r"[A-Za-z0-9_]+", normalized):
        raise ConfigError(f"{field_name} must contain only letters, numbers, and underscores")
    return normalized


def _quote_mysql_identifier(value: str) -> str:
    return f"`{_normalize_mysql_identifier(value, field_name='MySQL identifier')}`"


def _mysql_column_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    actual_type = str(actual.get("DATA_TYPE") or "").lower()
    if actual_type != str(expected["data_type"]):
        return False

    expected_max_length = expected.get("max_length")
    if expected_max_length is not None and int(actual.get("CHARACTER_MAXIMUM_LENGTH") or 0) != int(expected_max_length):
        return False

    if str(actual.get("IS_NULLABLE") or "").upper() != "NO":
        return False

    expected_extra = str(expected.get("extra") or "").lower()
    if expected_extra and expected_extra not in str(actual.get("EXTRA") or "").lower():
        return False

    return True


def _mysql_index_columns(columns: tuple[str, ...]) -> str:
    return ", ".join(_quote_mysql_identifier(column) for column in columns)


def _raise_mysql_permission_error(exc: Exception, sql: str) -> None:
    args = getattr(exc, "args", ())
    error_code = args[0] if args else None
    if error_code != 1142:
        return

    permission = _detect_mysql_permission(sql)
    raise ConfigError(
        f"MySQL user is missing the {permission} privilege required to initialize the conversation schema. "
        f"Grant {permission} on the configured database, then restart the service."
    ) from exc


def _detect_mysql_permission(sql: str) -> str:
    normalized_sql = " ".join(str(sql or "").split()).upper()
    if " REFERENCES " in normalized_sql or " FOREIGN KEY " in normalized_sql:
        return "REFERENCES"
    if normalized_sql.startswith("CREATE DATABASE") or normalized_sql.startswith("CREATE TABLE"):
        return "CREATE"
    if normalized_sql.startswith("ALTER TABLE"):
        return "ALTER"
    if normalized_sql.startswith("CREATE INDEX") or normalized_sql.startswith("DROP INDEX"):
        return "INDEX"
    if normalized_sql.startswith("SELECT"):
        return "SELECT"
    if normalized_sql.startswith("INSERT"):
        return "INSERT"
    if normalized_sql.startswith("UPDATE"):
        return "UPDATE"
    if normalized_sql.startswith("DELETE"):
        return "DELETE"
    return "required MySQL"


def _build_summary_block(rows: list[Any]) -> str:
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


def _normalize_referenced_documents(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized = [dict(item) for item in value if isinstance(item, dict)]
    try:
        json.dumps(normalized, ensure_ascii=False)
    except (TypeError, ValueError):
        return []
    return normalized


def _load_referenced_documents(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return _normalize_referenced_documents(decoded)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
