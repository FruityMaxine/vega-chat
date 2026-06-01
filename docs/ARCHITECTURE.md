# Vega Chat 架构

## 组件

| 组件 | 技术 | 端口 | 角色 |
|---|---|---|---|
| LibreChat 内核 | React + Node (预构建镜像) | 3080 | 聊天主站、对话/用户/模型管理 |
| vega-codex-proxy | Python / FastAPI | 3084 | codex app-server → OpenAI 兼容 API；会话控制 |
| vega-admin | Python / FastAPI | 3082 | 服务 inject.js + 管理 API 转发 |
| MongoDB | mongo | 27017 | LibreChat 数据 |
| Meilisearch | meili | 7700 | 全文检索 |
| vectordb / rag | pgvector / rag-api | — | RAG (可选) |
| Caddy | — | 80/443 | 反代 + TLS + 子路径分发 |

## 核心数据流：一次 codex 对话

```
用户在聊天框发消息
  → LibreChat 调 Codex endpoint (host.docker.internal:3084/v1/responses)
  → vega-codex-proxy: require_admin 鉴权 → 按 (user_id, conv_key) 查 SQLite 找/建 codex thread
  → codex_app_server.run_turn: turn/start → 长驻 codex app-server (stdio JSON-RPC)
  → 流式 notification (item/agentMessage/delta 等) → codex_events 归一化成 markdown
  → SSE 推回 LibreChat → React 渲染
  → inject.js (MutationObserver) 后处理: 命令块默认折叠 / token 文本→chip
```

## 关键设计

- **app-server 而非 exec**：长驻单进程 JSON-RPC，delta 为短通知，无超长行；进程崩溃懒重启 + thread/resume 续传。
- **无上限 NDJSON 行缓冲**：弃用 asyncio readline 的行长上限，手动 `read(n)`+切行，根治超长输出中断。
- **多用户隔离**：session_store SQLite 复合主键 `(user_id, conv_key)`，两用户同消息文本不串台。
- **前端注入**：LibreChat 是预构建镜像、React 源码不在本地 → 唯一前端手段是 inject.js（sed 进 dist/index.html，Caddy `/vega-admin/*` 服务）。无 rehype-raw，折叠/chip/hub 全靠 MutationObserver + 外部状态 Map。
- **systemd `--host 0.0.0.0`**：proxy 在宿主、LibreChat 容器经 docker bridge 网关访问，故须 0.0.0.0；UFW 限只放行 docker 子网，勿对公网开。

## 模块（vega-codex-proxy/）

| 文件 | 职责 |
|---|---|
| `codex_app_server.py` | 长驻 codex app-server JSON-RPC 客户端 + 无上限行缓冲 |
| `codex_events.py` | app-server 事件 → markdown 文本块归一化 |
| `session_store.py` | SQLite 会话映射 (user 隔离 / archived / label) |
| `codex_schema.py` | schema 漂移告警 (防 codex 升级静默崩) |
| `app.py` | FastAPI: OpenAI 兼容端点 + /codex/session/* 控制 + 状态页 |
