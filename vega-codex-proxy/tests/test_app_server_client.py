"""codex_app_server 单测 —— 重点验证无上限行缓冲(治本) + 消息路由 + 握手格式。

用 asyncio.run 跑异步用例, 不依赖 pytest-asyncio 插件。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from codex_app_server import (  # noqa: E402
    CodexAppServer,
    CodexAppServerError,
    CodexEvent,
    split_ndjson,
)


# ────── split_ndjson: 治本核心 ──────
def test_split_basic():
    buf = bytearray(b'{"a":1}\n{"b":2}\n')
    assert split_ndjson(buf) == [b'{"a":1}', b'{"b":2}']
    assert len(buf) == 0


def test_split_partial_keeps_remainder():
    buf = bytearray(b'{"a":1}\n{"b":')
    assert split_ndjson(buf) == [b'{"a":1}']
    assert bytes(buf) == b'{"b":'


def test_split_skips_blank_lines():
    buf = bytearray(b"\n\n{\"a\":1}\n\n")
    assert split_ndjson(buf) == [b'{"a":1}']


def test_split_unbounded_5mb_line():
    """一行 5MB —— 远超 asyncio readline 默认 64KB limit。证明无上限, 不抛错。"""
    payload = b"a" * (5 * 1024 * 1024)
    big = b'{"x":"' + payload + b'"}'
    buf = bytearray(big + b"\n")
    lines = split_ndjson(buf)
    assert len(lines) == 1
    assert len(lines[0]) == len(big)
    assert len(buf) == 0
    # 真能 JSON 解析
    assert json.loads(lines[0])["x"] == payload.decode()


def test_split_multi_chunk_reassembly():
    """模拟超长行被拆进多个 chunk: 累加后仍切回完整一行。"""
    buf = bytearray()
    parts = [b'{"k":"', b"x" * 100000, b"y" * 100000, b'"}\n']
    out = []
    for p in parts:
        buf.extend(p)
        out.extend(split_ndjson(buf))
    assert len(out) == 1
    assert json.loads(out[0])["k"] == "x" * 100000 + "y" * 100000


# ────── 消息分发 ──────
def _srv() -> CodexAppServer:
    return CodexAppServer("codex", "/tmp")


def test_response_resolves_future():
    async def run():
        srv = _srv()
        fut = asyncio.get_event_loop().create_future()
        srv._pending[7] = fut
        srv._handle_message({"id": 7, "result": {"ok": 1}})
        return fut.result()

    assert asyncio.run(run()) == {"ok": 1}


def test_error_response_raises():
    async def run():
        srv = _srv()
        fut = asyncio.get_event_loop().create_future()
        srv._pending[8] = fut
        srv._handle_message({"id": 8, "error": {"message": "boom"}})
        return fut

    fut = asyncio.run(run())
    with pytest.raises(CodexAppServerError):
        fut.result()


def test_notification_routed_to_thread_queue():
    async def run():
        srv = _srv()
        q: asyncio.Queue = asyncio.Queue()
        srv._thread_queues["T1"] = q
        srv._handle_message(
            {"method": "item/agentMessage/delta", "params": {"threadId": "T1", "delta": "hi"}}
        )
        return q.get_nowait()

    method, params = asyncio.run(run())
    assert method == "item/agentMessage/delta"
    assert params["delta"] == "hi"


def test_notification_unknown_thread_dropped():
    # 没注册队列 → 静默丢, 不抛错 (防多用户路由 KeyError)
    srv = _srv()
    srv._handle_message({"method": "x", "params": {"threadId": "ZZZ"}})


def test_notification_no_threadid_ignored():
    srv = _srv()
    srv._handle_message({"method": "account/rateLimits/updated", "params": {}})


def test_server_request_approval_auto_approved():
    async def run():
        srv = _srv()
        sent: list = []

        async def fake_write(obj):
            sent.append(obj)

        srv._write = fake_write
        srv._handle_message({"id": 5, "method": "execCommandApproval", "params": {}})
        await asyncio.sleep(0)  # 让 create_task 调度的 _respond 跑完
        return sent

    sent = asyncio.run(run())
    assert sent and sent[0]["id"] == 5
    assert sent[0]["result"]["decision"] == "approved"


def test_server_request_unknown_returns_error():
    async def run():
        srv = _srv()
        sent: list = []

        async def fake_write(obj):
            sent.append(obj)

        srv._write = fake_write
        srv._handle_message({"id": 9, "method": "item/tool/requestUserInput", "params": {}})
        await asyncio.sleep(0)
        return sent

    sent = asyncio.run(run())
    assert sent and sent[0]["id"] == 9
    assert sent[0]["error"]["code"] == -32601


# ────── _normalize ──────
def test_normalize_agent_delta():
    ev = CodexAppServer._normalize("item/agentMessage/delta", {"delta": "abc"}, "t1")
    assert ev.kind == "agent_delta" and ev.text == "abc" and ev.turn_id == "t1"


def test_normalize_reasoning_delta():
    ev = CodexAppServer._normalize("item/reasoning/textDelta", {"delta": "think"}, "t")
    assert ev.kind == "reasoning_delta" and ev.text == "think"


def test_normalize_completed_extracts_usage():
    ev = CodexAppServer._normalize(
        "turn/completed", {"turn": {"id": "T9", "usage": {"total": 5}}}, "t"
    )
    assert ev.kind == "completed" and ev.usage == {"total": 5} and ev.turn_id == "T9"


def test_normalize_noise_returns_none():
    assert CodexAppServer._normalize("account/rateLimits/updated", {}, "t") is None
    assert CodexAppServer._normalize("thread/status/changed", {}, "t") is None


# ────── 组2 TickA: _normalize 内容漂移告警 ──────
def test_normalize_unknown_content_method_warns():
    """未处理的 item/* 或 turn/* (codex 升级新增/改名) → 告警, 返 None 不崩。"""
    import codex_schema

    codex_schema.reset()
    assert CodexAppServer._normalize("item/brandNewThing/delta", {"delta": "x"}, "t") is None
    assert CodexAppServer._normalize("turn/somethingNew", {}, "t") is None
    snap = codex_schema.snapshot()
    assert snap.get("unknown_content_method") == 2


def test_normalize_control_method_does_not_warn():
    """控制/噪声方法 (非 item//turn/) 不告警, 避免假阳性刷屏。"""
    import codex_schema

    codex_schema.reset()
    CodexAppServer._normalize("account/rateLimits/updated", {}, "t")
    CodexAppServer._normalize("thread/status/changed", {}, "t")
    CodexAppServer._normalize("mcpServer/startupStatus/updated", {}, "t")
    assert codex_schema.total() == 0


# ────── 组3 Tick2: turnId 缓存 + archive ──────
def test_last_turn_id_cache():
    srv = _srv()
    assert srv.get_last_turn_id("T1") is None
    srv._last_turn_id["T1"] = "turn-abc"
    assert srv.get_last_turn_id("T1") == "turn-abc"


def test_archive_clears_turn_id_cache():
    async def run():
        srv = _srv()
        srv._last_turn_id["T1"] = "turn-abc"
        sent = []

        async def fake_call(method, params, timeout=120):
            sent.append((method, params))
            return {}

        async def fake_ready():
            return None

        srv._call = fake_call
        srv.ensure_ready = fake_ready
        await srv.archive_thread("T1")
        return sent, srv.get_last_turn_id("T1")

    sent, last = asyncio.run(run())
    assert sent and sent[0][0] == "thread/archive" and sent[0][1] == {"threadId": "T1"}
    assert last is None  # 归档后清缓存


# ────── 进程退出清理 ──────
def test_on_proc_exit_fails_pending_and_injects_error():
    async def run():
        srv = _srv()

        class _FakeProc:
            returncode = 1

        fake = _FakeProc()
        srv._proc = fake
        pend = asyncio.get_event_loop().create_future()
        srv._pending[1] = pend
        q: asyncio.Queue = asyncio.Queue()
        srv._thread_queues["T1"] = q
        srv._on_proc_exit(fake)
        return pend, q

    pend, q = asyncio.run(run())
    with pytest.raises(CodexAppServerError):
        pend.result()
    method, params = q.get_nowait()
    assert method == "error" and params["willRetry"] is False
