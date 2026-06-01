# Vega Chat 管理手册

> **已部署独立 Admin GUI**：https://admin.example.com/
> 凭证：与主站同（你的 admin 邮箱密码）。
> LibreChat 主聊天 UI 没有 admin 面板，所有管理动作在 admin panel 或 `vc-admin` CLI。

## 0. 三个入口

| 入口 | URL | 干嘛 |
|---|---|---|
| **聊天 UI**（普通用户 + 你自己）| https://chat.example.com/ | 跟模型对话 / 用 agent |
| **Admin Panel**（GUI 后台）| https://admin.example.com/ | 用户管理 / 配置 / 角色 / 权限 / 组 |
| **vc-admin CLI**（SSH 进服务器跑）| `vc-admin --help` | 改密 / 批量操作 / 直连 DB |

## 0.1 Admin Panel 功能（GUI）

登录 https://admin.example.com/ 后看到 4 大区：

- **Configuration**：可视化编辑 LibreChat 实例配置（每个字段一个表单）
- **Access**：建组 / 分角色 / 改成员权限
- **Grants**：控制每个角色能用什么能力（看配置 / 管用户 / 改实例配置等）
- **Help**：文档链接

用 Access 加用户 / 改角色比 CLI 直观。配置 override 也走 GUI。

## 0.2 你的当前角色

```bash
vc-admin show admin@example.com
```

应返回 `role: 'ADMIN'`。在 LibreChat 里你是 admin —— 但因为 0.8.6-rc1 **还没有 web admin 面板**（roadmap 项），admin 身份目前只决定：
- 部分 RBAC 默认权限（如 prompt share）
- 未来 admin panel 上线后会自动获得管理权限

**现在所有管理动作走 `vc-admin` 而非浏览器**。

## 1. 锁死模型 / Endpoint（已配置 ✓）

**当前状态**：普通用户登录后**只能选 DeepSeek + Agents**，**完全看不到 OpenAI / Anthropic / Google**。
他们也**不能填自己的 API key**（已禁 `user_provided` 模式）。

实现：
- `.env` 设 `ENDPOINTS=custom,agents` → 隐藏所有原生 provider
- `.env` 设 `OPENAI_API_KEY=` 空 / `ANTHROPIC_API_KEY=` 空（不写 `user_provided`）
- `librechat.yaml` 的 `endpoints.custom[]` 显式声明你想暴露的模型
- DeepSeek key 用 `vc-admin set-key deepseek sk-xxx` 集中配置

**加新模型**（仅 admin 操作）：
```bash
# 1. 编辑 librechat.yaml 在 endpoints.custom: 数组里追加一项
#    模仿 DeepSeek 块改 name / baseURL / models
# 2. 用 vc-admin set-key 把对应 env 设到 .env
# 3. force-recreate api 让 env 生效
cd /opt/vega-chat/docker
docker compose up -d --force-recreate api
```

**警告**：千万别在 `.env` 写 `XXX_API_KEY=user_provided` —— 会让该 endpoint 出现在前端让用户填自己的 key。

## 2. 改密码

```bash
# 指定密码
vc-admin passwd admin@example.com MyNewStrongPass123

# 不指定 → 随机生成并打印
vc-admin passwd admin@example.com
```

## 2. 加用户

```bash
# 普通用户（看不到 Codex agent）
vc-admin add friend@example.com 'Friend Name' friend

# 管理员
vc-admin add buddy@example.com 'Buddy' buddy ADMIN
```

会自动随机生成密码并打印。把临时密码给他们，让他们登录后告诉你新密码，你用 `vc-admin passwd` 帮他们改。

## 3. 临时开放公开注册（让朋友自己注册）

```bash
vc-admin open          # 打开注册
# ... 让朋友访问 https://chat.example.com/ 自助注册 ...
vc-admin close         # 注册完立刻关
```

## 4. 列用户 / 看用户详情 / 删用户 / 改角色

```bash
vc-admin list
vc-admin show friend@example.com
vc-admin delete friend@example.com    # 会二次确认
vc-admin role friend@example.com ADMIN
```

## 5. 设 provider API key

```bash
vc-admin set-key deepseek sk-xxxxxxxxxxxx
vc-admin set-key openai sk-xxxxxxxxxxxx
vc-admin set-key anthropic sk-ant-xxxxxxxxxxxx
```
（自动重启 api 容器）

## 6. 服务运维

```bash
vc-admin ps                       # 容器状态
vc-admin logs                     # api 日志
vc-admin logs mongo               # 看 mongo
vc-admin restart                  # 重启 api
vc-admin doctor                   # 全链路体检（推荐每天跑一次）
```

## 7. 防止别人用 Codex 干掉你服务器

LibreChat 的 MCP 工具默认对所有**登录用户**在 Agent Builder 里都能选。要锁死，三种方式（推荐组合用）：

### 7.1 已经做了（librechat.yaml）
```yaml
mcpServers:
  codex:
    chatMenu: false   # 不出现在普通 chat 模型选单
```

### 7.2 你建专属 Codex Agent + ACL=Private
1. 登录后 sidebar → **智能体构建器**（脑形图标）
2. 新建 → 名字 `Codex Root`，挑模型（DeepSeek / GPT-4o 都行）
3. **工具**栏选 codex 暴露的 `codex` / `codex-reply`
4. **指令**写：「你是 Vega 服务器的 root 运维助手，调 codex 工具时直接干活，不要反复确认」
5. **共享**改成 **Private**（只你能看到）
6. Save

这样普通用户即使会用 agent builder，他自己建的 agent 选 codex 工具时**仍可调到 codex**（这是 LibreChat 当前 RBAC 局限）。

### 7.3 釜底抽薪 —— 用 librechat.yaml 关掉普通用户的 agentBuilder
（需要重启）我可以帮你改，但代价是普通用户也不能建自己的 agent 了。要不要？

### 7.4 双保险 —— 不给普通用户账号
最简单：你只给信任的人开账号。陌生人完全不让进，配合 `vc-admin close` 关注册。

## 8. 已知未汉化（上游问题，等 LibreChat 升级）

**通用 tab**：`Markdown`、`Switch to Chat History on new chat`
**对话 tab**：`Auto-expand tool details`、`Temporary Chat by default`、`Advanced prompts editor`

这 5 个 key 在 zh-Hans/translation.json 缺失，i18n fallback 到英文。

**修法（如果你想手动改）**：
- 等 LibreChat 下次发版补上（最稳）
- 容器内 sed 替换打包后 JS 字符串（脆弱，升级会丢，**不推荐**）
- 自建 fork（成本高，不推荐）

**结论**：建议先放着，下次升级 LibreChat 时大概率会修。不影响功能。

## 9. 数据备份

MongoDB 数据 + Meilisearch 索引在 `/opt/vega-chat/docker/data/` 下。需要备份：

```bash
cd /opt/vega-chat/docker
tar czf /opt/backups/vega-chat-backup-$(date +%Y%m%d).tar.gz data/
```

## 10. 升级 LibreChat

```bash
cd /opt/vega-chat/docker
docker compose pull
docker compose up -d
docker compose logs -f api    # 看启动有没问题
```

升级后 i18n 翻译可能就补全了。
