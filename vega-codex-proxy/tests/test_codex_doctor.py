"""CodexDoctor 单测 — 逐项诊断 + TTL 缓存 (mock detect/login, 不依赖真 codex)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codex_onboard  # noqa: E402


def _reset_cache():
    codex_onboard.CodexDoctor._cache = None
    codex_onboard.CodexDoctor._cache_at = 0.0


def _patch(monkeypatch, det, login):
    monkeypatch.setattr(codex_onboard, "detect_codex_bin", lambda custom=None: det)
    monkeypatch.setattr(codex_onboard, "check_login_status", lambda b=None, timeout=10.0: login)


def test_diagnose_all_ok(monkeypatch):
    _reset_cache()
    _patch(monkeypatch,
           {"found": True, "path": "/x/codex", "version": "codex-cli 0.130.0"},
           {"logged_in": True, "method": "ChatGPT", "auth_exists": True, "auth_file": "/a"})
    r = codex_onboard.CodexDoctor.diagnose({"initialized": True, "codex_version": "x"}, force=True)
    assert r.overall == "ok"
    assert [i.key for i in r.items] == ["binary", "login", "auth", "appserver"]
    assert all(i.status == "ok" for i in r.items)


def test_diagnose_no_binary_fails_fast(monkeypatch):
    _reset_cache()
    _patch(monkeypatch, {"found": False, "path": None, "version": None}, {})
    r = codex_onboard.CodexDoctor.diagnose({"initialized": True}, force=True)
    assert r.overall == "fail"
    assert len(r.items) == 1
    assert r.items[0].key == "binary" and r.items[0].status == "fail" and r.items[0].fix


def test_diagnose_not_logged_in(monkeypatch):
    _reset_cache()
    _patch(monkeypatch,
           {"found": True, "path": "/x", "version": "v"},
           {"logged_in": False, "method": None, "auth_exists": False, "detail": "Not logged in"})
    r = codex_onboard.CodexDoctor.diagnose({"initialized": True}, force=True)
    assert r.overall == "fail"
    login = [i for i in r.items if i.key == "login"][0]
    assert login.status == "fail" and login.fix
    auth = [i for i in r.items if i.key == "auth"][0]
    assert auth.status == "warn"


def test_diagnose_appserver_uninit_warn(monkeypatch):
    _reset_cache()
    _patch(monkeypatch,
           {"found": True, "path": "/x", "version": "v"},
           {"logged_in": True, "method": "ChatGPT", "auth_exists": True})
    r = codex_onboard.CodexDoctor.diagnose({"initialized": False}, force=True)
    assert r.overall == "warn"
    aps = [i for i in r.items if i.key == "appserver"][0]
    assert aps.status == "warn"


def test_diagnose_no_appserver_injected(monkeypatch):
    """不注入 app_server 状态 → 不出 appserver 项 (3 项)。"""
    _reset_cache()
    _patch(monkeypatch,
           {"found": True, "path": "/x", "version": "v"},
           {"logged_in": True, "method": "x", "auth_exists": True})
    r = codex_onboard.CodexDoctor.diagnose(None, force=True)
    assert [i.key for i in r.items] == ["binary", "login", "auth"]


def test_diagnose_ttl_cache(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def det(custom=None):
        calls["n"] += 1
        return {"found": True, "path": "/x", "version": "v"}

    monkeypatch.setattr(codex_onboard, "detect_codex_bin", det)
    monkeypatch.setattr(codex_onboard, "check_login_status",
                        lambda b=None, timeout=10.0: {"logged_in": True, "method": "x", "auth_exists": True})
    r1 = codex_onboard.CodexDoctor.diagnose({"initialized": True})
    r2 = codex_onboard.CodexDoctor.diagnose({"initialized": True})  # TTL 内命中缓存
    assert r1 is r2
    assert calls["n"] == 1
    codex_onboard.CodexDoctor.diagnose({"initialized": True}, force=True)  # 强制重跑
    assert calls["n"] == 2


def test_cached_overall(monkeypatch):
    _reset_cache()
    assert codex_onboard.CodexDoctor.cached_overall() == "unknown"
    _patch(monkeypatch,
           {"found": True, "path": "/x", "version": "v"},
           {"logged_in": True, "method": "x", "auth_exists": True})
    codex_onboard.CodexDoctor.diagnose({"initialized": True}, force=True)
    assert codex_onboard.CodexDoctor.cached_overall() == "ok"


def test_report_to_dict(monkeypatch):
    _reset_cache()
    _patch(monkeypatch,
           {"found": True, "path": "/x", "version": "v"},
           {"logged_in": True, "method": "x", "auth_exists": True})
    r = codex_onboard.CodexDoctor.diagnose({"initialized": True}, force=True)
    d = r.to_dict()
    assert d["overall"] == "ok"
    assert isinstance(d["items"], list) and "checked_at" in d
    assert d["items"][0]["key"] == "binary" and "status" in d["items"][0]
