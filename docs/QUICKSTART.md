# Vega Chat 上手指南

> 本指南面向**已完成部署**的实例（见 [DEPLOY.md](./DEPLOY.md)）。
> 注意：LibreChat 0.8.6 **没有自助改密 / web admin GUI**，所有管理动作走 `vc-admin` 命令行（见 [ADMIN.md](./ADMIN.md)）。

## 1. 创建你的第一个账号

部署后浏览器打开你的站点（部署时配置的 `DOMAIN_CLIENT`，例如 `https://chat.example.com/`）。

`.env.example` 默认 `ALLOW_REGISTRATION=true`，直接在登录页**注册**第一个账号即可。生产环境拿到第一个 admin 后建议关掉开放注册。

## 2. 把自己提为管理员

在**服务器上**（部署 docker-compose 的机器）执行：

```bash
vc-admin role you@example.com ADMIN     # 把刚注册的账号升为 ADMIN
vc-admin show you@example.com           # 确认 role: 'ADMIN'
```

改密码也走命令行（无 GUI）：

```bash
vc-admin passwd you@example.com 新密码
```

## 3. 配置模型 provider key

```bash
vc-admin set-key deepseek sk-你的-deepseek-key
# 或在 docker/.env 里填 OPENAI_API_KEY / ANTHROPIC_API_KEY 后重启
```

然后浏览器顶部模型选单切到对应模型。

## 4. 接入 Codex

确保已跑过 `./scripts/codex-onboard.sh`（幂等接入，见 DEPLOY.md）。之后在模型选单里会出现 **Codex** endpoint，直接选它对话即可——
codex 会在服务器 `CODEX_WORK_DIR` 下真实执行文件 / 命令 / 构建，输出以本项目定制的折叠 / token chip UI 呈现。

> 也可在 sidebar → 智能体构建器里新建一个绑定 `codex` 工具的私有 agent，给它固定的系统指令。详见 [ADMIN.md](./ADMIN.md)。

## 5. 加成员 / 控制权限

```bash
vc-admin add  friend@example.com 'Friend' friend          # 加普通 USER
vc-admin add  buddy@example.com  'Buddy'  buddy ADMIN      # 加管理员
vc-admin role friend@example.com ADMIN                     # 升降权限
vc-admin delete friend@example.com                         # 删(二次确认)
vc-admin list                                              # 列出所有用户
```

## 6. 出问题排查

```bash
vc-admin doctor          # 全链路自检
vc-admin logs            # 看 api 日志
vc-admin restart         # 重启 api

curl http://127.0.0.1:3084/healthz   # vega-codex-proxy 健康
```

## 7. 已知限制

- 改密 / 用户管理只能命令行（LibreChat 0.8.6 无对应 GUI）
- 部分设置项文本未汉化（上游 LibreChat i18n 未覆盖）

## 8. 完整文档

- [README.md](../README.md) — 项目说明与差异化
- [ADMIN.md](./ADMIN.md) — 管理手册（完整版）
- [DEPLOY.md](./DEPLOY.md) — 部署与故障排查
- [ARCHITECTURE.md](./ARCHITECTURE.md) — 架构与数据流
