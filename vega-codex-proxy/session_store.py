"""
session_store — SQLite 持久化 codex session 映射 (替换内存 dict, 治串台 P0)。

表 sessions(user_id, conv_key, thread_id, updated_at) 主键 (user_id, conv_key)。
按 user_id 隔离 → 两用户即便发完全相同的消息文本, conv_key 相同, 但 (user_id,
conv_key) 复合主键不同, 各自独立 codex thread, 不再串台。

WAL 模式 + check_same_thread=False + 进程级单连接 + 线程锁 (FastAPI async 下
SQLite 调用为短同步操作, 锁开销可忽略)。进程重启映射不丢 (旧内存 dict 一重启全失)。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Optional

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    user_id    TEXT NOT NULL,
    conv_key   TEXT NOT NULL,
    thread_id  TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, conv_key)
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
"""


class SessionStore:
    """线程安全的 (user_id, conv_key) → thread_id 持久化映射。"""

    def __init__(self, db_path: str = _DEFAULT_DB) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """老库兼容: 缺列则 ALTER 补 (archived=关闭会话; label=会话重命名)。"""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)")}
        if "archived" not in cols:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        if "label" not in cols:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN label TEXT NOT NULL DEFAULT ''"
            )
        self._conn.commit()

    def set_label(self, user_id: str, thread_id: str, label: str) -> bool:
        """给会话打标签/重命名, 返回是否命中行。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET label=?, updated_at=? WHERE user_id=? AND thread_id=?",
                (label[:120], time.time(), user_id, thread_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_thread(self, user_id: str, conv_key: str) -> Optional[str]:
        # 已归档(关闭)的会话不返回 → 下次发消息自动起新 thread
        if not conv_key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT thread_id FROM sessions WHERE user_id=? AND conv_key=? AND archived=0",
                (user_id, conv_key),
            ).fetchone()
        return row[0] if row else None

    def set_thread(self, user_id: str, conv_key: str, thread_id: str) -> None:
        # 新 turn 写映射时复位 archived=0 (关闭后再发消息即重新激活)
        if not conv_key or not thread_id:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (user_id, conv_key, thread_id, updated_at, archived) "
                "VALUES (?,?,?,?,0) "
                "ON CONFLICT(user_id, conv_key) DO UPDATE SET "
                "thread_id=excluded.thread_id, updated_at=excluded.updated_at, archived=0",
                (user_id, conv_key, thread_id, time.time()),
            )
            self._conn.commit()

    # ────── 组3: 关闭(归档)会话支持 ──────
    def mark_archived_by_thread(self, thread_id: str) -> bool:
        """按 thread_id 标记归档, 返回是否命中行。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET archived=1, updated_at=? WHERE thread_id=?",
                (time.time(), thread_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def is_archived_by_thread(self, thread_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT archived FROM sessions WHERE thread_id=? LIMIT 1", (thread_id,)
            ).fetchone()
        return bool(row[0]) if row else False

    def list_by_user(self, user_id: str) -> list[dict]:
        """该 user 的会话列表 (按 updated_at 倒序, 最近在前)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT conv_key, thread_id, updated_at, archived, label FROM sessions "
                "WHERE user_id=? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [
            {
                "conv_key": r[0], "thread_id": r[1], "updated_at": r[2],
                "archived": bool(r[3]), "label": r[4] or "",
            }
            for r in rows
        ]

    def delete_thread(self, user_id: str, conv_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE user_id=? AND conv_key=?",
                (user_id, conv_key),
            )
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_INSTANCE: Optional[SessionStore] = None


def get_store() -> SessionStore:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SessionStore(os.environ.get("CODEX_SESSION_DB", _DEFAULT_DB))
    return _INSTANCE
