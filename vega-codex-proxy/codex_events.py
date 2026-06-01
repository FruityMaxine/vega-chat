"""
codex_events — 把 CodexAppServer.run_turn 的 CodexEvent 归一化成 markdown 文本块。

复刻旧 exec 路径 (_codex_text_stream) 的输出格式, 前端展示无感切换。
关键差异: app-server 给 agent_delta 增量 → 真 token 流式 (旧 exec 是整条 message
一次性 yield)。命令 / 推理 / usage 块保持原样, 去 emoji (UI 铁律: 纯文本符号)。

item 字段名已对 codex 0.130.0 实测:
  commandExecution: command / aggregatedOutput / exitCode / status
  agentMessage: text (走 agent_delta 增量, item_completed 不重复)
  tokenUsage: {total: {inputTokens, outputTokens, cachedInputTokens, ...}}
"""
from __future__ import annotations

from typing import Optional

import codex_schema
from codex_app_server import CodexEvent

# TickB: 提高上限, 靠 LibreChat 原生代码块折叠处理长输出 (旧 3000 硬截断体验差)
_CMD_OUT_LIMIT = 20000
_REASONING_LIMIT = 500
_FALLBACK_FIELD_MAX = 200  # 兜底摘要单字段最长, 超长跳过


def _exit_ok(exit_code) -> bool:
    """退出码是否成功 (防御字符串/None)。"""
    try:
        return int(exit_code) == 0
    except (TypeError, ValueError):
        return exit_code in (0, None)


def _truncate_output(out: str) -> str:
    """超 _CMD_OUT_LIMIT 截断 + 省略提示 (含省略行数/字符数)。"""
    if len(out) <= _CMD_OUT_LIMIT:
        return out
    head = out[:_CMD_OUT_LIMIT]
    omitted_chars = len(out) - _CMD_OUT_LIMIT
    omitted_lines = out.count("\n") - head.count("\n")
    return (
        head
        + f"\n…（输出过长，已省略 {omitted_lines} 行 / {omitted_chars} 字符，完整见服务端日志）"
    )


def _blockquote(title: str, body: str, italic_title: bool = False) -> str:
    """GFM blockquote 卡片: 标题 + 逐行引用正文 (LibreChat 无 rehype-raw, 用 blockquote 替代 details)。"""
    lines = body.splitlines() or [body]
    quoted = "\n".join(f"> {ln}" for ln in lines)
    head = f"> *{title}*" if italic_title else f"> **{title}**"
    return f"\n{head}\n>\n{quoted}\n"


def _fmt_usage(usage) -> str:
    """token usage → inline code 标记 (客户端 inject.js 升级为 chip)。

    TickB(组3): 旧 `<sub>` 因 LibreChat 无 rehype-raw 以尖括号文本显示 (丑 bug)。
    改吐 inline code `vega-usage ...`: inject.js 正则识别替换成样式化 chip;
    即便 JS 失效, 也降级成一个干净的 code chip 而非尖括号文本。
    """
    if not usage or not isinstance(usage, dict):
        return ""
    total = usage.get("total") if "total" in usage else usage
    if not isinstance(total, dict):
        return ""
    inp = total.get("inputTokens") or total.get("input_tokens") or 0
    out = total.get("outputTokens") or total.get("output_tokens") or 0
    cached = total.get("cachedInputTokens") or total.get("cached_input_tokens") or 0
    tot = total.get("totalTokens") or total.get("total_tokens") or (inp + out)
    return f"\n\n`vega-usage in={inp} out={out} cached={cached} total={tot}`"


class EventFormatter:
    """有状态格式化: agent 内容走增量(避免与 item_completed 重复); usage 末尾汇总。"""

    def __init__(self) -> None:
        self._usage: Optional[dict] = None
        self._cmd_count = 0  # 已展示命令数 (多命令间加分隔线)

    def format(self, ev: CodexEvent) -> str:
        kind = ev.kind
        if kind == "agent_delta":
            return ev.text
        if kind == "item_started":
            return self._command_start(ev.item)
        if kind == "item_completed":
            return self._item_completed(ev.item)
        if kind == "token_usage":
            self._usage = ev.usage
            return ""
        if kind == "completed":
            if ev.usage:
                self._usage = ev.usage
            return _fmt_usage(self._usage)
        if kind == "error":
            err = ev.error or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return _blockquote("Codex 错误", msg or "未知错误")
        # started / reasoning_delta / command_output → 不直接输出
        return ""

    def _command_start(self, item) -> str:
        if not item or item.get("type") != "commandExecution":
            return ""
        cmd_text = item.get("command", "") or ""
        # 多命令连续执行加分隔线 (首条不加)
        sep = "\n\n---\n\n" if self._cmd_count else ""
        self._cmd_count += 1
        if cmd_text.startswith('/bin/bash -lc "') or cmd_text.startswith("/bin/bash -lc '"):
            inner = cmd_text[15:-1]
            return f"{sep}`{inner}`\n"
        return f"{sep}```bash\n{cmd_text}\n```\n" if cmd_text else sep

    @staticmethod
    def _item_completed(item) -> str:
        if not item:
            return ""
        itype = item.get("type")
        if itype == "commandExecution":
            # 字段缺失探测: codex 升级把 aggregatedOutput 改名时告警, 不无声丢
            if "aggregatedOutput" not in item and "aggregated_output" not in item:
                codex_schema.warn("missing_field:commandExecution.aggregatedOutput")
            out = (item.get("aggregatedOutput") or item.get("aggregated_output") or "").strip()
            exit_code = item.get("exitCode", item.get("exit_code", 0))
            ok = _exit_ok(exit_code)
            if not out:
                # 无输出: 失败仍提示, 成功静默
                return "" if ok else f"\n**[退出码 {exit_code} · 命令失败]**\n"
            display = _truncate_output(out)
            # 输出用 console 代码块 (LibreChat 原生折叠/复制); 退出状态作块外醒目标记
            block = f"```console\n{display}\n```\n"
            if ok:
                return block
            return f"\n**[退出码 {exit_code} · 命令失败]**\n{block}"
        if itype == "agentMessage":
            if "text" not in item:
                codex_schema.warn("missing_field:agentMessage.text")
            return ""  # 已走 agent_delta 增量, 不重复
        if itype == "userMessage":
            return ""  # 用户自己消息的回显, 已知 type, 静默抑制 (非漂移, 不告警)
        if itype == "reasoning":
            rt = (item.get("text") or "").strip()
            if rt:
                snippet = rt[:_REASONING_LIMIT] + ("…" if len(rt) > _REASONING_LIMIT else "")
                return _blockquote("思考", snippet, italic_title=True)
            return ""
        # 未识别 item type → 兜底展示 + 告警, 不静默丢 (防 codex 升级新增/改名类型无声消失)
        codex_schema.warn("unknown_item_type", str(itype))
        return EventFormatter._fallback_unknown(itype, item)

    @staticmethod
    def _fallback_unknown(itype, item) -> str:
        """未识别 item type 的兜底摘要展示 (跳过冗长/二进制字段)。"""
        parts = []
        for key, val in item.items():
            if key in ("type", "id"):
                continue
            text = str(val)
            if not text or len(text) > _FALLBACK_FIELD_MAX:
                continue
            parts.append(f"{key}={text}")
            if len(parts) >= 4:
                break
        label = itype if itype else "(无 type)"
        summary = "; ".join(parts)
        return f"\n_[未识别项 {label}] {summary}_\n" if summary else f"\n_[未识别项 {label}]_\n"
