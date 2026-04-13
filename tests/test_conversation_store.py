import tempfile
import unittest
from pathlib import Path

from ragflow_service.conversation_store import ConversationStore
from ragflow_service.exceptions import ValidationError


class ConversationStoreTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
