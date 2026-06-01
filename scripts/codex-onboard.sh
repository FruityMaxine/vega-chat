#!/usr/bin/env bash
#
# Vega Chat — codex 快速自动接入
#
# 探测 codex CLI → auth 校验 → 确保 librechat.yaml 注册了 Codex endpoint
# (幂等: 已注册则跳过) → 重启 proxy 验证 app-server 握手。
#
# 用法: ./scripts/codex-onboard.sh [--check]
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
YAML="$PROJECT_ROOT/configs/librechat.yaml"
CODEX_BIN="${CODEX_BIN:-/root/.local/bin/codex}"
CODEX_HOME="${CODEX_HOME:-/root/.codex}"

if [[ -t 1 ]]; then C_G=$'\033[0;32m';C_Y=$'\033[0;33m';C_R=$'\033[0;31m';C_B=$'\033[0;36m';C_0=$'\033[0m'; else C_G=""; C_Y=""; C_R=""; C_B=""; C_0=""; fi
log(){ echo "${C_B}▸${C_0} $*"; }; ok(){ echo "${C_G}✓${C_0} $*"; }; warn(){ echo "${C_Y}!${C_0} $*"; }; die(){ echo "${C_R}✗ $*${C_0}" >&2; exit 1; }
CHECK_ONLY=0; [[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

# ────── 1. codex CLI 探测 ──────
log "探测 codex CLI..."
CODEX="$(command -v codex || true)"; [[ -z "$CODEX" && -x "$CODEX_BIN" ]] && CODEX="$CODEX_BIN"
if [[ -z "$CODEX" ]]; then
  warn "未找到 codex CLI。安装: npm i -g @openai/codex   (或 Termux: @mmmbuto/codex-cli-termux)"
  die "装好 codex 后重跑本脚本"
fi
ok "codex: $CODEX ($("$CODEX" --version 2>/dev/null || echo '版本未知'))"

# ────── 2. app-server 子命令 + auth 状态 ──────
if "$CODEX" app-server --help >/dev/null 2>&1; then
  ok "codex app-server 子命令可用 (proxy 用它长驻 JSON-RPC)"
else
  warn "codex 不支持 app-server 子命令 — proxy 会回退 exec 模式"
fi
if [[ -f "$CODEX_HOME/auth.json" ]]; then
  ok "codex 已登录 (auth.json 存在于 $CODEX_HOME)"
else
  warn "codex 未登录 → 跑 '$CODEX login' (ChatGPT Plus OAuth) 后再用"
fi

# ────── 3. librechat.yaml 注册 Codex endpoint (幂等) ──────
[[ -f "$YAML" ]] || die "缺 $YAML"
if grep -qE "name:\s*['\"]?Codex['\"]?" "$YAML" || grep -qE "name:\s*['\"]?codex-cli['\"]?" "$YAML"; then
  ok "librechat.yaml 已注册 Codex endpoint (跳过, 幂等)"
else
  if [[ "$CHECK_ONLY" == 1 ]]; then
    warn "librechat.yaml 未注册 Codex → 完整运行时会自动补 custom endpoint"
  else
    log "librechat.yaml 未注册 Codex → 追加 custom endpoint..."
    cat >> "$YAML" <<'YEOF'

# Codex endpoint (vega-codex-proxy 包 codex app-server 为 OpenAI 兼容 API)
# 由 codex-onboard.sh 自动追加
    - name: 'Codex'
      apiKey: 'no-key-needed'
      baseURL: 'http://host.docker.internal:3084/v1'
      models:
        default: ['codex-cli']
        fetch: false
      titleConvo: false
      modelDisplayLabel: 'Codex'
YEOF
    ok "已追加 Codex custom endpoint (host.docker.internal:3084 → vega-codex-proxy)"
    warn "记得 docker compose restart api 让 LibreChat 重读 librechat.yaml"
  fi
fi

if [[ "$CHECK_ONLY" == 1 ]]; then ok "--check 通过 (未改动)"; exit 0; fi

# ────── 4. 重启 proxy + 握手验证 ──────
if systemctl list-unit-files vega-codex-proxy.service >/dev/null 2>&1; then
  log "重启 vega-codex-proxy 并验证 app-server 握手..."
  systemctl restart vega-codex-proxy; sleep 3
  if curl -fsS -m 15 http://127.0.0.1:3084/codex/appserver/ping | grep -q '"initialized": *true\|"initialized":true'; then
    ok "codex app-server 握手成功 — 接入完成！在 LibreChat 选 Codex endpoint 即可对话"
  else
    warn "握手未确认 — 看 journalctl -u vega-codex-proxy -n 50"
  fi
else
  warn "vega-codex-proxy 服务未装 → 先跑 ./scripts/setup.sh"
fi
