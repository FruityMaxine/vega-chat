"""
codex_app_server — 长驻 codex app-server JSON-RPC 客户端 (治本地基).

替代旧 `codex exec` 一问一进程模式: spawn 单个长驻 `codex app-server` 子进程,
JSON-RPC over stdio (newline-delimited JSON)。stdout 用**无上限手动行缓冲**读取
(`read(65536)` + bytearray 累加 + 手动按 \\n 切行), 彻底规避 asyncio
StreamReader.readline() 的 64KB 行长上限 —— 旧 exec 模式 stream 中断的根因。

协议链路 (已对 codex 0.130.0 真 spawn 实测):
    initialize -> initialized(notif) -> thread/start(得 thread.id)
    -> turn/start(input=[{type:text,text}], 得 turn.id)
    -> item/agentMessage/delta(params.delta 增量) ... -> turn/completed
    中断: turn/interrupt(threadId,turnId)；续传: thread/resume(threadId)

进程崩溃懒重启: 任意公开调用前 ensure_ready() 自动重 spawn + 重握手。
notification 按 threadId 路由到各自队列 → 多用户并发互不串台。

本模块只负责传输+协议+生命周期; notification → markdown 文本的归一化留给
Tick3 的 codex_events.py。run_turn 吐出半归一化 CodexEvent。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import codex_schema

# 已处理的内容/生命周期通知方法; 其余 item/* 或 turn/* 未在此 = codex 升级漂移
_HANDLED_METHODS = {
    "item/agentMessage/delta",
    "item/reasoning/textDelta",
    "item/reasoning/summaryTextDelta",
    "item/commandExecution/outputDelta",
    "command/exec/outputDelta",
    "item/started",
    "item/completed",
    "item/plan/delta",
    "error",
    "turn/started",
    "turn/completed",
    "turn/diff/updated",
    "turn/plan/updated",
    "thread/tokenUsage/updated",
}

logger = logging.getLogger("vega-codex-proxy.appserver")

CLIENT_NAME = "vega-codex-proxy"

# 审批型 server→client 请求: danger-full-access 下统一自动 approved
_APPROVAL_METHODS = {
    "execCommandApproval",
    "applyPatchApproval",
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
}


def _read_version() -> str:
    """从项目唯一版本号载体 VERSION 读 (single source of truth)。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(here, "..", "VERSION"),
        os.path.join(here, "VERSION"),
    ):
        try:
            with open(candidate) as f:
                return f.read().strip()
        except OSError:
            continue
    return "0.0.0.0"


CLIENT_VERSION = _read_version()


@dataclass
class CodexEvent:
    """run_turn 吐出的半归一化事件 (Tick3 codex_events.py 再转 markdown)。"""

    kind: str  # started|agent_delta|reasoning_delta|command_output|item_completed|error|completed|token_usage
    text: str = ""
    item: Optional[dict] = None
    error: Optional[dict] = None
    usage: Optional[dict] = None
    turn_id: Optional[str] = None


class CodexAppServerError(RuntimeError):
    """app-server 调用失败 (进程未起 / RPC error / 退出)。"""


def split_ndjson(buf: bytearray) -> list[bytes]:
    """从缓冲区切出所有完整行 (无长度上限), 原地消费已切出字节, 跳过空行。

    这是治本核心: 不用 readline, 故不受 asyncio StreamReader 的 _limit 约束,
    单行可任意长 —— 长输出只是多攒几个 chunk, 永不抛 LimitOverrunError。
    """
    lines: list[bytes] = []
    while True:
        nl = buf.find(b"\n")
        if nl == -1:
            break
        line = bytes(buf[:nl])
        del buf[: nl + 1]
        if line.strip():
            lines.append(line)
    return lines


class CodexAppServer:
    """长驻 codex app-server 客户端 (单例使用, 见 get_app_server)。"""

    def __init__(
        self,
        codex_bin: str,
        work_dir: str,
        sandbox: str = "danger-full-access",
        approval_policy: str = "never",
    ) -> None:
        self._codex_bin = codex_bin
        self._work_dir = work_dir
        self._sandbox = sandbox
        self._approval_policy = approval_policy

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._read_buffer = bytearray()
        self._pending: dict[int, asyncio.Future] = {}
        self._thread_queues: dict[str, asyncio.Queue] = {}
        self._next_id = 0
        self._initialized = False
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self.codex_version: Optional[str] = None  # codex CLI 版本 (schema 漂移参照)
        self._last_turn_id: dict[str, str] = {}  # thread_id → 最近 turnId (供 interrupt 端点)

    # ────── 公开属性 ──────
    @property
    def initialized(self) -> bool:
        return self._initialized and self._proc is not None and self._proc.returncode is None

    # ────── 生命周期 ──────
    async def ensure_ready(self) -> None:
        """幂等保证进程已起且已握手; 崩溃后自动重 spawn (懒重启)。"""
        if self.initialized:
            return
        async with self._start_lock:
            if self.initialized:
                return
            await self._spawn()
            await self._handshake()

    async def _spawn(self) -> None:
        logger.info("spawning codex app-server (bin=%s cwd=%s)", self._codex_bin, self._work_dir)
        self._proc = await asyncio.create_subprocess_exec(
            self._codex_bin,
            "app-server",
            "-c",
            f'approval_policy="{self._approval_policy}"',
            "-c",
            f'sandbox_mode="{self._sandbox}"',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._work_dir,
            # limit 仅影响 readline/readuntil; 我们用 read(n)+手动切行, 不触发。
            # 仍设大值作流控冗余兜底。
            limit=64 * 1024 * 1024,
        )
        self._read_buffer = bytearray()
        self._initialized = False
        self._reader_task = asyncio.create_task(self._reader_loop(self._proc))
        self._stderr_task = asyncio.create_task(self._stderr_loop(self._proc))

    async def _handshake(self) -> None:
        await self._call(
            "initialize",
            {"clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}},
            timeout=30,
        )
        await self._notify("initialized", None)
        self._initialized = True
        if self.codex_version is None:
            await self._probe_version()
        logger.info(
            "codex app-server initialized (client=%s/%s, codex=%s)",
            CLIENT_NAME, CLIENT_VERSION, self.codex_version or "?",
        )

    async def _probe_version(self) -> None:
        """探测 codex CLI 版本 (schema 漂移参照, 一次性, 失败不致命)。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._codex_bin, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            self.codex_version = out.decode("utf-8", errors="replace").strip() or None
        except Exception as exc:  # noqa: BLE001 - 版本探测尽力而为
            logger.warning("codex --version probe failed: %s", exc)

    async def dispose(self) -> None:
        """SIGTERM → 2s → SIGKILL, 清理 task。"""
        proc, self._proc = self._proc, None
        self._initialized = False
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
            except ProcessLookupError:
                pass
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()

    # ────── 读循环 ──────
    async def _reader_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                self._read_buffer.extend(chunk)
                for line in split_ndjson(self._read_buffer):
                    self._handle_line(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 读循环兜底, 进入退出清理
            logger.error("reader loop crashed: %s", exc)
        finally:
            self._on_proc_exit(proc)

    async def _stderr_loop(self, proc: asyncio.subprocess.Process) -> None:
        """分块 drain stderr (非 readline, 防超长行死锁/崩溃)。"""
        assert proc.stderr is not None
        try:
            while True:
                chunk = await proc.stderr.read(65536)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[codex stderr] %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    def _handle_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            logger.warning("non-json line (len=%d): %.120s", len(line), line)
            return
        if isinstance(msg, dict):
            self._handle_message(msg)

    def _handle_message(self, msg: dict) -> None:
        mid = msg.get("id")
        method = msg.get("method")

        # 1) 对请求的响应 (有 id 无 method)
        if mid is not None and method is None:
            fut = self._pending.pop(mid, None)
            if fut is not None and not fut.done():
                if "error" in msg:
                    fut.set_exception(CodexAppServerError(json.dumps(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return

        # 2) server→client 请求 (有 id 有 method) — 需回复, 否则 codex 挂起
        if mid is not None and method is not None:
            self._handle_server_request(mid, method, msg.get("params") or {})
            return

        # 3) 通知 (无 id 有 method) — 按 threadId 路由
        if method is not None:
            params = msg.get("params") or {}
            tid = params.get("threadId")
            queue = self._thread_queues.get(tid) if tid else None
            if queue is not None:
                queue.put_nowait((method, params))

    def _handle_server_request(self, mid: int, method: str, params: dict) -> None:
        if method in _APPROVAL_METHODS:
            asyncio.create_task(self._respond(mid, result={"decision": "approved"}))
        else:
            # 未支持的 server 请求 (tool input / elicitation / auth refresh):
            # 回 error 让 codex 收到响应继续, 不挂起。
            logger.info("unhandled server request: %s", method)
            asyncio.create_task(
                self._respond(
                    mid,
                    error={"code": -32601, "message": f"client does not handle {method}"},
                )
            )

    def _on_proc_exit(self, proc: asyncio.subprocess.Process) -> None:
        if proc is not self._proc:
            return
        code = proc.returncode
        logger.warning("codex app-server exited (code=%s); will lazy-restart on next call", code)
        self._initialized = False
        err = CodexAppServerError(f"app-server exited (code={code})")
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()
        # 通知在途 turn: 注入终止 error
        for tid, queue in list(self._thread_queues.items()):
            queue.put_nowait(
                ("error", {"threadId": tid, "willRetry": False,
                           "error": {"message": f"app-server exited (code={code})"}})
            )
        self._proc = None

    # ────── 写 / 调用 ──────
    async def _write(self, obj: dict) -> None:
        async with self._write_lock:
            proc = self._proc
            if proc is None or proc.stdin is None:
                raise CodexAppServerError("stdin unavailable (app-server not running)")
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            await proc.stdin.drain()

    async def _call(self, method: str, params: Optional[dict], timeout: float = 120) -> dict:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            raise CodexAppServerError("app-server not running")
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._write({"id": mid, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CodexAppServerError(f"rpc {method} timed out after {timeout}s") from exc
        finally:
            self._pending.pop(mid, None)

    async def _notify(self, method: str, params: Optional[dict]) -> None:
        msg: dict = {"method": method}
        if params is not None:
            msg["params"] = params
        await self._write(msg)

    async def _respond(self, mid: int, result: Optional[dict] = None,
                       error: Optional[dict] = None) -> None:
        msg: dict = {"id": mid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result or {}
        try:
            await self._write(msg)
        except CodexAppServerError:
            pass

    # ────── 线程 / 回合 ──────
    async def start_thread(self, cwd: Optional[str] = None) -> str:
        await self.ensure_ready()
        result = await self._call("thread/start", {"cwd": cwd or self._work_dir}, timeout=30)
        return result["thread"]["id"]

    async def resume_thread(self, thread_id: str, cwd: Optional[str] = None) -> str:
        await self.ensure_ready()
        result = await self._call(
            "thread/resume", {"threadId": thread_id, "cwd": cwd or self._work_dir}, timeout=30
        )
        return result["thread"]["id"]

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            await self._call(
                "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10
            )
        except CodexAppServerError as exc:
            logger.warning("interrupt failed (thread=%s turn=%s): %s", thread_id, turn_id, exc)

    def get_last_turn_id(self, thread_id: str) -> Optional[str]:
        """最近一次 turn 的 id (供 interrupt 端点; 无则 None)。"""
        return self._last_turn_id.get(thread_id)

    async def archive_thread(self, thread_id: str) -> None:
        """归档(关闭)一个 thread — codex 唯一的"结束会话"语义, 无硬删。"""
        await self.ensure_ready()
        await self._call("thread/archive", {"threadId": thread_id}, timeout=30)
        self._last_turn_id.pop(thread_id, None)

    async def run_turn(
        self, thread_id: str, text: str, idle_timeout: float = 300
    ) -> AsyncIterator[CodexEvent]:
        """发起一个 turn, 流式吐出 CodexEvent 直到 turn/completed / 终止 error / 超时。

        notification 按 thread_id 路由进本 turn 私有队列, 故多用户并发互不串台。
        生成器被提前关闭 (客户端断开) → finally 自动 turn/interrupt, 不杀进程。
        """
        await self.ensure_ready()
        queue: asyncio.Queue = asyncio.Queue()
        self._thread_queues[thread_id] = queue
        turn_id: Optional[str] = None
        completed = False
        try:
            result = await self._call(
                "turn/start",
                {"threadId": thread_id, "input": [{"type": "text", "text": text}]},
                timeout=60,
            )
            turn_id = (result.get("turn") or {}).get("id")
            if turn_id:
                self._last_turn_id[thread_id] = turn_id  # 缓存供 interrupt 端点
            yield CodexEvent("started", turn_id=turn_id)

            while True:
                try:
                    method, params = await asyncio.wait_for(queue.get(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    yield CodexEvent(
                        "error",
                        error={"message": f"turn idle timeout ({idle_timeout}s)"},
                        turn_id=turn_id,
                    )
                    return

                event = self._normalize(method, params, turn_id)
                if event is not None:
                    yield event

                if method == "turn/completed" and params.get("threadId") == thread_id:
                    completed = True
                    return
                if method == "error" and not params.get("willRetry", False):
                    completed = True
                    return
        finally:
            self._thread_queues.pop(thread_id, None)
            if turn_id and not completed:
                try:
                    await self.interrupt(thread_id, turn_id)
                except Exception:  # noqa: BLE001 - 清理期尽力而为
                    pass

    @staticmethod
    def _normalize(method: str, params: dict, turn_id: Optional[str]) -> Optional[CodexEvent]:
        if method == "item/agentMessage/delta":
            return CodexEvent("agent_delta", text=params.get("delta", ""), turn_id=turn_id)
        if method in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
            return CodexEvent("reasoning_delta", text=params.get("delta", ""), turn_id=turn_id)
        if method in (
            "item/commandExecution/outputDelta",
            "command/exec/outputDelta",
        ):
            return CodexEvent("command_output", text=params.get("delta", ""), turn_id=turn_id)
        if method == "item/started":
            return CodexEvent("item_started", item=params.get("item"), turn_id=turn_id)
        if method == "item/completed":
            return CodexEvent("item_completed", item=params.get("item"), turn_id=turn_id)
        if method == "error":
            return CodexEvent("error", error=params.get("error"), turn_id=turn_id)
        if method == "turn/completed":
            turn = params.get("turn") or {}
            return CodexEvent("completed", usage=turn.get("usage"), turn_id=turn.get("id") or turn_id)
        if method == "thread/tokenUsage/updated":
            return CodexEvent("token_usage", usage=params.get("tokenUsage"), turn_id=turn_id)
        # 未处理的 item/* 或 turn/* = codex 升级新增/改名内容方法 → 告警, 别静默丢。
        # 其余 (thread/status, account/, mcpServer/ 等控制/噪声) 静默忽略。
        if method not in _HANDLED_METHODS and (
            method.startswith("item/") or method.startswith("turn/")
        ):
            codex_schema.warn("unknown_content_method", method)
        return None


# ────── 进程级单例 ──────
_INSTANCE: Optional[CodexAppServer] = None


def get_app_server() -> CodexAppServer:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = CodexAppServer(
            codex_bin=os.environ.get("CODEX_BIN", "/root/.local/bin/codex"),
            work_dir=os.environ.get("CODEX_WORK_DIR", os.getcwd()),
            sandbox=os.environ.get("CODEX_SANDBOX", "danger-full-access"),
        )
    return _INSTANCE
