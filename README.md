# loom-agent-chat

A Claude Code **plugin** with one skill, `loom-chat`, that lets an AI agent talk to **Loom's agent-chat MCP server** — register under its bare role id, send directed messages to named teammates, and read its inbox.

Loom is a local read-only desktop viewer (Electron). When running, it exposes an MCP Streamable-HTTP server on `127.0.0.1:7077/mcp` (scanning up to `7077+15` if the port is busy). The bundled client (`skills/loom-chat/loom_chat.py`) auto-detects the running server, caches the session-bound identity, and exposes all 10 Loom tools as a CLI. Python **stdlib only** — nothing to install.

## Prerequisites

- The **Loom desktop app must be running**. If it is not, the skill detects this, warns, and skips agent-chat — it never blocks the rest of your task.
- **Best results with a Loom build that writes `<project>/.loom/mcp.json`** when a window opens — the skill reads that file to find the exact Loom instance serving the current project folder (disambiguating multiple Loom windows). Older builds that don't write the file still work: the skill falls back to scanning ports 7077..7077+15.
- `python3` on `PATH`.

## Install into Claude Code

Register this plugin in your agora marketplace (the recommended path):

1. Push this directory to a GitHub repo (the agora `plugin-register` flow resolves plugins from GitHub).
2. From Claude Code, run the agora register flow: `agora:run` → "register a new plugin" → point it at the `loom-agent-chat` repo. This adds an entry to `~/.claude/plugins/marketplaces/agora/plugins.json` and recompiles `marketplace.json`.
3. Restart/reload Claude Code so it picks up the new marketplace entry.

Alternative (no marketplace push): symlink or copy this directory under a marketplace Claude Code already loads, e.g.

```bash
ln -s /path/to/loom-agent-chat \
  ~/.claude/plugins/marketplaces/agora/loom-agent-chat
```

then register it locally via the agora flow.

Once installed, the skill is available as `loom-agent-chat:loom-chat` and triggers automatically when the agent needs to chat with other agents over Loom.

## Usage

Just ask the agent to chat with other agents (e.g. "register on Loom and message the knowledge engineer") — the skill gates on Loom being up, registers under its **bare role id** (e.g. `backend-engineer-1`), and sends **directed** messages by member name rather than broadcasting.

Chat messages are capped at a **configurable limit (default 500 characters)**, which the client reads from the server's advertised value (`LOOM_MAX_BODY` env → `maxBodyLength` in `<project>/.loom/mcp.json` → 500). Anything longer (reports, analyses, code dumps) is written to a file under `<project>/.loom/temp/` and shared as a short chat note pointing at its absolute path; the recipient reads it but does **not** delete it.

Chat history and reports **persist** across Loom sessions and are **never auto-deleted** — they stay until the human explicitly asks to delete them. A human-invoked `purge` command wipes all chat and all reports in one shot (the AI runs it only when the human asks); after a purge every agent must re-register.
