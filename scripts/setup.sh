#!/usr/bin/env bash
#
# Vega Chat — 一键部署脚本
#
# 从零把整套起起来: docker 全栈(LibreChat+Mongo+Meili+vectordb+rag) + Python venv
# + vega-codex-proxy / vega-admin systemd 服务 + 健康校验。幂等可重入。
#
# 用法:
#   ./scripts/setup.sh            完整部署
#   ./scripts/setup.sh --check    只检查依赖/配置 (dry-run, 不动系统)
#   ./scripts/setup.sh --help
#
set -euo pipefail

# ────── 路径 (从脚本位置推导, 可移植) ──────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$PROJECT_ROOT/.venv-admin"
COMPOSE="$PROJECT_ROOT/docker/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/docker/.env"

# ────── 彩色日志 ──────
if [[ -t 1 ]]; then
  C_G=$'\033[0;32m'; C_Y=$'\033[0;33m'; C_R=$'\033[0;31m'; C_B=$'\033[0;36m'; C_0=$'\033[0m'
else
  C_G=""; C_Y=""; C_R=""; C_B=""; C_0=""
fi
log()  { echo "${C_B}▸${C_0} $*"; }
ok()   { echo "${C_G}✓${C_0} $*"; }
warn() { echo "${C_Y}!${C_0} $*"; }
die()  { echo "${C_R}✗ $*${C_0}" >&2; exit 1; }

CHECK_ONLY=0
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  --help|-h)
    sed -n '3,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  "") ;;
  *) die "未知参数: $1 (用 --help)" ;;
esac

# ────── 1. 依赖检查 ──────
log "检查依赖..."
need() { command -v "$1" >/dev/null 2>&1 || die "缺依赖: $1 ($2)"; }
need docker "https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "缺 docker compose v2 插件"
need python3 "apt install python3 python3-venv"
ok "docker / docker compose / python3 就绪"
if command -v codex >/dev/null 2>&1 || [[ -x /root/.local/bin/codex ]]; then
  ok "codex CLI 就绪 ($(command -v codex || echo /root/.local/bin/codex))"
else
  warn "codex CLI 未找到 → 部署后跑 ./scripts/codex-onboard.sh 接入"
fi

# ────── 2. 校验配置文件 ──────
[[ -f "$COMPOSE" ]] || die "缺 docker-compose: $COMPOSE"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$PROJECT_ROOT/docker/.env.example" ]]; then
    die "缺 docker/.env — 复制 docker/.env.example 为 docker/.env 并填好密钥后重跑"
  fi
  die "缺 docker/.env (含 MONGO/JWT/APP_TITLE 等)"
fi
docker compose -f "$COMPOSE" --project-directory "$PROJECT_ROOT/docker" config -q \
  && ok "docker-compose 配置语法正确" || die "docker-compose 配置有误"

if [[ "$CHECK_ONLY" == 1 ]]; then
  ok "--check 通过: 依赖与配置就绪 (未对系统做任何修改)"
  exit 0
fi

# ────── 3. Python venv + 依赖 ──────
if [[ ! -x "$VENV/bin/python" ]]; then
  log "创建 Python venv: $VENV"
  python3 -m venv "$VENV"
fi
log "安装 Python 依赖..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
ok "Python 依赖就绪"

# ────── 4. docker 全栈 ──────
log "拉起 docker 全栈 (LibreChat + Mongo + Meili + vectordb + rag)..."
docker compose -f "$COMPOSE" --project-directory "$PROJECT_ROOT/docker" up -d
ok "docker 全栈已起"

# ────── 5. 生成 + 装 systemd 服务 ──────
install_unit() {
  local name="$1" desc="$2" wd="$3" exec="$4"; shift 4
  local extra_env=("$@")
  local unit="/etc/systemd/system/${name}.service"
  log "写 systemd unit: $unit"
  {
    echo "[Unit]"
    echo "Description=$desc"
    echo "After=network.target docker.service"
    echo "Wants=docker.service"
    echo ""
    echo "[Service]"
    echo "Type=simple"
    echo "WorkingDirectory=$wd"
    for e in "${extra_env[@]}"; do echo "Environment=$e"; done
    echo "ExecStart=$exec"
    echo "Restart=always"
    echo "RestartSec=5"
    echo "StandardOutput=journal"
    echo "StandardError=journal"
    echo ""
    echo "[Install]"
    echo "WantedBy=multi-user.target"
  } > "$unit"
}

install_unit "vega-codex-proxy" \
  "Vega Codex Proxy - codex app-server 包成 OpenAI 兼容 API" \
  "$PROJECT_ROOT/vega-codex-proxy" \
  "$VENV/bin/uvicorn app:app --host 0.0.0.0 --port 3084 --log-level info" \
  "HOME=/root" "CODEX_HOME=/root/.codex" "CODEX_BIN=/root/.local/bin/codex" \
  "CODEX_WORK_DIR=$PROJECT_ROOT" "CODEX_SANDBOX=danger-full-access"

install_unit "vega-chat-admin" \
  "Vega Chat 自建 Admin 后端 (FastAPI)" \
  "$PROJECT_ROOT/vega-admin" \
  "$VENV/bin/uvicorn backend.app:app --host 0.0.0.0 --port 3082 --log-level info" \
  "MONGO_URI=mongodb://127.0.0.1:27017/LibreChat" "ENV_FILE=$ENV_FILE"

systemctl daemon-reload
systemctl enable --now vega-codex-proxy vega-chat-admin
ok "systemd 服务已装并启动"

# ────── 6. Caddy 反代片段提示 ──────
cat <<EOF

${C_Y}═══ 把以下片段加进 /etc/caddy/Caddyfile, 然后 'caddy reload' ═══${C_0}
chat.<你的域名> {
    encode gzip zstd
    handle_path /vega-admin/* { reverse_proxy 127.0.0.1:3082 }
    @admin path /admin-panel /admin-panel/*
    handle @admin { reverse_proxy 127.0.0.1:3083 }
    reverse_proxy 127.0.0.1:3080 { flush_interval -1 }
}
EOF

# ────── 7. 健康校验 ──────
log "健康校验..."
sleep 4
hc() { curl -fsS -o /dev/null -m 8 "$1" && ok "$2 OK" || warn "$2 未通 ($1)"; }
hc "http://127.0.0.1:3084/healthz" "vega-codex-proxy :3084"
hc "http://127.0.0.1:3080/" "LibreChat :3080"
hc "http://127.0.0.1:3082/healthz" "vega-chat-admin :3082"

echo ""
ok "部署完成。接 codex: ./scripts/codex-onboard.sh   |   状态页: http://127.0.0.1:3084/codex/status"
