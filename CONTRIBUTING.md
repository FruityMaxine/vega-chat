# 贡献 Vega Chat

欢迎 issue 与 PR。注意本项目为 **非商用 license**（见 LICENSE）。

## 开发

```bash
cp docker/.env.example docker/.env   # 填密钥
./scripts/setup.sh                   # 起本地全栈
cd vega-codex-proxy && ../.venv-admin/bin/python -m pytest tests/ -q   # 跑测试
```

## 持续集成 (CI)

仓库自带一份 ready-to-use 的 GitHub Actions 配置 `.github/ci.example.yml`
（pytest 跨 Python 3.10–3.12 + `py_compile` 语法门）。启用只需一步：

```bash
mkdir -p .github/workflows
git mv .github/ci.example.yml .github/workflows/ci.yml
git commit -m "ci: enable GitHub Actions" && git push
```

> 以 `.example` 后缀分发，是因为放置 `.github/workflows/*` 需要带 `workflow`
> scope 的 token；用你自己的账号在网页或本地推一次即可激活。

## 约定

- 后端 Python：FastAPI，改动配 pytest 单测。
- 前端 inject.js：纯原生 JS，MutationObserver 幂等 + 静默降级（注入失败不破坏 LibreChat 原站）。选择器走语义化 token + 多 fallback。
- UI 无 emoji，唯 SVG。
- 提交信息：Conventional Commits（`feat(scope): ...`），不加 AI co-author trailer。
- 前端改动须截图/E2E 实测，不只看代码。

## 不要提交

`docker/.env`（密钥）、`docker/data/`、`*.db`、`.venv*/`、`node_modules/`。
