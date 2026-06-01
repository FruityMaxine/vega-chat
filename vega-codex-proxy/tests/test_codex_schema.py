"""codex_schema 单测 —— schema 漂移告警计数器是"防静默中断"的可观测兜底,
本身必须可靠: 计数准确 / 快照隔离 / 日志节流 / 并发安全。"""
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codex_schema  # noqa: E402


def setup_function():
    """每个用例前清零, 避免跨用例污染全局计数。"""
    codex_schema.reset()


def test_warn_increments_count():
    codex_schema.warn("unknown_item", "type=foo")
    codex_schema.warn("unknown_item", "type=bar")
    assert codex_schema.snapshot()["unknown_item"] == 2
    assert codex_schema.total() == 2


def test_total_sums_across_reasons():
    codex_schema.warn("reason_a")
    codex_schema.warn("reason_b")
    codex_schema.warn("reason_b")
    assert codex_schema.total() == 3
    snap = codex_schema.snapshot()
    assert snap == {"reason_a": 1, "reason_b": 2}


def test_snapshot_is_isolated_copy():
    """快照必须是副本: 改它不能影响内部状态(防 /healthz 读取方误改)。"""
    codex_schema.warn("r")
    snap = codex_schema.snapshot()
    snap["r"] = 999
    snap["injected"] = 1
    assert codex_schema.snapshot() == {"r": 1}


def test_reset_clears_all():
    codex_schema.warn("x")
    codex_schema.warn("y")
    assert codex_schema.total() == 2
    codex_schema.reset()
    assert codex_schema.total() == 0
    assert codex_schema.snapshot() == {}


def test_empty_state():
    assert codex_schema.total() == 0
    assert codex_schema.snapshot() == {}


def test_log_throttle_first_3_then_every_50(caplog):
    """节流策略: 前 3 次出日志, 之后每第 50 次出 (4..49 静默)。"""
    with caplog.at_level(logging.WARNING, logger="vega-codex-proxy.schema"):
        for _ in range(100):
            codex_schema.warn("flood", "x")
    # 计数仍精确(节流只影响日志, 不影响计数)
    assert codex_schema.snapshot()["flood"] == 100
    # 日志条数: 第 1,2,3 + 第 50,100 = 5 条
    drift_logs = [r for r in caplog.records if "schema drift" in r.message or "flood" in r.getMessage()]
    assert len(drift_logs) == 5


def test_detail_truncated_in_log(caplog):
    """detail 超长应被截断(防超长行刷屏), 计数不受影响。"""
    long_detail = "A" * 500
    with caplog.at_level(logging.WARNING, logger="vega-codex-proxy.schema"):
        codex_schema.warn("trunc", long_detail)
    assert codex_schema.snapshot()["trunc"] == 1
    # 日志里 detail 被截到 160
    rec = [r for r in caplog.records if r.levelno == logging.WARNING][-1]
    assert "A" * 160 in rec.getMessage()
    assert "A" * 200 not in rec.getMessage()


def test_thread_safety_concurrent_warn():
    """并发 warn 不丢计数(锁保护): 20 线程 x 100 次 = 2000。"""
    def hammer():
        for _ in range(100):
            codex_schema.warn("concurrent")

    threads = [threading.Thread(target=hammer) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codex_schema.snapshot()["concurrent"] == 2000
    assert codex_schema.total() == 2000
