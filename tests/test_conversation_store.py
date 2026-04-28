import sqlite3
import tempfile
import unittest
from pathlib import Path

from ragflow_service.conversation_store import ConversationStore
from ragflow_service.exceptions import ValidationError


class ConversationStoreTests(unittest.TestCase):
    def test_store_lists_conversations_for_user_with_recent_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)

            first = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-a")
            store.set_title(user_id="user-a", conversation_id=first.conversation_id, title="第一个会话")
            store.append_turn(
                user_id="user-a",
                conversation_id=first.conversation_id,
                user_message="第一个问题？",
                assistant_message="第一个答案。",
                referenced_documents=[
                    {
                        "index": 1,
                        "document_name": "流程说明书.docx",
                        "dataset_id": "kb_123",
                        "document_id": "doc_001",
                    }
                ],
            )
            second = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-b")
            store.append_turn(
                user_id="user-a",
                conversation_id=second.conversation_id,
                user_message="第二个问题？",
                assistant_message="第二个答案。",
            )
            other = store.get_or_create_conversation(user_id="user-b", conversation_id="conv-other")
            store.append_turn(
                user_id="user-b",
                conversation_id=other.conversation_id,
                user_message="其他用户问题？",
                assistant_message="其他用户答案。",
            )

            result = store.list_conversations_by_user(user_id="user-a", page=1, page_size=10)

        self.assertEqual(result.user_id, "user-a")
        self.assertEqual(result.total, 2)
        self.assertEqual(result.page, 1)
        self.assertEqual(result.page_size, 10)
        conversation_ids = {conversation.conversation_id for conversation in result.conversations}
        self.assertEqual(conversation_ids, {"conv-a", "conv-b"})
        first_result = next(
            conversation for conversation in result.conversations if conversation.conversation_id == "conv-a"
        )
        self.assertEqual(first_result.title, "第一个会话")
        self.assertEqual(
            [
                (message.role, message.content, message.referenced_documents)
                for message in first_result.recent_messages
            ],
            [
                ("user", "第一个问题？", []),
                (
                    "assistant",
                    "第一个答案。",
                    [
                        {
                            "index": 1,
                            "document_name": "流程说明书.docx",
                            "dataset_id": "kb_123",
                            "document_id": "doc_001",
                        }
                    ],
                ),
            ],
        )
        self.assertTrue(first_result.created_at)
        self.assertTrue(first_result.updated_at)

    def test_store_paginates_user_conversations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)
            for index in range(3):
                state = store.get_or_create_conversation(user_id="user-a", conversation_id=f"conv-{index}")
                store.append_turn(
                    user_id="user-a",
                    conversation_id=state.conversation_id,
                    user_message=f"问题 {index}",
                    assistant_message=f"答案 {index}",
                )

            result = store.list_conversations_by_user(user_id="user-a", page=2, page_size=2)

        self.assertEqual(result.total, 3)
        self.assertEqual(result.page, 2)
        self.assertEqual(result.page_size, 2)
        self.assertEqual(len(result.conversations), 1)

    def test_store_rejects_blank_user_id_when_listing_conversations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)

            with self.assertRaises(ValidationError):
                store.list_conversations_by_user(user_id=" ")

    def test_store_deletes_conversation_for_matching_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)
            first = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-a")
            store.append_turn(
                user_id="user-a",
                conversation_id=first.conversation_id,
                user_message="要删除的问题？",
                assistant_message="要删除的答案。",
            )
            second = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-b")
            store.append_turn(
                user_id="user-a",
                conversation_id=second.conversation_id,
                user_message="保留的问题？",
                assistant_message="保留的答案。",
            )

            store.delete_conversation(user_id="user-a", conversation_id="conv-a")
            result = store.list_conversations_by_user(user_id="user-a", page=1, page_size=10)

        self.assertEqual(result.total, 1)
        self.assertEqual(result.conversations[0].conversation_id, "conv-b")
        self.assertEqual(
            [(message.role, message.content) for message in result.conversations[0].recent_messages],
            [("user", "保留的问题？"), ("assistant", "保留的答案。")],
        )

    def test_store_rejects_delete_from_other_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)
            state = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-shared")

            with self.assertRaises(ValidationError):
                store.delete_conversation(user_id="user-b", conversation_id=state.conversation_id)

    def test_store_keeps_recent_window_and_summarizes_older_turns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=1, summary_max_chars=1000)

            state = store.get_or_create_conversation(user_id="user-a")
            titled = store.set_title(user_id="user-a", conversation_id=state.conversation_id, title="首轮问答标题")
            store.append_turn(
                user_id="user-a",
                conversation_id=state.conversation_id,
                user_message="第一轮问题是什么？",
                assistant_message="第一轮答案是 A。",
            )
            store.append_turn(
                user_id="user-a",
                conversation_id=state.conversation_id,
                user_message="第二轮问题是什么？",
                assistant_message="第二轮答案是 B。",
            )

            updated = store.get_or_create_conversation(user_id="user-a", conversation_id=state.conversation_id)

        self.assertEqual(titled.title, "首轮问答标题")
        self.assertEqual(updated.title, "首轮问答标题")
        self.assertIn("第一轮问题是什么", updated.summary)
        self.assertIn("第一轮答案是 A", updated.summary)
        self.assertEqual(
            [(message.role, message.content) for message in updated.recent_messages],
            [("user", "第二轮问题是什么？"), ("assistant", "第二轮答案是 B。")],
        )

    def test_store_rejects_conversation_id_from_other_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)
            state = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-shared")

            with self.assertRaises(ValidationError):
                store.get_or_create_conversation(user_id="user-b", conversation_id=state.conversation_id)

    def test_store_rejects_title_update_from_other_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)
            state = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-shared")

            with self.assertRaises(ValidationError):
                store.set_title(user_id="user-b", conversation_id=state.conversation_id, title="错误标题")

    def test_store_normalizes_invalid_referenced_documents_to_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)
            state = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-a")

            store.append_turn(
                user_id="user-a",
                conversation_id=state.conversation_id,
                user_message="问题？",
                assistant_message="答案。",
                referenced_documents={"unexpected": "shape"},
            )
            updated = store.get_or_create_conversation(user_id="user-a", conversation_id=state.conversation_id)

        self.assertEqual(updated.recent_messages[-1].referenced_documents, [])

    def test_store_migrates_existing_message_table_for_referenced_documents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "conversations.sqlite3"
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE conversations (
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
                    CREATE TABLE conversation_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )

            store = ConversationStore(db_path, recent_turn_window=2, summary_max_chars=1000)
            state = store.get_or_create_conversation(user_id="user-a", conversation_id="conv-a")
            store.append_turn(
                user_id="user-a",
                conversation_id=state.conversation_id,
                user_message="问题？",
                assistant_message="答案。",
                referenced_documents=[{"index": 1, "document_name": "doc-a"}],
            )
            updated = store.get_or_create_conversation(user_id="user-a", conversation_id=state.conversation_id)

        self.assertEqual(updated.recent_messages[-1].referenced_documents, [{"index": 1, "document_name": "doc-a"}])


if __name__ == "__main__":
    unittest.main()
