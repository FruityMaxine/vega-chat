"""
Vega Chat Admin — 轻量 admin 后端
- 复用 LibreChat JWT 鉴权（解 refreshToken cookie）
- 内嵌入主 chat 网站 /vega-admin/* 路径
- 仅 role=ADMIN 用户可访问
"""

import asyncio
import json as _json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
from fastapi import Cookie, FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from pymongo import MongoClient
from bson import ObjectId

# ────── 环境变量 ──────
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/LibreChat")
JWT_REFRESH_SECRET = os.environ.get(
    "JWT_REFRESH_SECRET",
    "",
)
ENV_FILE = os.environ.get(
    "ENV_FILE",
    str(Path(__file__).resolve().parents[2] / "docker" / ".env"),
)
STATIC_DIR = Path(__file__).parent.parent / "static"

# ────── 数据库 ──────
mongo = MongoClient(MONGO_URI)
db = mongo.get_default_database()
if db.name == "test":
    db = mongo["LibreChat"]

# ────── FastAPI ──────
app = FastAPI(title="Vega Chat Admin")


# ────── 鉴权辅助 ──────
JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "",
)
# 安全护栏: 空 JWT_SECRET 会让 admin token 校验可被伪造。启动时大声告警, 防静默漏配。
# 生成: openssl rand -hex 32
if not JWT_SECRET:
    import sys

    print(
        "[vega-admin] 警告: JWT_SECRET 未设置(空) —— admin token 校验不可信, "
        "生产环境务必设置 (openssl rand -hex 32)",
        file=sys.stderr,
    )


def _decode_token(token: str, secret: str) -> Optional[dict]:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def get_current_user(
    refresh_token: Optional[str] = None,
    bearer: Optional[str] = None,
) -> dict:
    """解 refreshToken cookie 或 Bearer JWT(LibreChat access_token) → 查 user。"""
    payload = None
    if bearer:
        # LibreChat access_token (JWT_SECRET 签)
        payload = _decode_token(bearer, JWT_SECRET) or _decode_token(bearer, JWT_REFRESH_SECRET)
    if not payload and refresh_token:
        payload = _decode_token(refresh_token, JWT_REFRESH_SECRET)
    if not payload:
        raise HTTPException(401, "未登录")
    user_id = payload.get("id")
    if not user_id:
        raise HTTPException(401, "Token 缺 id")
    try:
        user = db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user = db.users.find_one({"_id": user_id})
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def _bearer_from_header(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_admin(
    refresh_token: Optional[str] = None,
    bearer: Optional[str] = None,
) -> dict:
    user = get_current_user(refresh_token=refresh_token, bearer=bearer)
    if user.get("role") != "ADMIN":
        raise HTTPException(403, "需要 ADMIN 权限")
    return user


def serialize_user(u: dict) -> dict:
    return {
        "_id": str(u.get("_id")),
        "email": u.get("email"),
        "name": u.get("name"),
        "username": u.get("username"),
        "role": u.get("role"),
        "provider": u.get("provider"),
        "emailVerified": u.get("emailVerified"),
        "createdAt": u.get("createdAt").isoformat() if u.get("createdAt") else None,
        "updatedAt": u.get("updatedAt").isoformat() if u.get("updatedAt") else None,
    }


# ────── 路由 ──────


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/me")
def me(refreshToken: Optional[str] = Cookie(None)):
    """前端浮动按钮注入脚本调这个判断是否 admin。"""
    try:
        user = get_current_user(refreshToken)
    except HTTPException:
        return JSONResponse({"loggedIn": False}, status_code=200)
    return {
        "loggedIn": True,
        "isAdmin": user.get("role") == "ADMIN",
        "user": serialize_user(user),
    }


@app.get("/api/users")
def list_users(refreshToken: Optional[str] = Cookie(None)):
    require_admin(refreshToken)
    users = list(db.users.find({}, {"password": 0, "refreshToken": 0}).sort("createdAt", -1))
    return [serialize_user(u) for u in users]


class CreateUserBody(BaseModel):
    email: EmailStr
    name: str
    username: str
    password: Optional[str] = None  # 不传则随机
    role: str = "USER"


@app.post("/api/users")
def create_user(body: CreateUserBody, refreshToken: Optional[str] = Cookie(None)):
    require_admin(refreshToken)
    if body.role not in ("ADMIN", "USER"):
        raise HTTPException(400, "role 必须是 ADMIN 或 USER")
    if db.users.find_one({"email": body.email}):
        raise HTTPException(409, "邮箱已存在")
    pw = body.password or secrets.token_urlsafe(15)
    hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode()
    doc = {
        "email": body.email,
        "password": hashed,
        "name": body.name,
        "username": body.username,
        "provider": "local",
        "role": body.role,
        "emailVerified": True,
        "twoFactorEnabled": False,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }
    result = db.users.insert_one(doc)
    return {
        "ok": True,
        "_id": str(result.inserted_id),
        "tempPassword": pw,
        "message": f"用户已建，临时密码：{pw}（用户登录后请改密）",
    }


class UpdateUserBody(BaseModel):
    role: Optional[str] = None
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


@app.patch("/api/users/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUserBody,
    refreshToken: Optional[str] = Cookie(None),
):
    me_user = require_admin(refreshToken)
    update = {"updatedAt": datetime.utcnow()}
    if body.role:
        if body.role not in ("ADMIN", "USER"):
            raise HTTPException(400, "role 必须是 ADMIN 或 USER")
        # 不允许自己取消自己的 ADMIN（防自锁）
        if str(me_user["_id"]) == user_id and body.role != "ADMIN":
            raise HTTPException(400, "不允许取消自己的 ADMIN 权限")
        update["role"] = body.role
    if body.name:
        update["name"] = body.name
    if body.username:
        update["username"] = body.username
    if body.password:
        update["password"] = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt(10)).decode()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, "无效 user_id")
    r = db.users.update_one({"_id": oid}, {"$set": update})
    if r.matched_count == 0:
        raise HTTPException(404, "用户不存在")
    return {"ok": True, "modified": r.modified_count}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: str, refreshToken: Optional[str] = Cookie(None)):
    me_user = require_admin(refreshToken)
    if str(me_user["_id"]) == user_id:
        raise HTTPException(400, "不允许删除自己")
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, "无效 user_id")
    r = db.users.delete_one({"_id": oid})
    if r.deleted_count == 0:
        raise HTTPException(404, "用户不存在")
    return {"ok": True}


@app.get("/api/registration")
def get_registration(refreshToken: Optional[str] = Cookie(None)):
    require_admin(refreshToken)
    open_status = False
    if Path(ENV_FILE).exists():
        for line in Path(ENV_FILE).read_text().splitlines():
            if line.startswith("ALLOW_REGISTRATION="):
                open_status = line.split("=", 1)[1].strip().lower() == "true"
                break
    return {"open": open_status}


class RegistrationBody(BaseModel):
    open: bool


@app.post("/api/registration")
def set_registration(
    body: RegistrationBody, refreshToken: Optional[str] = Cookie(None)
):
    require_admin(refreshToken)
    if not Path(ENV_FILE).exists():
        raise HTTPException(500, "未找到 .env 文件")
    env_path = Path(ENV_FILE)
    content = env_path.read_text()
    new_val = "true" if body.open else "false"
    if "ALLOW_REGISTRATION=" in content:
        import re
        content = re.sub(
            r"^ALLOW_REGISTRATION=.*$",
            f"ALLOW_REGISTRATION={new_val}",
            content,
            flags=re.MULTILINE,
        )
    else:
        content += f"\nALLOW_REGISTRATION={new_val}\n"
    env_path.write_text(content)
    # 注意：env 改了要 force-recreate 才能生效。这里不自动重启，前端会提示用户。
    return {"ok": True, "note": "已写入 .env。生效需 docker compose up -d --force-recreate api"}


# ====================================================================
# Balances & Usage — LibreChat 用户配额管理
# 数据源:MongoDB `balances` collection(LibreChat 内建)
# admin-panel 通过 host.docker.internal:3082 调这些 endpoint
# ====================================================================


def serialize_balance(b: dict) -> dict:
    """balances collection 文档转 JSON 友好格式"""
    return {
        "_id": str(b.get("_id")),
        "user": str(b.get("user", "")),
        "tokenCredits": int(b.get("tokenCredits", 0)),
        "autoRefillEnabled": bool(b.get("autoRefillEnabled", False)),
        "refillIntervalValue": b.get("refillIntervalValue"),
        "refillIntervalUnit": b.get("refillIntervalUnit"),
        "refillAmount": b.get("refillAmount"),
        "lastRefill": b.get("lastRefill").isoformat() if b.get("lastRefill") else None,
    }


@app.get("/api/balances")
def list_balances(
    refreshToken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """列出所有用户的 balance,join users 显示 email/name"""
    require_admin(refresh_token=refreshToken, bearer=_bearer_from_header(authorization))
    balances = list(db.balances.find({}))
    # join users
    user_ids = [b.get("user") for b in balances if b.get("user")]
    users = {str(u["_id"]): u for u in db.users.find({"_id": {"$in": user_ids}})}
    result = []
    for b in balances:
        sb = serialize_balance(b)
        u = users.get(sb["user"])
        if u:
            sb["userEmail"] = u.get("email")
            sb["userName"] = u.get("name")
            sb["userRole"] = u.get("role")
        result.append(sb)
    # 没有 balance 的用户也列出(显示余额 0)
    all_user_ids = set(str(u["_id"]) for u in db.users.find({}, {"_id": 1, "email": 1, "name": 1, "role": 1}))
    listed = set(b["user"] for b in result)
    for u in db.users.find({"_id": {"$in": [ObjectId(uid) for uid in all_user_ids - listed]}}):
        result.append({
            "_id": None, "user": str(u["_id"]),
            "userEmail": u.get("email"), "userName": u.get("name"), "userRole": u.get("role"),
            "tokenCredits": 0, "autoRefillEnabled": False,
            "refillIntervalValue": None, "refillIntervalUnit": None, "refillAmount": None, "lastRefill": None,
        })
    return {"balances": result}


@app.get("/api/balances/{user_id}")
def get_balance(
    user_id: str,
    refreshToken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """单用户 balance 详情 + 最近 50 条 transactions"""
    require_admin(refresh_token=refreshToken, bearer=_bearer_from_header(authorization))
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, "无效 user_id")
    u = db.users.find_one({"_id": oid}, {"password": 0})
    if not u:
        raise HTTPException(404, "用户不存在")
    bal = db.balances.find_one({"user": oid})
    txs = list(db.transactions.find({"user": oid}).sort("createdAt", -1).limit(50))
    return {
        "user": {
            "_id": str(u["_id"]),
            "email": u.get("email"),
            "name": u.get("name"),
            "role": u.get("role"),
        },
        "balance": serialize_balance(bal) if bal else None,
        "transactions": [
            {
                "_id": str(t.get("_id")),
                "tokenType": t.get("tokenType"),
                "tokenValue": t.get("tokenValue"),
                "rate": t.get("rate"),
                "model": t.get("model"),
                "rawAmount": t.get("rawAmount"),
                "context": t.get("context"),
                "createdAt": t.get("createdAt").isoformat() if t.get("createdAt") else None,
            }
            for t in txs
        ],
    }


class RefillBody(BaseModel):
    amount: int  # 正数充值,负数扣减


@app.post("/api/balances/{user_id}/refill")
def refill_balance(
    user_id: str,
    body: RefillBody,
    refreshToken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """手动充值(或扣减)"""
    require_admin(refresh_token=refreshToken, bearer=_bearer_from_header(authorization))
    if body.amount == 0:
        raise HTTPException(400, "amount 不能为 0")
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, "无效 user_id")
    u = db.users.find_one({"_id": oid})
    if not u:
        raise HTTPException(404, "用户不存在")
    # upsert balance
    db.balances.update_one(
        {"user": oid},
        {
            "$inc": {"tokenCredits": body.amount},
            "$setOnInsert": {"user": oid, "autoRefillEnabled": False},
            "$set": {"lastRefill": datetime.utcnow()} if body.amount > 0 else {},
        },
        upsert=True,
    )
    # 落 transaction
    db.transactions.insert_one({
        "user": oid,
        "tokenType": "credits",
        "tokenValue": body.amount,
        "context": f"admin manual refill (amount={body.amount})",
        "createdAt": datetime.utcnow(),
    })
    bal = db.balances.find_one({"user": oid})
    return {"ok": True, "balance": serialize_balance(bal)}


class AutoRefillBody(BaseModel):
    autoRefillEnabled: bool
    refillIntervalValue: Optional[int] = None
    refillIntervalUnit: Optional[str] = None  # days/hours/minutes
    refillAmount: Optional[int] = None


@app.patch("/api/balances/{user_id}/auto-refill")
def set_auto_refill(
    user_id: str,
    body: AutoRefillBody,
    refreshToken: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
):
    """改自动续费配置"""
    require_admin(refresh_token=refreshToken, bearer=_bearer_from_header(authorization))
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, "无效 user_id")
    update = {"autoRefillEnabled": body.autoRefillEnabled}
    if body.refillIntervalValue is not None:
        update["refillIntervalValue"] = body.refillIntervalValue
    if body.refillIntervalUnit:
        if body.refillIntervalUnit not in ("minutes", "hours", "days", "weeks", "months"):
            raise HTTPException(400, "refillIntervalUnit 必须是 minutes/hours/days/weeks/months")
        update["refillIntervalUnit"] = body.refillIntervalUnit
    if body.refillAmount is not None:
        update["refillAmount"] = body.refillAmount
    db.balances.update_one(
        {"user": oid},
        {"$set": update, "$setOnInsert": {"user": oid, "tokenCredits": 0}},
        upsert=True,
    )
    bal = db.balances.find_one({"user": oid})
    return {"ok": True, "balance": serialize_balance(bal)}


# ────── 组3 Tick2: codex 会话控制转发 (复用 JWT, 供 inject.js 同源调用) ──────
CODEX_PROXY_BASE = os.environ.get("CODEX_PROXY_BASE", "http://127.0.0.1:3084")


async def _safe_json(request: Request) -> dict:
    try:
        return await request.json() or {}
    except Exception:  # noqa: BLE001 - 空/非 json body 容忍
        return {}


def _proxy_request(method: str, path: str, user: dict, payload: Optional[dict] = None):
    """转发到 vega-codex-proxy, 带 LibreChat user header (proxy require_admin 认)。"""
    url = CODEX_PROXY_BASE + path
    data = _json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Content-Type": "application/json",
        "x-librechat-user-role": user.get("role") or "USER",
        "x-librechat-user-id": str(user.get("_id")),
        "x-librechat-user-email": user.get("email") or "",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, _json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return exc.code, {"ok": False, "error": f"proxy {exc.code}"}
    except Exception as exc:  # noqa: BLE001 - proxy 不可达
        return 502, {"ok": False, "error": f"proxy unreachable: {exc}"}


async def _forward(method, path, user, payload=None):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _proxy_request, method, path, user, payload)


@app.get("/api/codex/info")
async def codex_info(refreshToken: Optional[str] = Cookie(None), threadId: Optional[str] = None):
    user = get_current_user(refreshToken)
    path = "/codex/session/info" + (f"?threadId={threadId}" if threadId else "")
    status, body = await _forward("GET", path, user)
    return JSONResponse(body, status_code=status)


@app.post("/api/codex/close")
async def codex_close(request: Request, refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    payload = await _safe_json(request)
    status, body = await _forward("POST", "/codex/session/close", user, payload)
    return JSONResponse(body, status_code=status)


@app.post("/api/codex/interrupt")
async def codex_interrupt(request: Request, refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    payload = await _safe_json(request)
    status, body = await _forward("POST", "/codex/session/interrupt", user, payload)
    return JSONResponse(body, status_code=status)


@app.get("/api/codex/list")
async def codex_list(refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    status, body = await _forward("GET", "/codex/session/list", user)
    return JSONResponse(body, status_code=status)


@app.post("/api/codex/rename")
async def codex_rename(request: Request, refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    payload = await _safe_json(request)
    status, body = await _forward("POST", "/codex/session/rename", user, payload)
    return JSONResponse(body, status_code=status)


# ── 组onboard TickA: codex 智能接入 (探测 / 登录态 / 配置) admin 守门转发 ──
@app.get("/api/codex/onboard/detect")
async def codex_onboard_detect(
    refreshToken: Optional[str] = Cookie(None), path: Optional[str] = None
):
    user = get_current_user(refreshToken)
    p = "/codex/onboard/detect"
    if path:
        p += "?path=" + urllib.parse.quote(path, safe="")
    status, body = await _forward("GET", p, user)
    return JSONResponse(body, status_code=status)


@app.get("/api/codex/onboard/status")
async def codex_onboard_status(refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    status, body = await _forward("GET", "/codex/onboard/status", user)
    return JSONResponse(body, status_code=status)


@app.post("/api/codex/onboard/config")
async def codex_onboard_config(request: Request, refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    payload = await _safe_json(request)
    status, body = await _forward("POST", "/codex/onboard/config", user, payload)
    return JSONResponse(body, status_code=status)


@app.post("/api/codex/onboard/login/start")
async def codex_onboard_login_start(refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    status, body = await _forward("POST", "/codex/onboard/login/start", user, {})
    return JSONResponse(body, status_code=status)


@app.get("/api/codex/onboard/login/poll")
async def codex_onboard_login_poll(refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    status, body = await _forward("GET", "/codex/onboard/login/poll", user)
    return JSONResponse(body, status_code=status)


@app.post("/api/codex/onboard/login/cancel")
async def codex_onboard_login_cancel(refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    status, body = await _forward("POST", "/codex/onboard/login/cancel", user, {})
    return JSONResponse(body, status_code=status)


def _proxy_raw(path: str, user: dict):
    """转发到 proxy 并原样返回字节 + content-type (供 QR SVG 等非 json 响应)。"""
    url = CODEX_PROXY_BASE + path
    headers = {
        "x-librechat-user-role": user.get("role") or "USER",
        "x-librechat-user-id": str(user.get("_id")),
        "x-librechat-user-email": user.get("email") or "",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read(), resp.headers.get("content-type", "application/octet-stream")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("content-type", "application/json")
    except Exception as exc:  # noqa: BLE001 - proxy 不可达
        return 502, f'{{"ok":false,"error":"proxy unreachable: {exc}"}}'.encode(), "application/json"


@app.get("/api/codex/onboard/qr")
async def codex_onboard_qr(refreshToken: Optional[str] = Cookie(None), data: str = ""):
    user = get_current_user(refreshToken)
    path = "/codex/onboard/qr?data=" + urllib.parse.quote(data, safe="")
    loop = asyncio.get_event_loop()
    status, content, ctype = await loop.run_in_executor(None, _proxy_raw, path, user)
    return Response(content=content, media_type=ctype, status_code=status)


@app.get("/api/codex/onboard/diagnose")
async def codex_onboard_diagnose(refreshToken: Optional[str] = Cookie(None), force: bool = False):
    user = get_current_user(refreshToken)
    path = "/codex/onboard/diagnose" + ("?force=true" if force else "")
    status, body = await _forward("GET", path, user)
    return JSONResponse(body, status_code=status)


@app.post("/api/codex/onboard/test")
async def codex_onboard_test(refreshToken: Optional[str] = Cookie(None)):
    user = get_current_user(refreshToken)
    status, body = await _forward("POST", "/codex/onboard/test", user, {})
    return JSONResponse(body, status_code=status)


# ────── 静态资源 ──────
# /vega-admin/        → index.html
# /vega-admin/inject.js → 浮动按钮注入脚本（被 LibreChat 主站加载）
# /vega-admin/static/* → CSS / JS

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR / "static") if (STATIC_DIR / "static").exists() else str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    f = STATIC_DIR / "index.html"
    if not f.exists():
        return JSONResponse({"err": "static/index.html 不存在"}, status_code=500)
    return FileResponse(str(f), media_type="text/html")


@app.get("/inject.js")
def inject_js():
    f = STATIC_DIR / "inject.js"
    if not f.exists():
        return JSONResponse({"err": "inject.js 不存在"}, status_code=500)
    return FileResponse(str(f), media_type="application/javascript")
