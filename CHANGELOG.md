# Changelog

本项目所有值得记录的变更都写在这里。格式参照
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号采用扩展 SemVer 4 段制 `MAJOR.MINOR.PATCH.BUILD`。

## [1.20.0.2] - 2026-06-01

### Security
- `vega-codex-proxy` / `vega-admin`：空 `JWT_SECRET` 启动告警护栏 —— 防止脱敏后的空默认值被静默用作可伪造 token 的密钥，提示运维用 `openssl rand -hex 32` 配置。
- 文档外部化：`QUICKSTART.md` 去除部署专属 bootstrap 凭据、改为通用注册→提权流程；`DEPLOY.md` 标注可选 admin-panel profile。
- 加 `.gitattributes`（统一 LF 行尾）。

## [1.20.0.1] - 2026-06-01

### Added
- `.github/ci.example.yml`：ready-to-use GitHub Actions（pytest 跨 Python 3.10–3.12 + `py_compile` 语法门）。
- CONTRIBUTING 增加 CI 一键启用说明。
- 仓库规范化：issue 模板（bug / feature）、PR 模板、`SECURITY.md`、本 `CHANGELOG.md`。

## [1.20.0.0] - 2026-06-01

首个公开发布。把 OpenAI Codex 通过 **app-server（JSON-RPC over stdio）协议**深度焊进
LibreChat 聊天界面，做成一体化、可一键部署的差异产品。

### Added
- **长驻 codex app-server 集成**（`vega-codex-proxy`）：单进程长驻 JSON-RPC 客户端，
  把 `codex` 包成 OpenAI 兼容 `/v1/*` 端点；**无上限 NDJSON 行缓冲**根治超长输出中断。
- **多用户会话隔离**：SQLite `session_store`，复合主键 `(user_id, conv_key)`，根治串台。
- **命令 / 工具输出折叠**：bash/console 输出默认折叠、点击展开，折叠头显示命令预览 + 行数。
- **token 用量 chip**：消息尾 token 做成样式化 chip（入 / 出 / 缓存 / 命中率），非裸文本。
- **codex 会话管理器**：列出会话、重命名打标签、归档 / 恢复；`/codex/session/{list,rename,close,interrupt}` 端点。
- **统一品牌 hub**：右上角齿轮一个入口通会话控制 / 管理跳转 / 系统状态。
- **一键部署**：`scripts/setup.sh`（docker 全栈 + venv + systemd + 健康校验）+
  `scripts/codex-onboard.sh`（幂等接入 codex）+ `scripts/teardown.sh`。
- **健壮性内功**：thread resume 自愈、turn interrupt/abort、空闲超时、codex schema 漂移容错。
- **文档**：README（差异化卖点 + 架构图 + quickstart + 截图）、`docs/ARCHITECTURE.md`、
  `docs/DEPLOY.md`、`docs/QUICKSTART.md`、`docs/ADMIN.md`、`CONTRIBUTING.md`、`THIRD_PARTY.md`。

### Security
- 全仓脱敏：密钥默认值置空 / 占位，私有域名 → `example.com`，内部绝对路径 → 可移植默认。
- `docker/.env` 被 `.gitignore` 屏蔽；`docker/.env.example` 提供全字段模板。

### License
- **PolyForm Noncommercial 1.0.0** —— 允许个人 / 研究 / 非营利使用，**禁止商用**。
- 第三方组件归属见 `THIRD_PARTY.md`（LibreChat 为 MIT）。

[1.20.0.2]: https://github.com/FruityMaxine/vega-chat/releases/tag/v1.20.0.2
[1.20.0.1]: https://github.com/FruityMaxine/vega-chat/releases/tag/v1.20.0.1
[1.20.0.0]: https://github.com/FruityMaxine/vega-chat/releases/tag/v1.20.0.0
