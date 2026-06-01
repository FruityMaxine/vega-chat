"""codex_events.EventFormatter 单测 —— 验证 CodexEvent → markdown 块归一化 + 无 emoji。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codex_schema  # noqa: E402
from codex_app_server import CodexEvent  # noqa: E402
from codex_events import EventFormatter, _fmt_usage  # noqa: E402

_EMOJI = "💭📊❌⚠️🚀✅🔥"


def _f() -> EventFormatter:
    return EventFormatter()


def test_agent_delta_passthrough():
    assert _f().format(CodexEvent("agent_delta", text="hello")) == "hello"


def test_agent_message_completed_not_duplicated():
    # agentMessage 已走 delta, item_completed 不重复输出
    out = _f().format(CodexEvent("item_completed", item={"type": "agentMessage", "text": "hi"}))
    assert out == ""


def test_command_start_bash_unwrap():
    item = {"type": "commandExecution", "command": "/bin/bash -lc 'echo hi'"}
    out = _f().format(CodexEvent("item_started", item=item))
    assert out == "`echo hi`\n"


def test_command_completed_ok_output():
    item = {"type": "commandExecution", "aggregatedOutput": "vega-test-123\n", "exitCode": 0}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert "vega-test-123" in out and out.startswith("```")


def test_command_completed_nonzero_exit_marker_outside_block():
    # TickB: 退出码标记移到代码块外, 醒目; 无 emoji
    item = {"type": "commandExecution", "aggregatedOutput": "boom", "exitCode": "2"}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert "[退出码 2 · 命令失败]" in out
    assert "```console" in out
    assert not any(e in out for e in _EMOJI)


def test_command_no_output_failure_still_flagged():
    item = {"type": "commandExecution", "aggregatedOutput": "", "exitCode": 1}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert "[退出码 1 · 命令失败]" in out


def test_command_no_output_success_silent():
    item = {"type": "commandExecution", "aggregatedOutput": "", "exitCode": 0}
    assert _f().format(CodexEvent("item_completed", item=item)) == ""


def test_command_output_uses_console_block():
    item = {"type": "commandExecution", "aggregatedOutput": "hello\nworld", "exitCode": 0}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert out.startswith("```console") and "hello\nworld" in out


def test_command_output_truncated_over_20k():
    # TickB: 上限提到 20000, 超限才截断 + 省略提示含行数/字符数
    big = "x" * 25000
    item = {"type": "commandExecution", "aggregatedOutput": big, "exitCode": 0}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert "输出过长" in out and "字符" in out
    assert "(truncated)" not in out  # 旧文案已废


def test_command_output_under_20k_not_truncated():
    item = {"type": "commandExecution", "aggregatedOutput": "y" * 5000, "exitCode": 0}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert "输出过长" not in out  # 5000 < 20000, 不截断 (旧 3000 会截)


def test_reasoning_completed_blockquote_no_emoji():
    item = {"type": "reasoning", "text": "deep thought " * 50}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert "思考" in out and out.lstrip().startswith(">")  # blockquote 卡片
    assert not any(e in out for e in _EMOJI)


def test_error_event_blockquote_card_no_emoji():
    out = _f().format(CodexEvent("error", error={"message": "rate limited"}))
    assert "rate limited" in out and "Codex 错误" in out
    assert "> **Codex 错误**" in out  # GFM blockquote 卡片
    assert not any(e in out for e in _EMOJI)


def test_multi_command_separator():
    f = _f()
    c1 = f.format(CodexEvent("item_started", item={"type": "commandExecution", "command": "/bin/bash -lc 'echo a'"}))
    c2 = f.format(CodexEvent("item_started", item={"type": "commandExecution", "command": "/bin/bash -lc 'echo b'"}))
    assert "---" not in c1  # 首条无分隔
    assert "---" in c2      # 第二条前有分隔线


def test_completed_emits_usage_from_token_usage_state():
    f = _f()
    # token_usage 先到, completed 再汇总
    assert f.format(CodexEvent("token_usage", usage={"total": {"inputTokens": 10, "outputTokens": 5, "cachedInputTokens": 3}})) == ""
    out = f.format(CodexEvent("completed"))
    assert "in=10" in out and "out=5" in out and "cached=3" in out
    assert not any(e in out for e in _EMOJI)


def test_started_and_reasoning_delta_silent():
    f = _f()
    assert f.format(CodexEvent("started", turn_id="t")) == ""
    assert f.format(CodexEvent("reasoning_delta", text="x")) == ""


def test_fmt_usage_handles_flat_and_nested():
    nested = _fmt_usage({"total": {"inputTokens": 1, "outputTokens": 2, "cachedInputTokens": 0}})
    flat = _fmt_usage({"inputTokens": 1, "outputTokens": 2})
    assert "in=1" in nested and "out=2" in nested
    assert "in=1" in flat and "out=2" in flat
    assert _fmt_usage(None) == ""
    assert _fmt_usage({}) == ""


def test_fmt_usage_no_sub_tag_uses_marker():
    """TickB(组3): 不再吐字面 <sub> (丑 bug), 改 inline code vega-usage 标记。"""
    out = _fmt_usage({"total": {"inputTokens": 10, "outputTokens": 5, "cachedInputTokens": 3}})
    assert "<sub>" not in out and "</sub>" not in out
    assert "`vega-usage in=10 out=5 cached=3 total=15`" in out  # inline code, total=in+out


def test_fmt_usage_total_from_field_if_present():
    out = _fmt_usage({"total": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 99}})
    assert "total=99" in out


# ────── 组2 TickA: schema 容错 ──────
def test_unknown_item_type_fallback_not_silent():
    """codex 升级新增/改名 item type → 兜底展示而非静默丢, 且计数告警。"""
    codex_schema.reset()
    item = {"type": "videoClip", "id": "x1", "url": "http://a", "durationMs": 500}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert "未识别项 videoClip" in out
    assert "url=http://a" in out  # 字段摘要可见, 内容没丢
    assert codex_schema.total() >= 1
    assert "unknown_item_type" in codex_schema.snapshot()
    assert not any(e in out for e in _EMOJI)


def test_unknown_item_type_no_type_key():
    codex_schema.reset()
    out = _f().format(CodexEvent("item_completed", item={"foo": "bar"}))
    assert "未识别项" in out


def test_missing_aggregated_output_field_warns():
    """commandExecution 缺 aggregatedOutput (codex 改名) → 告警。"""
    codex_schema.reset()
    item = {"type": "commandExecution", "command": "ls", "exitCode": 0}  # 无 aggregatedOutput 键
    _f().format(CodexEvent("item_completed", item=item))
    assert "missing_field:commandExecution.aggregatedOutput" in codex_schema.snapshot()


def test_user_message_suppressed_not_flagged_as_drift():
    """userMessage(用户消息回显) 是已知 type, 静默抑制, 不当漂移告警也不展示。"""
    codex_schema.reset()
    item = {"type": "userMessage", "id": "u1",
            "content": [{"type": "text", "text": "hi"}]}
    out = _f().format(CodexEvent("item_completed", item=item))
    assert out == ""
    assert codex_schema.total() == 0


def test_known_type_with_field_does_not_warn():
    """正常已知 type 带全字段 → 不告警 (无假阳性)。"""
    codex_schema.reset()
    item = {"type": "commandExecution", "aggregatedOutput": "ok\n", "exitCode": 0}
    _f().format(CodexEvent("item_completed", item=item))
    assert codex_schema.total() == 0


def test_codex_schema_counter_accumulates_and_resets():
    codex_schema.reset()
    codex_schema.warn("a")
    codex_schema.warn("a")
    codex_schema.warn("b", "detail")
    assert codex_schema.total() == 3
    assert codex_schema.snapshot() == {"a": 2, "b": 1}
    codex_schema.reset()
    assert codex_schema.total() == 0
