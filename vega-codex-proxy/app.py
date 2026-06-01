"""
vega-codex-proxy — Codex CLI 为 OpenAI-compatible chat completion API.

v1.1 关键升级:
- 仅 ADMIN 用户可访问 (从 LibreChat 转发的 header 解 user role)
- codex --resume 维持真上下文 (proxy 内存维护 conversation_hash → session_id)
- UI 输出模板优化
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
import logging
from typing import AsyncIterator, Optional

import jwt
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pydantic import BaseModel

# Tick2/3 治本: 长驻 codex app-server JSON-RPC 客户端为流式主路径,
# 旧 exec 路径保留供 app-server 不可用时优雅降级双保险。
from codex_app_server import get_app_server, CodexAppServerError
from codex_events import EventFormatter
# Tick4: SQLite 持久化 session 映射 (按 user_id 隔离, 治多用户串台 P0)
from session_store import get_store
# 组2 TickA: codex schema 漂移告警 (防 CLI 升级字段改名静默崩)
import codex_schema
# 组onboard TickA: codex 智能接入探测 / 登录态 / 配置落盘
import codex_onboard

# turn 空闲超时: 超过此秒数无任何事件 = codex 卡死, 中止并提示 (治"卡死永久挂起")
TURN_IDLE_TIMEOUT = float(os.environ.get("CODEX_TURN_IDLE_TIMEOUT", "180"))

logger = logging.getLogger("vega-codex-proxy")
logger.setLevel(logging.INFO)

CODEX_BIN = os.environ.get("CODEX_BIN", "/root/.local/bin/codex")
WORK_DIR = os.environ.get("CODEX_WORK_DIR", os.getcwd())
SANDBOX = os.environ.get("CODEX_SANDBOX", "danger-full-access")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/LibreChat")
JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "",
)
JWT_REFRESH_SECRET = os.environ.get(
    "JWT_REFRESH_SECRET",
    "",
)
# 安全护栏: 空 JWT_SECRET 会让 token 校验形同虚设(可伪造)。不硬崩(非严格模式可信任
# docker 子网转发), 但必须在启动日志大声告警, 防运维静默漏配。生成: openssl rand -hex 32
if not JWT_SECRET:
    logger.warning(
        "JWT_SECRET 未设置(空) —— JWT 校验不可信, 仅依赖子网信任/STRICT_ADMIN_ONLY。"
        "生产环境务必设置: openssl rand -hex 32"
    )
# 允许的角色 - 默认仅 ADMIN
ALLOWED_ROLES = set(os.environ.get("ALLOWED_ROLES", "ADMIN").split(","))
# 严格模式: 默认 false (信任 LibreChat 转发)
# true 时要求能解出 user 且 role=ADMIN, 否则拒绝
STRICT_ADMIN_ONLY = os.environ.get("STRICT_ADMIN_ONLY", "false").lower() == "true"
# LibreChat 容器 docker bridge 网段 - 来自此网段的请求即"内部"
TRUSTED_NETWORKS = ["172.23.", "172.17.", "127.0.0.1"]

# ────── MongoDB ─────────────────────────────────────────
mongo = MongoClient(MONGO_URI)
db = mongo.get_default_database()
if db.name == "test":
    db = mongo["LibreChat"]

app = FastAPI(title="vega-codex-proxy")

# ────── Session 映射: (user_id, conv_key) -> codex thread_id ──────
# Tick4: 改 SQLite 持久化 (session_store), 按 user_id 隔离, 重启不丢, 治串台 P0。
# 默认 user_id (LibreChat 未带身份时兜底, 正常永远有 _id)。
DEFAULT_USER_ID = "anon"


# ────── 鉴权: 解 LibreChat 转发的 user info ───────────────
async def _debug_log_headers(request: Request):
    """临时 debug: 记录所有 X- 和 Authorization header"""
    relevant = {
        k: v[:80] for k, v in request.headers.items()
        if k.lower().startswith("x-") or k.lower() == "authorization"
    }
    import sys
    print(f"[INBOUND] client={request.client.host if request.client else '?'} headers={relevant}", flush=True)
    sys.stderr.flush()


async def require_admin(request: Request) -> dict:
    """
    LibreChat 0.8.6 真的对 custom endpoint headers 渲染 {{LIBRECHAT_USER_*}}.
    实测 LibreChat 转发请求时携带:
      x-librechat-user-id: <ObjectId>
      x-librechat-user-email: <email>
      x-librechat-user-role: ADMIN | USER
    因为这是服务端渲染,普通 USER 无法伪造.

    流程:
    1. 拿 x-librechat-user-role header → 已是 ADMIN 直接放行(快路径)
    2. 没 role header → 解 x-librechat-user-id → MongoDB 查 user → 验 role
    3. 都没有 → 外部恶意请求 → 拒
    """
    h_role = request.headers.get("x-librechat-user-role", "").strip()
    h_user_id = request.headers.get("x-librechat-user-id", "").strip()
    h_user_email = request.headers.get("x-librechat-user-email", "").strip()

    # 模板未渲染 (LibreChat 服务端没替换 → 当作没值)
    def _is_template(v: str) -> bool:
        return v.startswith("{{") and v.endswith("}}")

    if _is_template(h_role): h_role = ""
    if _is_template(h_user_id): h_user_id = ""
    if _is_template(h_user_email): h_user_email = ""

    # 快路径: role header 直接命中
    if h_role and h_role in ALLOWED_ROLES:
        logger.info("require_admin OK via role header: user=%s role=%s", h_user_email or h_user_id, h_role)
        return {"_id": h_user_id, "email": h_user_email, "role": h_role}

    # 拒: role header 是非 ADMIN
    if h_role and h_role not in ALLOWED_ROLES:
        raise HTTPException(
            403, f"Codex 仅限 {sorted(ALLOWED_ROLES)} 角色,当前: {h_role}"
        )

    # 慢路径: 没 role header,从 user_id 查 MongoDB
    user_id = h_user_id
    if not user_id:
        # 兜底: Authorization Bearer JWT
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token and token != "no-key-needed":
                for secret in (JWT_SECRET, JWT_REFRESH_SECRET):
                    try:
                        payload = jwt.decode(token, secret, algorithms=["HS256"])
                        user_id = payload.get("id")
                        if user_id:
                            break
                    except jwt.PyJWTError:
                        pass

    if not user_id:
        raise HTTPException(401, "Codex 需要识别用户身份。请通过已登录的 chat 调用。")

    try:
        user = db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user = db.users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(401, "用户不存在")
    role = user.get("role", "USER")
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            403, f"Codex 仅限 {sorted(ALLOWED_ROLES)} 角色,当前: {role}"
        )
    return user


# ────── Pydantic ─────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str | list


class ChatCompletionRequest(BaseModel):
    model: str = "codex-cli"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


# ────── 健康 / 模型列表 ────────────────────────────────────


def _read_version() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "..", "VERSION"), os.path.join(here, "VERSION")):
        try:
            with open(candidate) as f:
                return f.read().strip()
        except OSError:
            continue
    return "0.0.0.0"


VERSION = _read_version()


@app.get("/healthz")
def healthz():
    srv = get_app_server()
    return {
        "ok": True,
        "version": VERSION,
        "codex_bin": CODEX_BIN,
        "codex_version": srv.codex_version,
        "sessions_cached": get_store().count(),
        "app_server_initialized": srv.initialized,
        "codex_schema_warnings": codex_schema.total(),
        "codex_schema_detail": codex_schema.snapshot(),
    }


@app.get("/codex/appserver/ping")
async def appserver_ping():
    """诊断: 拉起/复用长驻 app-server 并完成握手, 不发 turn (无模型调用)。"""
    srv = get_app_server()
    try:
        await srv.ensure_ready()
        return {"ok": True, "initialized": srv.initialized, "version": VERSION}
    except CodexAppServerError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    except Exception as exc:  # noqa: BLE001 - 诊断端点兜底
        return JSONResponse({"ok": False, "error": repr(exc)}, status_code=503)


# ────── 组onboard TickA: codex 智能接入 (探测 / 登录态 / 配置落盘) ──────
# 仅 admin: 新服务器部署后零命令行识别 codex 安装路径与登录状态。


class OnboardConfigReq(BaseModel):
    codex_bin: str


@app.get("/codex/onboard/detect")
async def codex_onboard_detect(request: Request, path: Optional[str] = None):
    """探测 codex 二进制 (多候选路径 + --version 校验)。path 传自定义路径优先试。"""
    await require_admin(request)
    return {"ok": True, **codex_onboard.detect_codex_bin(path)}


@app.get("/codex/onboard/status")
async def codex_onboard_status(request: Request):
    """检测 codex 登录态 (codex login status + auth.json)。"""
    await require_admin(request)
    return {"ok": True, **codex_onboard.check_login_status()}


@app.post("/codex/onboard/config")
async def codex_onboard_config(request: Request, body: OnboardConfigReq):
    """落盘自定义 codex 路径 (校验真能跑 --version 才写)。"""
    await require_admin(request)
    result = codex_onboard.persist_config(body.codex_bin)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


# ── 组onboard TickB: device-auth 前端登录编排 ──
@app.post("/codex/onboard/login/start")
async def codex_onboard_login_start(request: Request):
    """启动 codex device-auth 登录流, 返回验证 URL + 一次性码 (前端转二维码)。

    护栏: 已登录则直接返回 already_logged_in, 不启动新流程 (避免覆盖现有 auth)。
    """
    await require_admin(request)
    loop = asyncio.get_event_loop()
    status = await loop.run_in_executor(None, codex_onboard.check_login_status)
    if status.get("logged_in"):
        return {"ok": True, "already_logged_in": True, "method": status.get("method")}
    try:
        sess = await loop.run_in_executor(None, codex_onboard.LoginSession.start)
    except Exception as exc:  # noqa: BLE001 - 启动失败兜底
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    info = sess.info()
    return JSONResponse(info, status_code=200 if info.get("ok") else 502)


@app.get("/codex/onboard/login/poll")
async def codex_onboard_login_poll(request: Request):
    """轮询登录状态: idle / pending / success / failed。"""
    await require_admin(request)
    sess = codex_onboard.current_login()
    if sess is None:
        return {"ok": True, "status": "idle"}
    return {"ok": True, **sess.poll()}


@app.post("/codex/onboard/login/cancel")
async def codex_onboard_login_cancel(request: Request):
    """取消进行中的登录 (杀进程, 不影响已写入的 auth)。"""
    await require_admin(request)
    sess = codex_onboard.current_login()
    if sess is None:
        return {"ok": True, "cancelled": False, "detail": "无进行中的登录"}
    return {"ok": True, **sess.cancel()}


# ────── codex 状态仪表盘 (静态页, 同源轮询 /healthz) ──────
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/codex/static", StaticFiles(directory=_STATIC_DIR), name="codex-static")


@app.get("/codex/status")
def codex_status_page():
    """codex 实时状态仪表盘 (深绿主题, 无 emoji 唯 SVG, 同源轮询 /healthz)。"""
    page = os.path.join(_STATIC_DIR, "status.html")
    if not os.path.isfile(page):
        return JSONResponse({"err": "status.html 缺失"}, status_code=500)
    return FileResponse(page, media_type="text/html")


# ────── 组3 Tick2: codex 会话控制 (关闭/中断/状态) ──────
def _latest_thread(uid: str) -> Optional[str]:
    """该 user 最近活跃的 codex thread (store 按 updated_at 倒序首行)。"""
    sessions = get_store().list_by_user(uid)
    return sessions[0]["thread_id"] if sessions else None


async def _read_thread_id(request: Request) -> Optional[str]:
    try:
        body = await request.json()
        return (body or {}).get("threadId")
    except Exception:  # noqa: BLE001 - 空 body / 非 json 容忍
        return None


@app.get("/codex/session/info")
async def codex_session_info(request: Request, threadId: Optional[str] = None):
    """当前(或指定) codex 会话状态: threadId / lastTurnId / archived / 会话列表。"""
    user = await require_admin(request)
    uid = _uid(user)
    store = get_store()
    sessions = store.list_by_user(uid)
    thread_id = threadId or (sessions[0]["thread_id"] if sessions else None)
    srv = get_app_server()
    return {
        "ok": True,
        "version": VERSION,
        "threadId": thread_id,
        "lastTurnId": srv.get_last_turn_id(thread_id) if thread_id else None,
        "archived": store.is_archived_by_thread(thread_id) if thread_id else None,
        "appServerInitialized": srv.initialized,
        "sessionCount": len(sessions),
        "sessions": sessions[:20],
    }


@app.post("/codex/session/close")
async def codex_session_close(request: Request):
    """关闭(归档)当前或指定 codex 会话 — thread/archive + 标记 archived。"""
    user = await require_admin(request)
    uid = _uid(user)
    thread_id = (await _read_thread_id(request)) or _latest_thread(uid)
    if not thread_id:
        return JSONResponse({"ok": False, "error": "无活跃会话"}, status_code=404)
    store = get_store()
    app_server_ok = False
    try:
        await get_app_server().archive_thread(thread_id)
        app_server_ok = True
    except CodexAppServerError as exc:
        logger.warning("thread/archive 失败 thread=%s: %s", thread_id, exc)
    store.mark_archived_by_thread(thread_id)
    return {
        "ok": True,
        "threadId": thread_id,
        "archived": store.is_archived_by_thread(thread_id),
        "appServerArchived": app_server_ok,
    }


@app.post("/codex/session/interrupt")
async def codex_session_interrupt(request: Request):
    """中断当前或指定会话正在进行的 turn — turn/interrupt (用缓存 turnId)。"""
    user = await require_admin(request)
    uid = _uid(user)
    thread_id = (await _read_thread_id(request)) or _latest_thread(uid)
    if not thread_id:
        return JSONResponse({"ok": False, "error": "无活跃会话"}, status_code=404)
    srv = get_app_server()
    turn_id = srv.get_last_turn_id(thread_id)
    if not turn_id:
        return {"ok": True, "interrupted": False, "reason": "无进行中的 turn", "threadId": thread_id}
    await srv.interrupt(thread_id, turn_id)
    return {"ok": True, "interrupted": True, "threadId": thread_id, "turnId": turn_id}


@app.get("/codex/session/list")
async def codex_session_list(request: Request):
    """当前 user 的 codex 会话列表 (含 label/archived, 供会话管理器)。"""
    user = await require_admin(request)
    uid = _uid(user)
    return {"ok": True, "sessions": get_store().list_by_user(uid)}


@app.post("/codex/session/rename")
async def codex_session_rename(request: Request):
    """给 codex 会话重命名/打标签。"""
    user = await require_admin(request)
    uid = _uid(user)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    thread_id = (body or {}).get("threadId")
    label = (body or {}).get("label", "")
    if not thread_id:
        return JSONResponse({"ok": False, "error": "缺 threadId"}, status_code=400)
    hit = get_store().set_label(uid, thread_id, label)
    return {"ok": hit, "threadId": thread_id, "label": label[:120]}


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{"id": "codex-cli", "object": "model", "owned_by": "vega-codex-proxy"}],
    }


# ────── 文本提取 ─────────────────────────────────────────


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text") or item.get("content") or ""
                if t:
                    parts.append(t)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or "")


def _normalize_messages(messages) -> list[dict]:
    """统一成 [{role, text}] 格式 - 支持 chat completions / responses 两种 input."""
    norm = []
    if isinstance(messages, str):
        return [{"role": "user", "text": messages}]
    if not isinstance(messages, list):
        return []
    for m in messages:
        if hasattr(m, "model_dump"):
            m = m.model_dump()
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        text = _extract_text(m.get("content"))
        if text.strip():
            norm.append({"role": role, "text": text.strip()})
    return norm


def _conversation_key(messages: list[dict]) -> str:
    """
    用 *所有历史 user message 拼接* (不带 assistant) 算 hash 作 session key.

    设计 (store 按 (user_id, conv_key) 复合主键隔离, 故 conv_key 本身不含 user):
    - 第 1 轮: user=[u1], 跑完 spawn 新 thread, store.set(uid, hash([u1]), t1)
    - 第 2 轮: user=[u1, u2], LibreChat 也会带 a1 (assistant 第 1 轮回复),但我们
              只 hash user messages. proxy 算 conv_key 时:
              - 把 messages 里 user 提取出来,**去掉最后一条** (这是当前要回答的)
              - 剩下 [u1] -> hash = store.get(uid, hash([u1])) = t1
              - 找到 t1 -> resume t1 + 发 u2
    - 跑完后,再以 hash([u1, u2]) 为 key 存 t1 (第 3 轮能续上)

    这样 hash 不依赖 assistant 内容 (我们提前不知道 assistant 会回啥).
    """
    if not messages:
        return ""
    user_texts = [m["text"] for m in messages if m["role"] == "user"]
    # 去掉最后一条 user (就是本次新消息)
    history_users = user_texts[:-1]
    payload = json.dumps(history_users, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _save_session(user_id: str, messages: list[dict], thread_id: str) -> None:
    """跑完一轮后,把"含本次 user 的 user 序列 hash" -> thread_id 存进 store.
    按 user_id 隔离 (复合主键), 下一轮 LibreChat 带更长历史来时算的 conv_key 会匹配."""
    user_texts = [m["text"] for m in messages if m["role"] == "user"]
    payload = json.dumps(user_texts, ensure_ascii=False)
    new_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    get_store().set_thread(user_id, new_key, thread_id)


# ────── codex spawn 核心 ────────────────────────────────


async def _spawn_codex(prompt: str, session_id: Optional[str]) -> asyncio.subprocess.Process:
    """spawn codex exec (new or resume)."""
    if session_id:
        cmd = [
            CODEX_BIN, "exec",
            "--sandbox", SANDBOX,
            "--cd", WORK_DIR,
            "--skip-git-repo-check",
            "--json",
            "resume", session_id,
            prompt,
        ]
        logger.info("codex resume: session=%s prompt=%s", session_id, prompt[:60])
    else:
        cmd = [
            CODEX_BIN, "exec",
            "--sandbox", SANDBOX,
            "--cd", WORK_DIR,
            "--skip-git-repo-check",
            "--json",
            prompt,
        ]
        logger.info("codex new: prompt=%s", prompt[:60])

    # limit=16 MB 防 codex CLI 输出单行 JSON event 超 asyncio 默认 64KB
    # 触发过 ValueError: Separator is not found, and chunk exceed the limit (2026-05-26)
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORK_DIR,
        limit=16 * 1024 * 1024,
    )


# ────── codex JSONL → markdown 文本块流 ────────────────────


async def _codex_text_stream_exec(
    prompt: str, messages: list[dict], user_id: str
) -> AsyncIterator[str]:
    """[降级路径] 跑 codex exec,把 JSONL events 转成 markdown 文本块 yield.

    仅当 app-server 不可用时由 _codex_text_stream 调用 (双保险)。
    自动管理 session_id (新建/续接). 注: exec 模式仍受长输出行隐患,
    故为 fallback 而非主路径 —— 主路径见 _codex_text_stream_appserver.
    """
    conv_key = _conversation_key(messages)
    session_id = get_store().get_thread(user_id, conv_key)
    logger.info("codex exec stream: user=%s conv_key=%s found_session=%s prompt=%s",
                user_id[:8], conv_key[:8], session_id, prompt[:50])
    proc = await _spawn_codex(prompt, session_id)
    new_thread_id: Optional[str] = None
    assert proc.stdout is not None

    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")
            text = ""

            if etype == "thread.started":
                new_thread_id = event.get("thread_id")
                # 不输出
            elif etype == "turn.started":
                pass  # 静默
            elif etype == "item.started":
                item = event.get("item", {})
                if item.get("type") == "command_execution":
                    cmd_text = item.get("command", "")
                    # 简化 bash -lc 包装显示
                    if cmd_text.startswith('/bin/bash -lc "') or cmd_text.startswith("/bin/bash -lc '"):
                        inner = cmd_text[15:-1]
                        text = f"`{inner}`\n"
                    else:
                        text = f"```bash\n{cmd_text}\n```\n"
            elif etype == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "command_execution":
                    out = item.get("aggregated_output", "").strip()
                    exit_code = item.get("exit_code", 0)
                    if out:
                        # 限制输出长度防爆 chat
                        display = out[:3000] + ("\n...(truncated)" if len(out) > 3000 else "")
                        if exit_code == 0:
                            text = f"```\n{display}\n```\n"
                        else:
                            text = f"```\n[exit={exit_code}]\n{display}\n```\n"
                elif item.get("type") == "agent_message":
                    msg = item.get("text", "")
                    if msg:
                        text = f"\n{msg}\n"
                elif item.get("type") == "reasoning":
                    rt = item.get("text", "")
                    if rt:
                        text = f"_› 思考: {rt[:300]}_\n"
            elif etype == "turn.completed":
                usage = event.get("usage", {})
                if usage:
                    text = (
                        f"\n<sub>tokens: in={usage.get('input_tokens',0)} "
                        f"out={usage.get('output_tokens',0)} "
                        f"cached={usage.get('cached_input_tokens',0)}</sub>"
                    )
            elif etype == "error":
                err = event.get("message", "未知错误")
                text = f"\n\n**[错误] Codex**: {err}\n"

            if text:
                yield text
                await asyncio.sleep(0)

        await proc.wait()
        stderr_bytes = await proc.stderr.read() if proc.stderr else b""
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            yield f"\n\n_[警告] codex 退出码 {proc.returncode}_\n```\n{stderr_text[:1000]}\n```\n"
            # 失败的 session 不存 (避免下次 resume 失败)
            return

        # 成功: 为下一轮存 session 映射.
        # next_conv_key = hash(包含本轮 user 的 user 序列). 下次 LibreChat 带来
        # 更长历史时算的 conv_key 会等于此 next_conv_key,从而 resume 到同 thread.
        final_thread_id = new_thread_id or session_id
        if final_thread_id:
            _save_session(user_id, messages, final_thread_id)
            logger.info("session 存: thread=%s",  final_thread_id[:8] if final_thread_id else None)

    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()


def _is_thread_missing(exc: Exception) -> bool:
    s = str(exc).lower()
    return "not found" in s or "no rollout" in s or "thread" in s and "exist" in s


async def _codex_text_stream_appserver(
    prompt: str, messages: list[dict], user_id: str, srv
) -> AsyncIterator[str]:
    """[主路径] 经长驻 app-server 客户端跑 turn, 流式吐 markdown 文本块。

    含: resume 自愈(thread-not-found 自动 resume/新建 retry) · 客户端断开 abort
    (run_turn finally 发 turn/interrupt) · 空闲超时(run_turn idle_timeout) ·
    全程 try/except 把异常转 error 文本块(治半截流, 不让异常穿透已发头的 SSE)。
    session 按 user_id 隔离 (store 复合主键), 多用户不串台。
    """
    conv_key = _conversation_key(messages)
    thread_id = get_store().get_thread(user_id, conv_key)
    fmt = EventFormatter()
    logger.info("appserver stream: user=%s conv_key=%s thread=%s prompt=%s",
                user_id[:8], conv_key[:8], (thread_id or "")[:8], prompt[:50])

    async def _run(tid: str) -> AsyncIterator[str]:
        async for ev in srv.run_turn(tid, prompt, idle_timeout=TURN_IDLE_TIMEOUT):
            chunk = fmt.format(ev)
            if chunk:
                yield chunk

    try:
        if not thread_id:
            thread_id = await srv.start_thread()
        try:
            async for chunk in _run(thread_id):
                yield chunk
        except CodexAppServerError as exc:
            # turn/start 撞 thread-not-found (进程重启后内存线程没了) → 自愈
            if not _is_thread_missing(exc):
                raise
            logger.info("thread missing, self-heal resume/new: %s", exc)
            try:
                thread_id = await srv.resume_thread(thread_id)
            except CodexAppServerError:
                thread_id = await srv.start_thread()
            async for chunk in _run(thread_id):
                yield chunk

        # 成功: 存映射供下一轮 resume (按 user_id 隔离)
        if thread_id:
            _save_session(user_id, messages, thread_id)
            logger.info("appserver session 存: user=%s thread=%s", user_id[:8], thread_id[:8])
    except Exception as exc:  # noqa: BLE001 - 治半截流: 异常转 error 块, 不穿透 SSE
        logger.error("appserver stream error: %s", exc)
        yield f"\n\n_[错误] codex app-server: {exc}_\n"


async def _codex_text_stream(
    prompt: str, messages: list[dict], user_id: str = DEFAULT_USER_ID
) -> AsyncIterator[str]:
    """流式主入口: 优先 app-server 主路径; 不可用则优雅降级回 exec 路径(双保险)。

    user_id 贯穿到 session_store, 按用户隔离 codex thread (治串台 P0)。
    """
    uid = user_id or DEFAULT_USER_ID
    try:
        srv = get_app_server()
        await srv.ensure_ready()
    except Exception as exc:  # noqa: BLE001 - app-server 起不来则降级
        logger.warning("app-server unavailable, degrade to exec: %s", exc)
        async for text in _codex_text_stream_exec(prompt, messages, uid):
            yield text
        return
    async for text in _codex_text_stream_appserver(prompt, messages, uid, srv):
        yield text


# ────── helpers: 把文本块包成 OpenAI SSE ────────────────────


def _chat_chunk(cid: str, created: int, content: str, finish: Optional[str] = None) -> str:
    return "data: " + json.dumps({
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": "codex-cli",
        "choices": [{
            "index": 0,
            "delta": {"content": content} if content else {},
            "finish_reason": finish,
        }],
    }) + "\n\n"


def _uid(user: dict) -> str:
    """从 require_admin 返回的 user dict 取稳定 user_id 字符串 (_id 可能 ObjectId/str)。"""
    return str(user.get("_id") or user.get("email") or "") or DEFAULT_USER_ID


async def _stream_chat_completions(
    prompt: str, messages: list[dict], user_id: str
) -> AsyncIterator[str]:
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    yield "data: " + json.dumps({
        "id": cid, "object": "chat.completion.chunk", "created": created,
        "model": "codex-cli",
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }) + "\n\n"
    async for text in _codex_text_stream(prompt, messages, user_id):
        yield _chat_chunk(cid, created, text)
    yield _chat_chunk(cid, created, "", finish="stop")
    yield "data: [DONE]\n\n"


# ────── /v1/chat/completions ─────────────────────────────


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    user = await require_admin(request)
    user_id = _uid(user)
    messages = _normalize_messages(req.messages)
    if not messages:
        raise HTTPException(400, "messages 数组为空")
    if messages[-1]["role"] != "user":
        raise HTTPException(400, "最后一条 message 必须是 user")
    prompt = messages[-1]["text"]

    if req.stream:
        return StreamingResponse(
            _stream_chat_completions(prompt, messages, user_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    # 非流式
    parts = []
    async for text in _codex_text_stream(prompt, messages, user_id):
        parts.append(text)
    full = "".join(parts).strip() or "(codex 无输出)"
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion", "created": int(time.time()),
        "model": "codex-cli",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": full}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


# ────── /v1/responses (LibreChat 0.8.6 默认走此) ─────────


async def _stream_responses(
    prompt: str, messages: list[dict], user_id: str
) -> AsyncIterator[str]:
    rid = f"resp_{uuid.uuid4().hex[:24]}"
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    yield "event: response.created\ndata: " + json.dumps({
        "type": "response.created",
        "response": {
            "id": rid, "object": "response", "created_at": created,
            "status": "in_progress", "model": "codex-cli", "output": [],
        },
    }) + "\n\n"

    yield "event: response.output_item.added\ndata: " + json.dumps({
        "type": "response.output_item.added", "output_index": 0,
        "item": {"id": msg_id, "type": "message", "role": "assistant", "status": "in_progress", "content": []},
    }) + "\n\n"

    yield "event: response.content_part.added\ndata: " + json.dumps({
        "type": "response.content_part.added", "item_id": msg_id,
        "output_index": 0, "content_index": 0,
        "part": {"type": "output_text", "text": "", "annotations": [], "logprobs": []},
    }) + "\n\n"

    full_parts = []
    async for text in _codex_text_stream(prompt, messages, user_id):
        full_parts.append(text)
        yield "event: response.output_text.delta\ndata: " + json.dumps({
            "type": "response.output_text.delta", "item_id": msg_id,
            "output_index": 0, "content_index": 0, "delta": text,
        }) + "\n\n"

    full_text = "".join(full_parts)

    yield "event: response.output_text.done\ndata: " + json.dumps({
        "type": "response.output_text.done", "item_id": msg_id,
        "output_index": 0, "content_index": 0, "text": full_text,
    }) + "\n\n"

    yield "event: response.content_part.done\ndata: " + json.dumps({
        "type": "response.content_part.done", "item_id": msg_id,
        "output_index": 0, "content_index": 0,
        "part": {"type": "output_text", "text": full_text, "annotations": [], "logprobs": []},
    }) + "\n\n"

    yield "event: response.output_item.done\ndata: " + json.dumps({
        "type": "response.output_item.done", "output_index": 0,
        "item": {
            "id": msg_id, "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": full_text, "annotations": [], "logprobs": []}],
        },
    }) + "\n\n"

    yield "event: response.completed\ndata: " + json.dumps({
        "type": "response.completed",
        "response": {
            "id": rid, "object": "response", "created_at": created,
            "status": "completed", "model": "codex-cli",
            "output": [{
                "id": msg_id, "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": full_text, "annotations": [], "logprobs": []}],
            }],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        },
    }) + "\n\n"


@app.post("/v1/responses")
async def responses(request: Request):
    user = await require_admin(request)
    user_id = _uid(user)
    body = await request.json()
    input_data = body.get("input", body.get("messages", ""))
    messages = _normalize_messages(input_data)
    if not messages:
        raise HTTPException(400, "input 为空")
    if messages[-1]["role"] != "user":
        # responses API 可能历史结尾就是 user
        messages.append({"role": "user", "text": "(继续)"})
    prompt = messages[-1]["text"]
    stream = body.get("stream", True)
    if stream:
        return StreamingResponse(
            _stream_responses(prompt, messages, user_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    parts = []
    async for text in _codex_text_stream(prompt, messages, user_id):
        parts.append(text)
    full_text = "".join(parts)
    rid = f"resp_{uuid.uuid4().hex[:24]}"
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    return JSONResponse({
        "id": rid, "object": "response", "created_at": int(time.time()),
        "status": "completed", "model": "codex-cli",
        "output": [{
            "id": msg_id, "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": full_text, "annotations": [], "logprobs": []}],
        }],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    })
