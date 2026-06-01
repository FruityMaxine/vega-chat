#!/usr/bin/env bash
#
# Vega Chat — 拆除 (停服务, 默认保留数据)
#
# 用法:
#   ./scripts/teardown.sh              停 docker 全栈 + systemd 服务, 保留 data
#   ./scripts/teardown.sh --purge      额外删 docker volumes (⚠ 抹掉所有对话/用户数据)
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE="$PROJECT_ROOT/docker/docker-compose.yml"
if [[ -t 1 ]]; then C_G=$'\033[0;32m';C_Y=$'\033[0;33m';C_B=$'\033[0;36m';C_0=$'\033[0m'; else C_G=""; C_Y=""; C_B=""; C_0=""; fi
log(){ echo "${C_B}▸${C_0} $*"; }; ok(){ echo "${C_G}✓${C_0} $*"; }; warn(){ echo "${C_Y}!${C_0} $*"; }

PURGE=0; [[ "${1:-}" == "--purge" ]] && PURGE=1

log "停 systemd 服务..."
systemctl stop vega-codex-proxy vega-chat-admin 2>/dev/null || true
systemctl disable vega-codex-proxy vega-chat-admin 2>/dev/null || true
ok "vega-codex-proxy / vega-chat-admin 已停"

if [[ "$PURGE" == 1 ]]; then
  warn "--purge: 删 docker 全栈 + volumes (数据将丢失)"
  docker compose -f "$COMPOSE" --project-directory "$PROJECT_ROOT/docker" down -v
  ok "全栈 + volumes 已删"
else
  log "停 docker 全栈 (保留 volumes/data)..."
  docker compose -f "$COMPOSE" --project-directory "$PROJECT_ROOT/docker" down
  ok "docker 全栈已停, 数据保留 (重新部署: ./scripts/setup.sh)"
fi
