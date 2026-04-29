import types
import unittest
from unittest.mock import patch

from ragflow_service.config import Settings
from ragflow_service.conversation_store import MySQLConversationStore
from ragflow_service.exceptions import ConfigError
from ragflow_service.http_server import ServiceRuntime


class FakeMySQLCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None
        self.results = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))
        self.result = None
        self.results = []
        normalized_sql = " ".join(sql.split()).upper()
        if "INFORMATION_SCHEMA.SCHEMATA" in normalized_sql:
            self.result = {"SCHEMA_NAME": self.connection.state["database"]} if self.connection.state["database_exists"] else None
        elif "INFORMATION_SCHEMA.TABLES" in normalized_sql:
            self.results = [{"TABLE_NAME": table_name} for table_name in self.connection.state["tables"]]
        elif "INFORMATION_SCHEMA.COLUMNS" in normalized_sql:
            self.results = list(self.connection.state["columns"])
        elif "INFORMATION_SCHEMA.STATISTICS" in normalized_sql:
            self.results = list(self.connection.state["indexes"])
        elif "INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS" in normalized_sql:
            self.result = self.connection.state["foreign_key"]
        elif normalized_sql.startswith("CREATE DATABASE"):
            self.connection.state["database_exists"] = True
        elif self.connection.state.get("raise_on_references") and " REFERENCES " in normalized_sql:
            raise RuntimeError(1142, "REFERENCES command denied")

    def executemany(self, sql, params):
        self.connection.statements.append((sql, list(params)))

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.results


class FakeMySQLConnection:
    def __init__(self, state=None):
        self.state = state or build_valid_schema_state(database_exists=False)
        self.statements = []
        self.committed = False
        self.closed = False

    def cursor(self):
        return FakeMySQLCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def build_valid_schema_state(database_exists=True):
    return {
        "database": "ragflow_qa",
        "database_exists": database_exists,
        "tables": {"conversations", "conversation_messages"},
        "columns": [
            _column("conversations", "conversation_id", "varchar", 64, "NO"),
            _column("conversations", "user_id", "varchar", 255, "NO"),
            _column("conversations", "title", "varchar", 120, "NO"),
            _column("conversations", "summary", "text", None, "NO"),
            _column("conversations", "created_at", "varchar", 64, "NO"),
            _column("conversations", "updated_at", "varchar", 64, "NO"),
            _column("conversation_messages", "id", "bigint", None, "NO", extra="auto_increment"),
            _column("conversation_messages", "conversation_id", "varchar", 64, "NO"),
            _column("conversation_messages", "role", "varchar", 32, "NO"),
            _column("conversation_messages", "content", "longtext", None, "NO"),
            _column("conversation_messages", "referenced_documents", "longtext", None, "NO"),
            _column("conversation_messages", "created_at", "varchar", 64, "NO"),
        ],
        "indexes": [
            {"TABLE_NAME": "conversations", "INDEX_NAME": "PRIMARY", "COLUMN_NAME": "conversation_id", "SEQ_IN_INDEX": 1},
            {"TABLE_NAME": "conversations", "INDEX_NAME": "idx_conversations_user_id", "COLUMN_NAME": "user_id", "SEQ_IN_INDEX": 1},
            {"TABLE_NAME": "conversations", "INDEX_NAME": "idx_conversations_updated_at", "COLUMN_NAME": "updated_at", "SEQ_IN_INDEX": 1},
            {
                "TABLE_NAME": "conversation_messages",
                "INDEX_NAME": "PRIMARY",
                "COLUMN_NAME": "id",
                "SEQ_IN_INDEX": 1,
            },
            {
                "TABLE_NAME": "conversation_messages",
                "INDEX_NAME": "idx_conversation_messages_conversation_id",
                "COLUMN_NAME": "conversation_id",
                "SEQ_IN_INDEX": 1,
            },
            {
                "TABLE_NAME": "conversation_messages",
                "INDEX_NAME": "idx_conversation_messages_conversation_id",
                "COLUMN_NAME": "id",
                "SEQ_IN_INDEX": 2,
            },
        ],
        "foreign_key": {"CONSTRAINT_NAME": "fk_conversation_messages_conversation"},
        "raise_on_references": False,
    }


def build_empty_schema_state(database_exists=True):
    return {
        "database": "ragflow_qa",
        "database_exists": database_exists,
        "tables": set(),
        "columns": [],
        "indexes": [],
        "foreign_key": None,
        "raise_on_references": False,
    }


def _column(table, name, data_type, max_length, nullable, extra=""):
    return {
        "TABLE_NAME": table,
        "COLUMN_NAME": name,
        "DATA_TYPE": data_type,
        "CHARACTER_MAXIMUM_LENGTH": max_length,
        "IS_NULLABLE": nullable,
        "EXTRA": extra,
    }


class MySQLConversationStoreTests(unittest.TestCase):
    def test_store_creates_missing_database_and_schema_with_env_connection_settings(self):
        state = build_empty_schema_state(database_exists=False)
        fake_connections = []
        connect_calls = []

        def fake_connect(**kwargs):
            connect_calls.append(kwargs)
            connection = FakeMySQLConnection(state)
            fake_connections.append(connection)
            return connection

        fake_pymysql = types.SimpleNamespace(
            connect=fake_connect,
            cursors=types.SimpleNamespace(DictCursor=object),
        )

        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            MySQLConversationStore(
                host="mysql.local",
                port=3307,
                user="qa_user",
                password="qa-password",
                database="ragflow_qa",
                charset="utf8mb4",
                recent_turn_window=4,
                summary_max_chars=1200,
            )

        self.assertEqual(
            connect_calls[0],
            {
                "host": "mysql.local",
                "port": 3307,
                "user": "qa_user",
                "password": "qa-password",
                "charset": "utf8mb4",
                "cursorclass": object,
                "autocommit": False,
                "connect_timeout": 10,
            },
        )
        self.assertEqual(
            connect_calls[1],
            {
                "host": "mysql.local",
                "port": 3307,
                "user": "qa_user",
                "password": "qa-password",
                "database": "ragflow_qa",
                "charset": "utf8mb4",
                "cursorclass": object,
                "autocommit": False,
                "connect_timeout": 10,
            },
        )
        executed_sql = "\n".join(sql for connection in fake_connections for sql, _ in connection.statements)
        self.assertIn("CREATE DATABASE `ragflow_qa` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS conversations", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS conversation_messages", executed_sql)
        self.assertIn("ON DELETE CASCADE", executed_sql)
        self.assertTrue(all(connection.committed for connection in fake_connections))
        self.assertTrue(all(connection.closed for connection in fake_connections))

    def test_store_accepts_existing_database_when_schema_is_valid(self):
        state = build_valid_schema_state(database_exists=True)
        fake_connections = []

        def fake_connect(**kwargs):
            connection = FakeMySQLConnection(state)
            fake_connections.append(connection)
            return connection

        fake_pymysql = types.SimpleNamespace(
            connect=fake_connect,
            cursors=types.SimpleNamespace(DictCursor=object),
        )

        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            MySQLConversationStore(
                host="mysql.local",
                port=3307,
                user="qa_user",
                password="qa-password",
                database="ragflow_qa",
            )

        executed_sql = "\n".join(sql for connection in fake_connections for sql, _ in connection.statements)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS conversations", executed_sql)
        self.assertNotIn("ALTER TABLE", executed_sql)

    def test_store_repairs_existing_database_schema_after_confirmation(self):
        state = build_empty_schema_state(database_exists=True)
        prompted_issues = []
        fake_connections = []

        def fake_connect(**kwargs):
            connection = FakeMySQLConnection(state)
            fake_connections.append(connection)
            return connection

        fake_pymysql = types.SimpleNamespace(
            connect=fake_connect,
            cursors=types.SimpleNamespace(DictCursor=object),
        )

        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            MySQLConversationStore(
                host="mysql.local",
                port=3306,
                user="qa_user",
                password="qa-password",
                database="ragflow_qa",
                schema_repair_prompt=lambda issues: prompted_issues.extend(issues) or True,
            )

        executed_sql = "\n".join(sql for connection in fake_connections for sql, _ in connection.statements)
        self.assertTrue(prompted_issues)
        self.assertIn("Missing table: conversations", prompted_issues)
        self.assertIn("CREATE TABLE IF NOT EXISTS conversations", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS conversation_messages", executed_sql)

    def test_store_rejects_existing_database_schema_repair_without_confirmation(self):
        state = build_empty_schema_state(database_exists=True)
        fake_pymysql = types.SimpleNamespace(
            connect=lambda **kwargs: FakeMySQLConnection(state),
            cursors=types.SimpleNamespace(DictCursor=object),
        )

        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            with self.assertRaises(ConfigError):
                MySQLConversationStore(
                    host="mysql.local",
                    port=3306,
                    user="qa_user",
                    password="qa-password",
                    database="ragflow_qa",
                    schema_repair_prompt=lambda issues: False,
                )

    def test_store_reports_missing_references_permission(self):
        state = build_valid_schema_state(database_exists=True)
        state["foreign_key"] = None
        state["raise_on_references"] = True
        fake_pymysql = types.SimpleNamespace(
            connect=lambda **kwargs: FakeMySQLConnection(state),
            cursors=types.SimpleNamespace(DictCursor=object),
        )

        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            with self.assertRaisesRegex(ConfigError, "REFERENCES"):
                MySQLConversationStore(
                    host="mysql.local",
                    port=3306,
                    user="qa_user",
                    password="qa-password",
                    database="ragflow_qa",
                    schema_repair_prompt=lambda issues: True,
                )

    def test_store_rejects_unsafe_database_identifier(self):
        with self.assertRaises(ConfigError):
            MySQLConversationStore(
                host="mysql.local",
                port=3306,
                user="qa_user",
                password="qa-password",
                database="ragflow-qa;drop",
            )

    def test_store_raises_config_error_when_pymysql_is_missing(self):
        with patch.dict("sys.modules", {"pymysql": None}):
            with self.assertRaises(ConfigError):
                MySQLConversationStore(
                    host="mysql.local",
                    port=3306,
                    user="qa_user",
                    password="qa-password",
                    database="ragflow_qa",
                )

    def test_runtime_builds_mysql_store_from_settings(self):
        fake_connection = FakeMySQLConnection()

        fake_pymysql = types.SimpleNamespace(
            connect=lambda **kwargs: fake_connection,
            cursors=types.SimpleNamespace(DictCursor=object),
        )

        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            runtime = ServiceRuntime(
                Settings(
                    conversation_store_backend="mysql",
                    conversation_mysql_host="mysql.local",
                    conversation_mysql_port=3306,
                    conversation_mysql_user="qa_user",
                    conversation_mysql_password="qa-password",
                    conversation_mysql_database="ragflow_qa",
                )
            )

        self.assertIsInstance(runtime.get_conversation_store(), MySQLConversationStore)


if __name__ == "__main__":
    unittest.main()
