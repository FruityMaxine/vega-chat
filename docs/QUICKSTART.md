# Vega Chat 上手指南（已更正）

> **重要纠错**：上一版说 "Settings → Account → Change Password" 是错的。
> LibreChat 0.8.6-rc1 **没有自助改密 GUI**。所有管理动作走 `vc-admin` 命令行。
> 详见 `ADMIN.md`。

## 1. 登录

浏览器（手机 / PC）打开：

**https://chat.example.com/**

凭证（**临时**，建议立刻用 `vc-admin passwd` 改）：

- 邮箱：`admin@example.com`
- 密码：`REDACTED`

## 2. 改密码（不是 GUI 改，走命令行）

SSH 进 Vega，执行：

```bash
vc-admin passwd admin@example.com 你想要的新密码
```

## 3. 你是 admin

```bash
vc-admin show admin@example.com   # 看 role: 'ADMIN'
```

但 LibreChat 0.8.6-rc1 **没有 web admin 后台**。所有管理动作命令行：见 `ADMIN.md`。

## 4. 配 DeepSeek

```bash
vc-admin set-key deepseek sk-你的-deepseek-key
```

然后浏览器顶部模型选单切到 "DeepSeek"。

## 5. Codex Root Agent

登录后 sidebar → 智能体构建器 → 新建：
- 名字：`Codex Root`
- 模型：随便选一个（DeepSeek / GPT-4o）
- 工具：勾 `codex` 和 `codex-reply`
- 指令：参考 ADMIN.md 7.2
- **共享：Private（只你能看到）**
- Save

测试：发一条 "看下当前服务器内存"，agent 会调 codex MCP，codex 真去 root 执行。

## 6. 加朋友 / 控制权限

```bash
vc-admin add friend@example.com 'Friend' friend          # 普通 USER
vc-admin add buddy@example.com  'Buddy'  buddy ADMIN     # 给管理员权限
vc-admin role friend@example.com ADMIN                   # 升降权限
vc-admin delete friend@example.com                       # 删
vc-admin list                                      # 看所有
```

## 7. 已知问题

- **未汉化文本** 5 处（设置 → 通用 / 对话）：上游 LibreChat i18n 没补，等下次升级
- **改密只能命令行**：没有 GUI（LibreChat 设计如此）
- **没有 admin web 后台**：所有用户管理走 `vc-admin`

## 8. 完整文档

- `README.md` — 项目说明
- `ADMIN.md` — **管理手册（完整版）**
- `docs/progress/部署记录_2026-05-23.md` — 部署日志

## 9. 出问题排查

```bash
vc-admin doctor          # 全链路自检
vc-admin logs            # 看 api 日志
vc-admin restart         # 重启 api
```
