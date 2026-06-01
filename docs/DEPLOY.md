# Vega Chat — 部署指南

一键把整套 Vega Chat（LibreChat 内核 + codex 集成 + 自建 admin）部署到一台 Linux 服务器。

## 前置

- Linux（Ubuntu 22.04+ 实测）, root 或 sudo
- **Docker** + Docker Compose v2 插件
- **Python 3.10+**（含 `python3-venv`）
- **codex CLI**（接 Codex 功能需要；没有也能先起聊天，之后再接）
- 反向代理 **Caddy**（可选，生产建议；TLS + 子路径分发）

## 一键部署

```bash
git clone https://github.com/FruityMaxine/vega-chat.git
cd vega-chat

# 1. 配置密钥
cp docker/.env.example docker/.env
$EDITOR docker/.env          # 填 MONGO/JWT/APP_TITLE/各 provider key

# 2. 先体检 (不动系统)
./scripts/setup.sh --check

# 3. 完整部署 (docker 全栈 + venv + systemd 服务 + 健康校验)
./scripts/setup.sh

# 4. 接入 codex (幂等, 已接则跳过)
./scripts/codex-onboard.sh
```

`setup.sh` 会：依赖检查 → `docker compose up -d` 全栈 → 建 Python venv 装依赖 →
生成并启动 `vega-codex-proxy` / `vega-chat-admin` systemd 服务 → 打印 Caddy 反代片段 → 健康校验。
**幂等**：可反复跑，不会重复破坏。

## 组件与端口

| 组件 | 端口 | 说明 |
|---|---|---|
| LibreChat (api) | 3080 | 聊天主站 (docker) |
| vega-codex-proxy | 3084 | codex app-server → OpenAI 兼容 API (systemd) |
| vega-chat-admin | 3082 | 自建 admin 后端 + inject.js (systemd) |
| admin-panel | 3083 | 独立管理 GUI (**可选**, 需 `librechat-admin-panel` fork, 见 THIRD_PARTY.md) |
| MongoDB | 27017 | LibreChat 数据 |

> **可选 admin-panel**：默认 `docker compose up` 不启它（`profiles: [admin]`）。Vega Chat 的统一 hub
> （inject.js + vega-admin）已内置 codex 会话 / 系统状态管理，独立面板纯属增强。
> 需要时 `docker compose --profile admin up -d` 并自行 build 该 fork。

> **注**：`vega-codex-proxy` 监听 `0.0.0.0:3084` 是因为 LibreChat 容器需经 docker bridge
> 网关访问宿主；UFW 须只放行 docker 子网（如 `172.23.0.0/16`），勿对公网开 3084。

## 验证

```bash
curl http://127.0.0.1:3084/healthz                 # {"ok":true,"version":...}
curl http://127.0.0.1:3084/codex/appserver/ping    # {"initialized":true}
# 浏览器开 chat.<域名> 登录, 选 Codex endpoint 对话
# codex 状态仪表盘: http://127.0.0.1:3084/codex/status
```

## 故障排查

| 现象 | 排查 |
|---|---|
| proxy 起不来 | `journalctl -u vega-codex-proxy -n 50`；确认 venv + codex_bin 路径 |
| LibreChat 连不上 codex | 确认 `librechat.yaml` 有 Codex endpoint（跑 `codex-onboard.sh`）+ 容器能解析 `host.docker.internal` |
| codex 握手失败 | `codex login`（ChatGPT Plus OAuth）；`codex app-server --help` 看子命令在不在 |
| 改了 librechat.yaml 不生效 | `docker compose -f docker/docker-compose.yml restart api` |
| 前端注入(折叠/chip/齿轮)不显 | 确认 Caddy `/vega-admin/*` 路由到 3082；浏览器硬刷新 |

## 拆除

```bash
./scripts/teardown.sh           # 停服务, 保留数据
./scripts/teardown.sh --purge   # ⚠ 连数据一起删
```
