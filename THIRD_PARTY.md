# 第三方组件与归属

Vega Chat 在以下开源项目之上构建。各自版权与 license 归原作者所有。

## 运行依赖（不随本仓库分发源码）

| 组件 | License | 说明 |
|---|---|---|
| [LibreChat](https://github.com/danny-avila/LibreChat) | MIT | 聊天内核，以预构建 docker 镜像 `ghcr.io/danny-avila/librechat-dev` 形式使用，本仓库不含其源码 |
| [OpenAI Codex CLI](https://github.com/openai/codex) | 见上游 | 外部 CLI，部署时单独安装 |
| MongoDB / Meilisearch / pgvector | 各自 | 标准 docker 镜像 |

## 可选外部组件（不在本仓库内）

- **librechat-admin-panel**（ClickHouse 的 admin GUI fork）—— 可选管理面板，单独部署，本仓库不含。
- **CLIProxyAPI** —— 可选的 provider 代理工具，单独部署。

## 本仓库的原创部分

`vega-codex-proxy/` `vega-admin/`（含 inject.js / 前端增强层）`configs/` `docker/`（编排，不含密钥）
`scripts/` `docs/` 为本项目原创，按根目录 [LICENSE](./LICENSE)（PolyForm Noncommercial 1.0.0）授权。
