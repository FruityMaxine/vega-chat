<div align="center">

# ⬡ Vega Chat

**A multi-model AI workbench that welds OpenAI Codex deep into the chat UI**

Built on the [LibreChat](https://github.com/danny-avila/LibreChat) core, with a long-lived codex
engine, IDE-grade codex interaction, and one-command self-deploy — not a patch, but an integrated product.

[简体中文](./README.md) · **English**

[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial-2ea043?style=flat-square)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Based on LibreChat](https://img.shields.io/badge/core-LibreChat-7c3aed?style=flat-square)](https://github.com/danny-avila/LibreChat)
[![Codex](https://img.shields.io/badge/engine-codex%20app--server-16a34a?style=flat-square)](https://github.com/openai/codex)
[![Stars](https://img.shields.io/github/stars/FruityMaxine/vega-chat?style=flat-square&logo=github)](https://github.com/FruityMaxine/vega-chat/stargazers)

</div>

---

## What is this

**Vega Chat** wraps OpenAI's `codex` CLI via its **app-server (JSON-RPC over stdio) protocol** into an
OpenAI-compatible endpoint, plugging it into the chat UI as a first-class model. You drive codex like you
chat with a model — it really executes files / commands / builds on the server — and the results render
through a **UI tailored for codex**.

> The usual approach is "throw a few patches onto LibreChat." Vega Chat's goal is to make codex feel like a
> professional IDE, and ship the whole thing as a one-command-deployable, open-source, standalone project.

## Differentiators

| Capability | Detail |
|---|---|
| **Long-lived codex app-server** | Single long-lived JSON-RPC process, unbounded NDJSON line buffering — cures mid-stream interruption on huge output; per-user SQLite session isolation |
| **Collapsible command/tool output** | bash/console output is **collapsed by default**, click to expand; the fold header shows a command preview + line count |
| **Token usage chip** | Trailing token count rendered as a styled chip (in / out / cached / hit-rate), not raw text |
| **Codex session manager** | List sessions, rename & label, archive/restore — like managing IDE workspaces |
| **Unified brand hub** | One gear entry for session control / user-model-quota management / system status |
| **One-command deploy + codex auto-onboard** | `./scripts/setup.sh` brings up the full stack + `./scripts/codex-onboard.sh` idempotently wires codex |
| **Robustness internals** | resume self-heal / abort / idle timeout / schema-drift tolerance (survives silent codex upgrades) |

## How it compares

|  | **Vega Chat** | Vanilla LibreChat + codex patch | codex-mobile-style web wrapper |
|---|---|---|---|
| codex integration | app-server JSON-RPC long-lived process | usually one-shot `exec` / raw stdout | a web layer over codex CLI |
| huge-output interruption | **cured** (unbounded NDJSON buffer) | hits asyncio line-length limit, streams break | often breaks, implementation-dependent |
| command / tool output | **collapsed by default** + preview + line count | raw wall of text | raw output |
| token usage | styled chip (in/out/cached/hit-rate) | raw text or none | none |
| close / rename session | **session manager** (list/label/archive) | no entry point | usually none |
| multi-user isolation | SQLite composite key, no cross-talk | patch-dependent | mostly single-user |
| multi-model capability | **keeps all of LibreChat** | kept | codex only |
| deployment | one-command `setup.sh` + idempotent onboard | hand-assembled | ad hoc |

> Not "a few more patches on LibreChat" — codex interaction is taken to IDE level, without sacrificing
> LibreChat's existing multi-model workbench.

## Architecture

```
                          Browser (you)
                              │  HTTPS
                       ┌──────▼──────┐
                       │    Caddy     │  reverse proxy + TLS
                       └──┬───────┬───┘
          /vega-admin/*   │       │   /  (chat)
                ┌─────────▼──┐ ┌──▼────────────────┐
                │ vega-admin │ │  LibreChat core    │  ← prebuilt image
                │ (FastAPI)  │ │  (React + Node)    │
                │ inject.js  │ └──┬─────────────────┘
                │ into chat  │    │ host.docker.internal:3084
                └────────────┘ ┌──▼─────────────────┐
                               │ vega-codex-proxy    │  ← core differentiator
                               │ codex app-server    │
                               │ long-lived JSON-RPC │
                               └──┬──────────┬───────┘
                          codex app-server   │ SQLite
                          (stdio JSON-RPC)   session_store
                                             (multi-user / labels)
```

- **vega-codex-proxy** (Python/FastAPI): wraps `codex app-server` into OpenAI-compatible `/v1/*` endpoints,
  normalizes events, manages sessions, exposes `/codex/session/*` control endpoints.
- **vega-admin** (Python/FastAPI): serves `inject.js` (folding/chip/hub into the chat page) + admin API forwarders.
- **inject.js**: a MutationObserver-driven frontend enhancement layer (folding / token chip / brand theme /
  session manager) injected into the prebuilt LibreChat page — no React source fork.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).

## Quick deploy

```bash
git clone https://github.com/FruityMaxine/vega-chat.git
cd vega-chat
cp docker/.env.example docker/.env && $EDITOR docker/.env   # fill in secrets
./scripts/setup.sh          # docker full stack + venv + systemd + health checks
./scripts/codex-onboard.sh  # wire codex (idempotent)
```

Full steps and troubleshooting in [docs/DEPLOY.md](./docs/DEPLOY.md).

## Screenshots

| Commands collapsed by default (click to expand) | Token usage chip |
|---|---|
| ![fold](docs/screenshots/fold-collapsed.png) | ![chip](docs/screenshots/token-chip.png) |

| Unified brand hub | Codex session manager |
|---|---|
| ![hub](docs/screenshots/hub.png) | ![sessions](docs/screenshots/session-manager.png) |

## Tech stack

Python · FastAPI · SQLite · Docker Compose · Caddy · vanilla-JS injection layer ·
[LibreChat](https://github.com/danny-avila/LibreChat) core · [codex](https://github.com/openai/codex) app-server

## Roadmap

See the [Chinese README](./README.md#roadmap). Highlights: session grouping by project, fold-block syntax
highlighting, cumulative token cost visualization, approval-policy GUI, fully containerized stack, and i18n.

## License

**[PolyForm Noncommercial 1.0.0](./LICENSE)** — personal / research / nonprofit use allowed, **commercial use
prohibited**. Commercial use requires a separate license. Third-party attributions in [THIRD_PARTY.md](./THIRD_PARTY.md).

Copyright © 2026 FruityMaxine.
