"""session_store 单测 —— 重点验证多用户隔离(治串台) + 持久化往返 + 重启不丢。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_store import SessionStore  # noqa: E402


def test_set_get_roundtrip(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "thread-A")
    assert s.get_thread("u1", "ck1") == "thread-A"
    s.close()


def test_multi_user_same_conv_key_no_collision(tmp_path):
    """核心: 两用户发完全相同消息 (相同 conv_key), 各自独立 thread, 不串台。"""
    s = SessionStore(str(tmp_path / "s.db"))
    same_ck = "identical-conv-key"
    s.set_thread("alice", same_ck, "thread-alice")
    s.set_thread("bob", same_ck, "thread-bob")
    assert s.get_thread("alice", same_ck) == "thread-alice"
    assert s.get_thread("bob", same_ck) == "thread-bob"
    # alice 看不到 bob 的, 反之亦然
    assert s.get_thread("alice", same_ck) != s.get_thread("bob", same_ck)
    s.close()


def test_persistence_across_restart(tmp_path):
    """重启 = 关连接重开同 db 文件, 映射必须存活 (旧内存 dict 重启全失)。"""
    db = str(tmp_path / "persist.db")
    s1 = SessionStore(db)
    s1.set_thread("u1", "ck1", "thread-persist")
    s1.close()
    # 模拟进程重启: 全新实例打开同文件
    s2 = SessionStore(db)
    assert s2.get_thread("u1", "ck1") == "thread-persist"
    s2.close()


def test_upsert_overwrites(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "old")
    s.set_thread("u1", "ck1", "new")
    assert s.get_thread("u1", "ck1") == "new"
    assert s.count() == 1  # 没产生重复行
    s.close()


def test_get_missing_returns_none(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    assert s.get_thread("nobody", "nothing") is None
    s.close()


def test_empty_key_guarded(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    assert s.get_thread("u1", "") is None
    s.set_thread("u1", "", "x")       # 空 conv_key 不存
    s.set_thread("u1", "ck", "")      # 空 thread_id 不存
    assert s.count() == 0
    s.close()


def test_delete(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "t1")
    s.delete_thread("u1", "ck1")
    assert s.get_thread("u1", "ck1") is None
    s.close()


def test_count_isolated_per_user(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "t1")
    s.set_thread("u1", "ck2", "t2")
    s.set_thread("u2", "ck1", "t3")
    assert s.count() == 3
    s.close()


# ────── 组3 Tick2: archived 关闭会话 ──────
def test_archive_hides_thread_from_get(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "t1")
    assert s.get_thread("u1", "ck1") == "t1"
    assert s.mark_archived_by_thread("t1") is True
    assert s.is_archived_by_thread("t1") is True
    # 归档后 get_thread 不再返回 → 下次发消息起新 thread
    assert s.get_thread("u1", "ck1") is None
    s.close()


def test_set_thread_reactivates_archived(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "t1")
    s.mark_archived_by_thread("t1")
    assert s.get_thread("u1", "ck1") is None
    # 关闭后再发消息(新 thread) → 复位 archived
    s.set_thread("u1", "ck1", "t2")
    assert s.get_thread("u1", "ck1") == "t2"
    assert s.is_archived_by_thread("t2") is False
    s.close()


def test_list_by_user_sorted_desc(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "t1")
    s.set_thread("u1", "ck2", "t2")
    rows = s.list_by_user("u1")
    assert len(rows) == 2
    # 最近(t2)在前
    assert rows[0]["thread_id"] == "t2"
    assert "archived" in rows[0]
    s.close()


def test_mark_archived_miss_returns_false(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    assert s.mark_archived_by_thread("nonexistent") is False
    s.close()


def test_migration_adds_archived_column(tmp_path):
    """老库(无 archived 列)打开后自动 ALTER 补列。"""
    import sqlite3
    db = str(tmp_path / "old.db")
    # 造一个无 archived 列的老库
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE sessions (user_id TEXT, conv_key TEXT, thread_id TEXT, updated_at REAL, PRIMARY KEY(user_id,conv_key))")
    c.execute("INSERT INTO sessions VALUES ('u','ck','t',1.0)")
    c.commit(); c.close()
    # SessionStore 打开 → 迁移补列
    s = SessionStore(db)
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(sessions)")}
    assert "archived" in cols
    assert s.get_thread("u", "ck") == "t"  # 老数据可读, 默认未归档
    s.close()


def test_label_set_and_list(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    s.set_thread("u1", "ck1", "t1")
    assert s.set_label("u1", "t1", "我的会话") is True
    rows = s.list_by_user("u1")
    assert rows[0]["label"] == "我的会话"
    s.close()


def test_label_migration_old_db(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE sessions (user_id TEXT, conv_key TEXT, thread_id TEXT, updated_at REAL, PRIMARY KEY(user_id,conv_key))")
    c.execute("INSERT INTO sessions VALUES ('u','ck','t',1.0)"); c.commit(); c.close()
    s = SessionStore(db)
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(sessions)")}
    assert "label" in cols and "archived" in cols
    assert s.list_by_user("u")[0]["label"] == ""
    s.close()
