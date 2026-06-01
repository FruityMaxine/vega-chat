"""codex_onboard 单测 — 探测/登录态/配置落盘的纯逻辑 (mock 子进程, 不依赖真 codex)。"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codex_onboard  # noqa: E402


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── 配置落盘 ──
def test_load_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_onboard, "CONFIG_PATH", str(tmp_path / "nope.json"))
    assert codex_onboard.load_config() == {}


def test_persist_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_onboard, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\necho codex-cli 9.9.9\n")
    fake.chmod(0o755)
    monkeypatch.setattr(codex_onboard, "_probe_version", lambda p, timeout=8.0: "codex-cli 9.9.9")
    r = codex_onboard.persist_config(str(fake))
    assert r["ok"] is True
    assert r["codex_bin"] == str(fake)
    assert codex_onboard.load_config()["codex_bin"] == str(fake)


def test_persist_config_invalid_path(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_onboard, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    r = codex_onboard.persist_config("/no/such/codex")
    assert r["ok"] is False
    assert "error" in r


def test_persist_config_unrunnable(tmp_path, monkeypatch):
    """路径存在可执行但 --version 跑不通 → 拒绝落盘。"""
    monkeypatch.setattr(codex_onboard, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    fake = tmp_path / "codex"
    fake.write_text("x"); fake.chmod(0o755)
    monkeypatch.setattr(codex_onboard, "_probe_version", lambda p, timeout=8.0: None)
    r = codex_onboard.persist_config(str(fake))
    assert r["ok"] is False


# ── 路径探测 ──
def test_candidate_paths_dedup(monkeypatch):
    monkeypatch.setattr(codex_onboard, "load_config", lambda: {"codex_bin": "/dup/codex"})
    monkeypatch.setenv("CODEX_BIN", "/dup/codex")
    paths = codex_onboard._candidate_paths(custom="/dup/codex")
    assert paths.count("/dup/codex") == 1


def test_detect_found(tmp_path, monkeypatch):
    fake = tmp_path / "codex"
    fake.write_text("x"); fake.chmod(0o755)
    monkeypatch.setattr(codex_onboard, "_candidate_paths", lambda custom=None: [str(fake)])
    monkeypatch.setattr(codex_onboard, "_probe_version", lambda p, timeout=8.0: "codex-cli 0.130.0")
    r = codex_onboard.detect_codex_bin()
    assert r["found"] is True
    assert r["path"] == str(fake)
    assert r["version"] == "codex-cli 0.130.0"
    assert len(r["candidates"]) == 1


def test_detect_not_found(monkeypatch):
    monkeypatch.setattr(codex_onboard, "_candidate_paths", lambda custom=None: ["/no/codex"])
    r = codex_onboard.detect_codex_bin()
    assert r["found"] is False
    assert r["path"] is None
    assert r["candidates"][0]["exists"] is False


def test_detect_picks_first_working(tmp_path, monkeypatch):
    """多候选时选第一个 存在+版本跑通 的。"""
    bad = tmp_path / "bad"  # 不存在
    good = tmp_path / "good"
    good.write_text("x"); good.chmod(0o755)
    monkeypatch.setattr(codex_onboard, "_candidate_paths",
                        lambda custom=None: [str(bad), str(good)])
    monkeypatch.setattr(codex_onboard, "_probe_version",
                        lambda p, timeout=8.0: "codex-cli 0.130.0" if p == str(good) else None)
    r = codex_onboard.detect_codex_bin()
    assert r["found"] is True
    assert r["path"] == str(good)


# ── 登录态 ──
def test_login_status_logged_in(monkeypatch):
    monkeypatch.setattr(codex_onboard, "detect_codex_bin", lambda custom=None: {"path": "/x/codex"})
    monkeypatch.setattr(codex_onboard.subprocess, "run",
                        lambda *a, **k: _FakeProc(0, "Logged in using ChatGPT\n"))
    r = codex_onboard.check_login_status()
    assert r["logged_in"] is True
    assert r["method"] == "ChatGPT"
    assert r["reachable"] is True


def test_login_status_api_key(monkeypatch):
    monkeypatch.setattr(codex_onboard, "detect_codex_bin", lambda custom=None: {"path": "/x/codex"})
    monkeypatch.setattr(codex_onboard.subprocess, "run",
                        lambda *a, **k: _FakeProc(0, "Logged in using API key\n"))
    r = codex_onboard.check_login_status()
    assert r["logged_in"] is True
    assert r["method"] == "API Key"


def test_login_status_not_logged_in(monkeypatch):
    monkeypatch.setattr(codex_onboard, "detect_codex_bin", lambda custom=None: {"path": "/x/codex"})
    monkeypatch.setattr(codex_onboard.subprocess, "run",
                        lambda *a, **k: _FakeProc(0, "Not logged in\n"))
    r = codex_onboard.check_login_status()
    assert r["logged_in"] is False
    assert r["method"] is None


def test_login_status_no_bin(monkeypatch):
    monkeypatch.setattr(codex_onboard, "detect_codex_bin", lambda custom=None: {"path": None})
    r = codex_onboard.check_login_status()
    assert r["logged_in"] is False
    assert r["reachable"] is False


def test_login_status_subprocess_error(monkeypatch):
    monkeypatch.setattr(codex_onboard, "detect_codex_bin", lambda custom=None: {"path": "/x/codex"})

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=10)

    monkeypatch.setattr(codex_onboard.subprocess, "run", _boom)
    r = codex_onboard.check_login_status()
    assert r["reachable"] is False
    assert "探测失败" in r["detail"]
