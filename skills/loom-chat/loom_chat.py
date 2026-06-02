#!/usr/bin/env python3
"""loom_chat.py — tiny MCP Streamable-HTTP client for Loom's agent chat.

Loom (the read-only desktop viewer) exposes a 10-tool MCP server on
http://127.0.0.1:7077/mcp. Detection first reads <project>/.loom/mcp.json (the
endpoint file Loom writes per project, identifying the instance serving this
folder), then falls back to scanning the next ports up to 7077+15. Every
candidate is liveness-probed: accepted only if serverInfo.name == "loom" in its
initialize response. Identity is bound to the transport session at register()
time, so we persist the mcp-session-id per agent name and replay it on every
later call.

Usage:
  loom_chat.py detect
  loom_chat.py register <name>
  loom_chat.py create-channel <name>          --as <name>
  loom_chat.py join <channel>                 --as <name>
  loom_chat.py list-channels                  --as <name>
  loom_chat.py send <channel> <to> <body...>  --as <name>
  loom_chat.py inbox                          --as <name>
  loom_chat.py read [channel]                 --as <name>
  loom_chat.py mark-read <id> [<id> ...]      --as <name>
  loom_chat.py deregister                     --as <name>
  loom_chat.py purge                          --as <name>
  loom_chat.py whoami                         --as <name>

`to` is a teammate's member name for a direct message, or "@here" to broadcast
to all other channel members. After register, `<name>` may be auto-suffixed by
the server (scout -> scout-2); always use the printed assigned name with --as.

Message bodies are capped; the cap is configurable (default 500). It is read
from the server's advertised value: env LOOM_MAX_BODY -> maxBodyLength in
<project>/.loom/mcp.json -> 500. `send` rejects anything longer (exit 2) --
offload long content to a file under <project>/.loom/temp/ and send a short
note with its absolute path + a 1-2 sentence summary instead.

`purge` (human-invoked only) calls the server's purge_all tool, deleting ALL
chat and ALL reports in .loom/temp/. Everyone must re-register afterward.

Exit codes:
  0  success
  2  usage error (includes a body over the resolved cap)
  3  detect: no reachable Loom MCP server found
  4  runtime error (not registered, transport/server failure)

Env overrides:
  LOOM_MCP_URL    explicit MCP url; if set, detection probes ONLY this url
  LOOM_STATE_DIR  session/state cache dir (default /tmp/loom_sessions)
  LOOM_MAX_BODY   override the message-body cap (integer; default 500)
"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

STATE_DIR = os.environ.get("LOOM_STATE_DIR", "/tmp/loom_sessions")
# Pinned to the MCP protocol revision Loom's server negotiates. If a future
# Loom release requires a newer protocol, bump this to match its server.
PROTOCOL = "2024-11-05"
BASE_PORT = 7077
MAX_ATTEMPTS = 16  # scan 7077 .. 7077+15
DEFAULT_MAX_BODY = 500  # fallback per-message body cap when none is advertised
URL_CACHE = os.path.join(STATE_DIR, "_url.json")


def _state_path(name):
    return os.path.join(STATE_DIR, name.replace("/", "_") + ".json")


def _explicit_url():
    """Return LOOM_MCP_URL if the user pinned one, else None."""
    return os.environ.get("LOOM_MCP_URL")


def _candidate_urls():
    """URLs to probe during detection."""
    pinned = _explicit_url()
    if pinned:
        return [pinned]
    return ["http://127.0.0.1:%d/mcp" % (BASE_PORT + i) for i in range(MAX_ATTEMPTS)]


def _post(url, payload, session_id=None, timeout=20):
    """POST one JSON-RPC message; return (parsed_json_or_None, session_id)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if session_id:
        req.add_header("mcp-session-id", session_id)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        sid = resp.headers.get("mcp-session-id") or session_id
        raw = resp.read().decode()
    # Response is either plain JSON or SSE ("event: message\ndata: {...}").
    result = None
    if raw.strip():
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                result = json.loads(line[len("data:"):].strip())
                break
        if result is None and raw.lstrip().startswith("{"):
            result = json.loads(raw)
    return result, sid


def _init_payload():
    return {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                   "clientInfo": {"name": "loom_chat", "version": "1.0"}},
    }


def _probe(url, timeout=2):
    """Try the initialize handshake at url.

    Returns (sid, init_result) if this is a Loom MCP server, else None.
    Connection refused / timeouts / other errors are swallowed -> None.
    """
    try:
        res, sid = _post(url, _init_payload(), timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
            ConnectionError, OSError, ValueError):
        return None
    if not res or not sid:
        return None
    name = res.get("result", {}).get("serverInfo", {}).get("name")
    if name != "loom":
        return None
    return sid, res


def _scan_url():
    """Port-scan 7077..7077+15 for a live Loom server. Return url or None.

    Kept for older Loom builds that do not write <project>/.loom/mcp.json.
    """
    for url in _candidate_urls():
        if _probe(url) is not None:
            return url
    return None


def _endpoint_file_path():
    """Path to the current project's Loom endpoint file: <cwd>/.loom/mcp.json.

    The skill runs inside a project folder; the Loom serving that folder writes
    this file (and removes it on graceful shutdown).
    """
    return os.path.join(os.getcwd(), ".loom", "mcp.json")


def _read_endpoint_file():
    """Parse <cwd>/.loom/mcp.json and return the dict, or None if absent/bad.

    Single reader for the project endpoint file; other helpers derive specific
    fields (url, maxBodyLength) from its result.
    """
    try:
        with open(_endpoint_file_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _endpoint_file_url():
    """URL advertised by <cwd>/.loom/mcp.json, or None if absent/invalid.

    No liveness check here — the caller probes it. A stale file left by a crash
    is rejected by the probe, not by this reader.
    """
    data = _read_endpoint_file()
    if not data:
        return None
    url = data.get("url")
    if url:
        return url
    # Fall back to building it from port if url is missing but port present.
    port = data.get("port")
    if isinstance(port, int):
        return "http://127.0.0.1:%d/mcp" % port
    return None


def _resolve_max_body():
    """Resolve the per-message body cap (chars).

    Precedence: env LOOM_MAX_BODY (integer) -> maxBodyLength advertised in
    <cwd>/.loom/mcp.json -> DEFAULT_MAX_BODY. Invalid/non-positive values at any
    tier are ignored and we fall through to the next.
    """
    env = os.environ.get("LOOM_MAX_BODY")
    if env is not None:
        try:
            n = int(env)
            if n > 0:
                return n
        except ValueError:
            pass
    data = _read_endpoint_file()
    if data:
        n = data.get("maxBodyLength")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            return n
    return DEFAULT_MAX_BODY


def _discover():
    """Resolve the Loom MCP URL using the full precedence. Liveness-probes
    every candidate before accepting it. Returns (url, source) where source is
    one of env | endpoint-file | scan, or (None, None) if nothing is live.

    Precedence:
      1. LOOM_MCP_URL pinned        -> probe only that            (env)
      2. <cwd>/.loom/mcp.json url   -> probe it                   (endpoint-file)
      3. port-scan 7077..7077+15    -> probe each                 (scan)
    """
    pinned = _explicit_url()
    if pinned:
        return (pinned, "env") if _probe(pinned) is not None else (None, None)
    ep = _endpoint_file_url()
    if ep and _probe(ep) is not None:
        return ep, "endpoint-file"
    scanned = _scan_url()
    if scanned is not None:
        return scanned, "scan"
    return None, None


def _detect_url():
    """Back-compat helper: discovered url or None (drops the source tag)."""
    return _discover()[0]


def _cache_url(url):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(URL_CACHE, "w") as f:
        json.dump({"url": url}, f)


def _cached_url():
    try:
        with open(URL_CACHE) as f:
            return json.load(f).get("url")
    except (OSError, ValueError):
        return None


def _resolve_url():
    """Best URL to talk to Loom on, for non-detect commands.

    Order: explicit LOOM_MCP_URL -> <cwd>/.loom/mcp.json (probed) -> cached
    detected url (re-validated) -> fresh scan. The project endpoint file is
    authoritative for the current folder, so it is preferred over the generic
    cache. The cached URL is re-probed before use (H1): if Loom restarted on a
    different port the stale entry is discarded and we rescan, then rewrite the
    cache. Guarantees we never hand a dead port to register()/_call_tool().
    """
    pinned = _explicit_url()
    if pinned:
        return pinned
    ep = _endpoint_file_url()
    if ep and _probe(ep) is not None:
        _cache_url(ep)
        return ep
    cached = _cached_url()
    if cached and _probe(cached) is not None:
        return cached
    url = _scan_url()
    if url:
        _cache_url(url)
    return url


def _new_session(url):
    """initialize + notifications/initialized; return fresh session id."""
    res, sid = _post(url, _init_payload())
    if not sid:
        raise RuntimeError("no mcp-session-id returned; init result=%r" % res)
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    return sid


def _call_tool(url, session_id, tool, args):
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": tool, "arguments": args}}
    res, _ = _post(url, payload, session_id)
    if res is None:
        raise RuntimeError("empty response from server")
    if "error" in res:
        raise RuntimeError("loom error: %s" % json.dumps(res["error"]))
    # MCP tool result: { result: { content: [ { type:text, text: "..." } ] } }
    content = res.get("result", {}).get("content", [])
    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    try:
        return json.loads(text) if text else res.get("result")
    except json.JSONDecodeError:
        return text


class NotRegisteredError(Exception):
    """Raised when no session state exists for the requested --as name."""


def _load(name):
    try:
        with open(_state_path(name)) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        raise NotRegisteredError(
            "not registered as %s — run register first" % name)


def _save(name, url, sid, assigned):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_state_path(name), "w") as f:
        json.dump({"requested": name, "assigned": assigned,
                   "session_id": sid, "url": url}, f)


def _session_for(name):
    st = _load(name)
    # Older state may lack "url"; fall back to resolution.
    return st.get("url") or _resolve_url(), st["session_id"], st["assigned"]


def _port_of(url):
    """Best-effort extract the integer port from an http URL, else None."""
    try:
        return int(url.rsplit(":", 1)[1].split("/", 1)[0])
    except (IndexError, ValueError):
        return None


def _cmd_detect():
    # _discover() applies the full precedence (env -> project endpoint-file ->
    # port-scan), liveness-probing every candidate; detection logic lives in
    # exactly one place. If the endpoint-file/scan miss but a live cache exists
    # it is honored too, tagged "cache" -- but ONLY when no env URL is pinned.
    # With a pin, _discover already probed exactly that URL; reporting a cached
    # different URL would disagree with the command path (_resolve_url strictly
    # honors the pin, dead or not).
    url, source = _discover()
    if url is None and _explicit_url() is None:
        cached = _cached_url()
        if cached and _probe(cached) is not None:
            url, source = cached, "cache"
    if url is None:
        print(json.dumps({"available": False}))
        return 3
    _cache_url(url)
    print(json.dumps({"available": True, "url": url,
                      "port": _port_of(url), "source": source}))
    return 0


class UsageError(Exception):
    """Raised for argument/usage problems -> exit 2."""


def _parse_as(argv):
    """Split off --as <name>; return (as_name, rest). Bounds-checked."""
    as_name = None
    rest = []
    i = 1
    while i < len(argv):
        if argv[i] == "--as":
            if i + 1 >= len(argv):
                raise UsageError("--as requires a <name> argument")
            as_name = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return as_name, rest


def _run(argv):
    """Execute one command. May raise UsageError (->2), NotRegisteredError,
    or any runtime Exception (->4); main() is the error boundary."""
    cmd = argv[0]

    if cmd == "detect":
        return _cmd_detect()

    as_name, rest = _parse_as(argv)

    # Validate send args + the message-body cap up front, before any session
    # lookup or network call -- both are pure usage errors (exit 2) regardless
    # of registration state, and we must never send an over-length body.
    if cmd == "send":
        if len(rest) < 3:
            raise UsageError("send requires <channel> <to> <body...>")
        body = " ".join(rest[2:])
        max_body = _resolve_max_body()
        if len(body) > max_body:
            raise UsageError(
                "message body is %d chars (max %d). Do NOT send long content "
                "as chat -- write it to %s/.loom/temp/<name>.md and send a "
                "short note with the file's absolute path + a 1-2 sentence "
                "summary." % (len(body), max_body, os.getcwd()))

    if cmd == "register":
        if not rest:
            raise UsageError("register requires a <name>")
        req_name = rest[0]
        url = _resolve_url()
        if not url:
            print(json.dumps({"available": False}))
            return 3
        sid = _new_session(url)
        out = _call_tool(url, sid, "register", {"name": req_name})
        assigned = out.get("name", req_name) if isinstance(out, dict) else req_name
        _save(req_name, url, sid, assigned)
        if assigned != req_name:  # also key state by the assigned name
            _save(assigned, url, sid, assigned)
        print(json.dumps({"assigned_name": assigned, "session_id": sid, "url": url}))
        return 0

    if as_name is None:
        raise UsageError("--as <name> required for this command")
    url, sid, assigned = _session_for(as_name)

    if cmd == "whoami":
        print(json.dumps({"assigned": assigned, "session_id": sid, "url": url}))
    elif cmd == "create-channel":
        if not rest:
            raise UsageError("create-channel requires a <name>")
        print(json.dumps(_call_tool(url, sid, "create_channel", {"name": rest[0]})))
    elif cmd == "join":
        if not rest:
            raise UsageError("join requires a <channel>")
        print(json.dumps(_call_tool(url, sid, "join_channel", {"channel": rest[0]})))
    elif cmd == "list-channels":
        print(json.dumps(_call_tool(url, sid, "list_channels", {})))
    elif cmd == "deregister":
        # self-only: caller may deregister only its own assigned name
        print(json.dumps(_call_tool(url, sid, "deregister", {"name": assigned})))
    elif cmd == "purge":
        # DESTRUCTIVE: wipes ALL chat + ALL reports in .loom/temp/. Human-
        # invoked only. After this everyone is gone -> callers must re-register.
        print(json.dumps(_call_tool(url, sid, "purge_all", {})))
    elif cmd == "send":
        # args + body length already validated up front (resolved cap)
        channel, to = rest[0], rest[1]
        body = " ".join(rest[2:])
        print(json.dumps(_call_tool(url, sid, "send_message",
                                    {"channel": channel, "to": to, "body": body})))
    elif cmd == "inbox":
        print(json.dumps(_call_tool(url, sid, "check_inbox", {})))
    elif cmd == "read":
        args = {"channel": rest[0]} if rest else {}
        print(json.dumps(_call_tool(url, sid, "read_messages", args)))
    elif cmd == "mark-read":
        if not rest:
            raise UsageError("mark-read requires at least one <id>")
        try:
            ids = [int(x) for x in rest]
        except ValueError:
            raise UsageError("mark-read ids must be integers")
        print(json.dumps(_call_tool(url, sid, "mark_read", {"message_ids": ids})))
    else:
        raise UsageError("unknown command: %s" % cmd)
    return 0


def main(argv):
    """Error boundary. Exit codes: 0 ok, 2 usage, 3 not-available,
    4 runtime error. Never lets an exception escape as a traceback."""
    if not argv:
        print(__doc__)
        return 2
    try:
        return _run(argv)
    except UsageError as e:
        print(json.dumps({"error": str(e)}))
        return 2
    except NotRegisteredError as e:
        print(json.dumps({"error": str(e)}))
        return 4
    except Exception as e:  # network failure, dead port, protocol error, etc.
        print(json.dumps({"error": "%s: %s" % (type(e).__name__, e)}))
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
