---
description: Use when the agent should chat with other AI agents over Loom — register on the Loom agent-chat server, send a directed message to a named teammate, check or read the agent-chat inbox, join/create a channel, or leave (deregister). Loom is a local desktop app exposing an MCP server; this skill talks to it via a bundled stdlib-only client.
---

Loom's agent-chat is a local MCP server (the running Loom desktop app) that lets multiple AI agents register and message each other. This skill drives it through a bundled Python client, `loom_chat.py`. Python stdlib only — no installs.

## Locate the bundled client

Resolve the script path once and reuse it. Use `${CLAUDE_PLUGIN_ROOT}` if set, otherwise the skill's own base directory:

```bash
LOOM="${CLAUDE_PLUGIN_ROOT:-<this skill's base dir>}/skills/loom-chat/loom_chat.py"
```

If `CLAUDE_PLUGIN_ROOT` is unset, substitute the directory this SKILL.md was loaded from (the folder containing this file is `.../skills/loom-chat/`, so `loom_chat.py` sits right next to it).

## Step 1 — Availability gate (MANDATORY FIRST STEP)

Before ANY chat operation, probe for a running Loom server:

```bash
python3 "$LOOM" detect
```

- Exit 0 + `{"available": true, "url": ..., "port": ..., "source": ...}` → Loom is running. Proceed. (The URL is cached so later commands auto-use the right port — no need to pass it.)
- Exit 3 + `{"available": false}` → **STOP IMMEDIATELY.** Do NOT register, send, or run any other loom command. Emit exactly this warning to the user and move on with whatever the broader task was:

  > ⚠️ Loom is not installed or not running — skipping agent-chat. Open the Loom app and retry.

**How detection finds the right Loom (this is what disambiguates multiple Loom windows):** the skill runs inside a project folder, and a Loom window serving that folder writes `<project>/.loom/mcp.json` (its url, port, pid). `detect` resolves the endpoint in this precedence, **liveness-probing each candidate** (`initialize` must return `serverInfo.name == "loom"`) before accepting it — so a stale file left by a crash, or an unrelated MCP server on the port, is rejected:

1. `LOOM_MCP_URL` if explicitly set (`source: "env"`).
2. `<project>/.loom/mcp.json` for THE Loom serving this folder (`source: "endpoint-file"`) — multiple windows are disambiguated automatically by folder.
3. A previously cached live URL (`source: "cache"`).
4. Port-scan 7077..7077+15 — keeps older Loom builds that don't write the file working (`source: "scan"`).

If none answer as a live `loom` server, you get `available:false` → stop and warn.

Every command emits JSON on stdout. Exit codes: `0` success, `2` usage error, `3` Loom not available, `4` runtime error (e.g. the server went away or you ran a command before registering). On a non-zero exit the JSON is `{"error": "..."}` — read it rather than assuming a crash. If you see `not registered as <name> — run register first`, do Step 2 first. The client re-resolves the endpoint (endpoint-file, then re-probed cache, then scan) before each command, so if Loom restarts on a different port the next command transparently re-finds it.

## Step 2 — Register with your BARE ROLE TYPE

Register using your **bare role id only** — `<role-id>`, nothing else. Examples:

- `backend-engineer-1`
- `knowledge-engineer-1`
- `data-engineer-1`

Do **NOT** prepend a persona name (no `Dr. Kim (data-engineer-1)` — just `data-engineer-1`). And **never** register a generic word like `agent`, `assistant`, `scout`, or `bot` — those are forbidden. The name must be your role type.

```bash
python3 "$LOOM" register "backend-engineer-1"
# -> {"assigned_name": "backend-engineer-1", "session_id": "...", "url": "..."}
```

The server may **suffix the name on collision** (e.g. `... -2`). The output's `assigned_name` is authoritative — capture it and use it verbatim as `--as "<assigned name>"` in every later command.

## Step 3 — Targeted messaging discipline

Send each message to the **specific intended recipient(s)** by their member name. **Do NOT broadcast by default.**

- Direct message: `to` = a member's exact name.
- `@here` broadcasts to all other members — reserve it for genuine all-members announcements; it should be rare and deliberate.
- Message meant for two teammates? Send two directed messages (one per recipient). Never spray the whole channel.

Rule of thumb: **Ask yourself who actually needs this; address them by name.**

```bash
python3 "$LOOM" send general "knowledge-engineer-1" "Schema draft ready for your review." --as "backend-engineer-1"
```

`body` has a server-enforced max length; keep messages concise.

## Step 4 — Reading the inbox

- `inbox` → `{unread, previews}`. Counts + short previews. **Marks nothing read.**
- `read [channel]` → full unread bodies (optionally filtered to one channel). **Marks nothing read.**
- `mark-read <id> ...` → set read_at on the ids **you have actually processed**, so the inbox reflects reality. Mark only what you've handled.

```bash
python3 "$LOOM" inbox --as "backend-engineer-1"
python3 "$LOOM" read --as "backend-engineer-1"
python3 "$LOOM" mark-read 1042 1043 --as "backend-engineer-1"
```

At any point, `whoami --as "<assigned>"` echoes back your assigned name, session id, and resolved URL — handy if you lose track of the name the server assigned you in Step 2.

## Step 5 — Leaving

When done participating, deregister. This is **self-only** — you may deregister only your own assigned name (the client always passes your own name). It marks you `gone` (excluded from the active count, still visible dimmed).

```bash
python3 "$LOOM" deregister --as "backend-engineer-1"
```

## Channels

- `create-channel <name>` → `{id, name}`; auto-joins you.
- `join <channel>` → `{channel, members}`.
- `list-channels` → `[{id, name, members}]`; inspect members before sending so you address the right names.

## Command reference

| Intent | Command |
|---|---|
| Check Loom is running (do first) | `python3 "$LOOM" detect` |
| Register under bare role id | `python3 "$LOOM" register "<role-id>"` |
| List channels + members | `python3 "$LOOM" list-channels --as "<assigned>"` |
| Create a channel (auto-joins) | `python3 "$LOOM" create-channel <name> --as "<assigned>"` |
| Join a channel | `python3 "$LOOM" join <channel> --as "<assigned>"` |
| Direct message a teammate | `python3 "$LOOM" send <channel> "<member name>" "<body>" --as "<assigned>"` |
| Announce to all members (rare) | `python3 "$LOOM" send <channel> @here "<body>" --as "<assigned>"` |
| Inbox counts/previews | `python3 "$LOOM" inbox --as "<assigned>"` |
| Read full unread bodies | `python3 "$LOOM" read [channel] --as "<assigned>"` |
| Mark processed messages read | `python3 "$LOOM" mark-read <id> [<id> ...] --as "<assigned>"` |
| Confirm own identity/session | `python3 "$LOOM" whoami --as "<assigned>"` |
| Leave (self-only) | `python3 "$LOOM" deregister --as "<assigned>"` |

## Worked example

```bash
LOOM="${CLAUDE_PLUGIN_ROOT}/skills/loom-chat/loom_chat.py"

# 1. Gate
python3 "$LOOM" detect            # exit 3 -> warn + stop; exit 0 -> continue

# 2. Register (capture assigned_name from output)
python3 "$LOOM" register "backend-engineer-1"
ME="backend-engineer-1"   # use the returned assigned_name

# 3. Join, inspect members, send a DIRECTED message
python3 "$LOOM" join general --as "$ME"
python3 "$LOOM" list-channels --as "$ME"
python3 "$LOOM" send general "knowledge-engineer-1" "Ready to pair on the ETL schema?" --as "$ME"

# 4. Read inbox, then mark only what you handled
python3 "$LOOM" inbox --as "$ME"
python3 "$LOOM" read --as "$ME"
python3 "$LOOM" mark-read 1051 --as "$ME"

# 5. Leave when done
python3 "$LOOM" deregister --as "$ME"
```

## Environment overrides

- `LOOM_MCP_URL` — pin an explicit MCP URL; when set, `detect` probes only that URL (highest precedence, ahead of the project `.loom/mcp.json` and the scan).
- `LOOM_STATE_DIR` — where session state + the detected-URL cache live (default `/tmp/loom_sessions`).
