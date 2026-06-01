"""
codex_schema — codex CLI schema 漂移告警计数 (防升级静默崩)。

codex_events / codex_app_server 的归一化逻辑硬编码了 codex 0.130.0 的 item type
与字段名 (commandExecution/agentMessage/aggregatedOutput/exitCode...)。codex CLI
升级一旦改名/新增, 旧归一化会**静默丢弃**内容 = 新的"静默中断"。本模块在遇到
未识别 item type / 未知 content 通知方法 / 关键字段缺失时累计告警, 经 /healthz
暴露给运维, 让漂移可观测而非无声失效。

放独立模块 (无项目内依赖) 以避免 codex_events ↔ codex_app_server 循环导入。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("vega-codex-proxy.schema")

_lock = threading.Lock()
_warnings: dict[str, int] = {}


def warn(reason: str, detail: str = "") -> None:
    """累计一条 schema 漂移告警。日志做节流 (前 3 次 + 每 50 次), 防刷屏。"""
    with _lock:
        _warnings[reason] = _warnings.get(reason, 0) + 1
        count = _warnings[reason]
    if count <= 3 or count % 50 == 0:
        logger.warning("codex schema drift [%s] x%d %s", reason, count, detail[:160])


def total() -> int:
    """累计告警总数。"""
    with _lock:
        return sum(_warnings.values())


def snapshot() -> dict[str, int]:
    """各 reason → 计数 快照 (供 /healthz 诊断)。"""
    with _lock:
        return dict(_warnings)


def reset() -> None:
    """清零 (主要供测试)。"""
    with _lock:
        _warnings.clear()
