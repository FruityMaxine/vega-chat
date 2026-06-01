# 安全策略

## 上报漏洞

请**不要**通过公开 issue 上报安全漏洞。

请通过 GitHub 的私密通道上报：仓库 **Security → Report a vulnerability**
（[Private vulnerability reporting](https://github.com/FruityMaxine/vega-chat/security/advisories/new)）。

收到后会尽快确认并评估影响，修复后再公开披露。

## 范围

本项目把 `codex` CLI 以 app-server 协议包成 OpenAI 兼容端点。需特别留意的面：

- **vega-codex-proxy** 暴露的 `/v1/*` 与 `/codex/session/*` 端点：鉴权、会话隔离、命令执行边界。
- **codex 真实执行能力**：codex 会在 `CODEX_WORK_DIR` 下真实读写文件 / 跑命令。务必把工作目录限定在受控路径，approval 策略按需收紧。
- **vega-admin** 的 inject.js 注入层与管理 API。
- 部署拓扑：后端只监听回环 / docker 子网，对外一律经反代（Caddy）+ 鉴权。

## 部署侧硬性建议

- 所有密钥（`JWT_SECRET` / `JWT_REFRESH_SECRET` / `CREDS_KEY` / `MEILI_MASTER_KEY` 等）必须用 `openssl rand` 自行生成，**绝不沿用任何示例值**。仓库内所有默认值均为空或占位。
- `docker/.env` 已被 `.gitignore` 屏蔽，**绝不提交**。
- `vega-codex-proxy` 监听 `0.0.0.0:3084` 仅为让 LibreChat 容器经 docker bridge 网关访问宿主；UFW/防火墙须只放行 docker 子网，**不要对公网开放 3084**。
- 向量库（pgvector）默认密码务必改掉，且不对外暴露端口。

## 不在仓库中的东西

本仓库经过脱敏：不含任何真实密钥、私有域名、内部绝对路径或运行时数据。
若你发现仍有疑似泄露，请按上述私密通道告知。
