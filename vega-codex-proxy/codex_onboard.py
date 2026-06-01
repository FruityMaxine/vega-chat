"""codex_onboard — codex 环境探测 / 登录态检测 / 配置落盘 (智能接入向导后端 · 组onboard TickA)。

目标: 新服务器装了 codex 后, 部署本项目即可"零命令行"识别 codex 安装路径与登录状态。
- detect_codex_bin: 多候选路径探测 (自定义 > 落盘配置 > env > which > 常见安装位) + `--version` 校验
- check_login_status: 跑 `codex login status` 解析 + ~/.codex/auth.json 检测
- persist_config / load_config: 自定义 CODEX_BIN 落盘, 重启存活

纯标准库, 无项目内依赖 (可独立单测)。所有子进程调用带超时, 失败优雅返回结构化结果不抛。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# 配置落盘位置 (自定义 codex 路径等), 默认模块同级, 可 env 覆盖
CONFIG_PATH = os.environ.get(
    "CODEX_ONBOARD_CONFIG",
    str(Path(__file__).resolve().parent / "codex_onboard_config.json"),
)
# codex 登录凭据落盘位置 (codex CLI 固定写这里)
AUTH_FILE = os.path.expanduser("~/.codex/auth.json")

# 常见 codex 安装位 (按探测优先级靠后, 前面让位给 which / 配置 / env)
_COMMON_PATHS = [
    "~/.local/bin/codex",
    "/usr/local/bin/codex",
    "/usr/bin/codex",
    "~/.codex/packages/standalone/current/codex",
]


def load_config() -> dict:
    """读落盘配置 (含自定义 codex_bin)。文件不存在 / 损坏 → 空 dict。"""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _candidate_paths(custom: str | None = None) -> list[str]:
    """按优先级生成 codex 二进制候选路径, 去重保序。"""
    cands: list[str] = []
    if custom:
        cands.append(custom)
    cfg_bin = load_config().get("codex_bin")
    if cfg_bin:
        cands.append(cfg_bin)
    env_bin = os.environ.get("CODEX_BIN")
    if env_bin:
        cands.append(env_bin)
    which_bin = shutil.which("codex")
    if which_bin:
        cands.append(which_bin)
    cands += [os.path.expanduser(p) for p in _COMMON_PATHS]
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
        else:
            continue
        out.append(c)
    return out


def _probe_version(path: str, timeout: float = 8.0) -> str | None:
    """跑 `<path> --version`, 成功返回版本串, 失败返回 None (不抛)。"""
    try:
        r = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0:
            return (r.stdout or r.stderr).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def detect_codex_bin(custom: str | None = None) -> dict:
    """探测 codex 二进制。返回 {found, path, version, candidates:[{path,exists,version}]}。

    选中规则: 第一个"存在 + 可执行 + --version 跑通"的候选。
    """
    candidates: list[dict] = []
    chosen: dict | None = None
    for path in _candidate_paths(custom):
        exists = os.path.isfile(path) and os.access(path, os.X_OK)
        version = _probe_version(path) if exists else None
        entry = {"path": path, "exists": exists, "version": version}
        candidates.append(entry)
        if chosen is None and exists and version:
            chosen = entry
    return {
        "found": chosen is not None,
        "path": chosen["path"] if chosen else None,
        "version": chosen["version"] if chosen else None,
        "candidates": candidates,
    }


def check_login_status(codex_bin: str | None = None, timeout: float = 10.0) -> dict:
    """检测 codex 登录态。跑 `codex login status` 解析 + auth.json 检测。

    返回 {logged_in, method, reachable, auth_file, auth_exists, detail}。
    """
    bin_path = codex_bin or detect_codex_bin().get("path")
    auth_exists = os.path.isfile(AUTH_FILE)
    base = {"auth_file": AUTH_FILE, "auth_exists": auth_exists}
    if not bin_path:
        return {**base, "logged_in": False, "method": None, "reachable": False,
                "detail": "未找到 codex 二进制"}
    try:
        r = subprocess.run(
            [bin_path, "login", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return {**base, "logged_in": False, "method": None, "reachable": False,
                "detail": f"探测失败: {exc}"}
    low = out.lower()
    logged_in = "logged in" in low and "not logged in" not in low
    method = None
    if logged_in:
        if "chatgpt" in low:
            method = "ChatGPT"
        elif "api key" in low or "apikey" in low:
            method = "API Key"
        else:
            method = "unknown"
    return {**base, "logged_in": logged_in, "method": method, "reachable": True,
            "detail": out[:200]}


def persist_config(codex_bin: str) -> dict:
    """落盘自定义 codex 路径 (校验真能跑 --version 才写)。返回 {ok, ...}。"""
    expanded = os.path.expanduser(codex_bin or "")
    if not (os.path.isfile(expanded) and os.access(expanded, os.X_OK)):
        return {"ok": False, "error": "路径不存在或不可执行"}
    version = _probe_version(expanded)
    if not version:
        return {"ok": False, "error": "该路径无法运行 codex --version"}
    cfg = load_config()
    cfg["codex_bin"] = expanded
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        return {"ok": False, "error": f"落盘失败: {exc}"}
    return {"ok": True, "codex_bin": expanded, "version": version,
            "config_path": CONFIG_PATH}
