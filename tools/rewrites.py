#!/usr/bin/env python3
"""Count the cold cache rewrites in Claude Code's subagent transcripts, and price them.

Reads every `<project>/<session>/subagents/agent-*.jsonl` under the transcript root, with the
`.meta.json` beside each, and reports for a date window, or for two windows split at a date:
subagents seen, cold rewrites with their gaps and causes, what they cost at list prices from
config/prices.toml, the cache cost of every subagent request for comparison, and how the
`embers:relay` agent was used. Standard library only; reads the transcripts, writes nothing.

    python3 tools/rewrites.py --since 2026-11-01                     # one window
    python3 tools/rewrites.py --since 2026-11-01 --split 2026-11-15  # before and after
    python3 tools/rewrites.py --since 2026-11-01 --list              # every rewrite, one line each
    python3 tools/rewrites.py --since 2026-11-01 --detail            # and what happened before it
    python3 tools/rewrites.py --min-write 50000 --json

A cold rewrite is a request, not the agent's first, that writes at least --min-write tokens to
the cache (default 100,000) and reads at least --min-write fewer than the previous request had
cached: the cache it should have found was gone, and it wrote the context again.
"""
import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thresholds  # noqa: E402

MIN_WRITE = 100_000
RELAY = "embers:relay"
# The two cache lifetimes; main() takes them from config/prices.toml ([ttl]).
FIVE_MINUTES_S = 300
ONE_HOUR_S = 3600

# Why a request found its cache gone, as far as the transcript shows. Checked in this order:
# the gap from the previous request's start passed the agent's cache lifetime, and that request
# ran longer than the agent then sat idle: the request itself ate the lifetime, which counts
# from its start;
LONG_REQUEST = "long request"
# otherwise, by the last tool result or message to arrive before the request (waited_on):
TOOL_WAIT = "tool wait"          # a tool result: the agent waited on a tool
RESUME = "resume"                # a message: the agent's manager sent it more work
NOTIFICATION = "notification"    # a system notification, such as a background job's notice
OTHER = "other"                  # none of these
CAUSES = (TOOL_WAIT, RESUME, NOTIFICATION, LONG_REQUEST, OTHER)
# Leading tags of a message that Claude Code itself sends, not the manager.
NOTIFICATION_TAGS = ("[SYSTEM NOTIFICATION", "<task-notification")
# Bracketed starts that are Claude Code's own wording, printed as they are. Any other message
# that starts with a bracket may be the manager's prompt, and prints as BRACKET.
CLAUDE_CODE_BRACKETS = ("[SYSTEM NOTIFICATION", "[Request interrupted")
BRACKET = "[...]"
# A tool result and a message this close together arrived together.
TIE_S = 2.0


def default_root():
    """Claude Code keeps transcripts under its config directory, ~/.claude by default."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def parse_time(value):
    """An ISO timestamp or date as an aware UTC datetime; a date or naive time counts as UTC."""
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def model_key(name):
    """'claude-opus-5-5' or 'claude-opus-5-5-20261101' or 'claude-opus-5-5[1m]' -> 'opus-5-5'."""
    key = re.sub(r"\[.*\]$", "", name or "")
    key = re.sub(r"-\d{8}$", "", key)
    return key[len("claude-"):] if key.startswith("claude-") else key


# --- reading one agent ---------------------------------------------------------------------------

def _new_pending():
    return {"request_at": None, "last_at": None, "events": []}


def _new_request(rid, line, ts, pending):
    return {
        "id": rid, "model": line["message"].get("model") or "",
        "start": pending["request_at"] or pending["last_at"] or ts, "end": ts,
        "input": 0, "read": 0, "write_5m": 0, "write_1h": 0, "output": 0,
        "stop_reason": None, "tool_use": False, "tools": [],
        "events": pending["events"],
    }


def _tool_call(block):
    """What --detail prints of a tool call: its name and, for a call that can run in the
    background, whether it did and its timeout. Never the command or the prompt."""
    call = {"name": block.get("name") or "?"}
    args = block.get("input") if isinstance(block.get("input"), dict) else {}
    if "run_in_background" in args:
        call["background"] = bool(args["run_in_background"])
    if isinstance(args.get("timeout"), (int, float)):
        call["timeout_s"] = args["timeout"] / 1000
    return call


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _tag(text):
    """The leading tag of a message, and nothing else of it: a whole XML-like tag
    ('<task-notification>'), a bracketed start in Claude Code's own wording
    ('[SYSTEM NOTIFICATION - NOT USER INPUT]'), BRACKET for any other bracketed start, or
    'text'."""
    text = (text or "").lstrip()
    m = re.match(r"<[A-Za-z][\w-]{0,40}>", text)
    if m:
        return m.group(0)
    m = re.match(r"\[[A-Za-z][^\]]{0,40}\]", text)
    if m:
        return m.group(0) if text.startswith(CLAUDE_CODE_BRACKETS) else BRACKET
    return "text"


def _is_notification(text):
    """Whether a message starts as Claude Code's own notifications do; read from the raw text,
    so it does not depend on what _tag keeps."""
    return (text or "").lstrip().startswith(NOTIFICATION_TAGS)


def _apply_usage(req, line, ts, tool_names):
    """Each content block of a response is its own line, all carrying the request's usage; the
    last line wins, and output takes the largest figure seen."""
    msg = line["message"]
    usage = msg.get("usage") or {}
    if ts:
        req["end"] = ts if req["end"] is None else max(req["end"], ts)
    req["input"] = int(usage.get("input_tokens") or 0)
    req["read"] = int(usage.get("cache_read_input_tokens") or 0)
    write = int(usage.get("cache_creation_input_tokens") or 0)
    split = usage.get("cache_creation") or {}
    w1 = int(split.get("ephemeral_1h_input_tokens") or 0)
    w5 = split.get("ephemeral_5m_input_tokens")
    # Without the per-lifetime split, a write counts as 5 minutes, the subagent default.
    req["write_1h"] = w1
    req["write_5m"] = int(w5) if w5 is not None else max(write - w1, 0)
    req["output"] = max(req["output"], int(usage.get("output_tokens") or 0))
    if msg.get("stop_reason"):
        req["stop_reason"] = msg["stop_reason"]
    content = msg.get("content")
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            req["tool_use"] = True
            req["tools"].append(_tool_call(b))
            if b.get("id"):
                tool_names[b["id"]] = req["tools"][-1]["name"]


def read_agent(path):
    """One subagent transcript: its id, type and requests in order. Unreadable lines are
    skipped and counted in bad_lines; an unreadable file gives an agent with no requests,
    marked unreadable."""
    path = Path(path)
    agent_id = path.stem[len("agent-"):] if path.stem.startswith("agent-") else path.stem
    agent = {"id": agent_id, "path": str(path), "type": None, "requests": [], "bad_lines": 0,
             "unreadable": False}
    meta = path.with_name(path.stem + ".meta.json")
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("agentType"), str):
            agent["type"] = data["agentType"]
    except (OSError, ValueError):
        pass

    by_id, tool_names = {}, {}
    pending = _new_pending()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        agent["unreadable"] = True
        return agent
    for raw in lines:
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            agent["bad_lines"] += 1
            continue
        if not isinstance(line, dict):
            agent["bad_lines"] += 1
            continue
        kind = line.get("type")
        ts = parse_time(line.get("timestamp"))
        if kind == "assistant" and isinstance(line.get("message"), dict):
            msg = line["message"]
            if msg.get("model") == "<synthetic>" or not isinstance(msg.get("usage"), dict):
                pending["events"].append({"at": ts, "kind": "synthetic assistant",
                                          "tag": _tag(_text_of(msg.get("content")))})
                continue
            rid = line.get("requestId") or msg.get("id") or line.get("uuid")
            req = by_id.get(rid)
            if req is None:
                req = _new_request(rid, line, ts, pending)
                by_id[rid] = req
                agent["requests"].append(req)
                pending = _new_pending()
            _apply_usage(req, line, ts, tool_names)
            continue
        if kind == "api-request" and ts:
            # Written when the request is sent: the start of the cache's clock.
            pending["request_at"] = ts
            continue
        if kind in ("api-request-shape", "api-request-blob"):
            continue
        if ts and kind in ("user", "attachment", "system"):
            pending["last_at"] = ts
        if kind == "user" and isinstance(line.get("message"), dict):
            content = line["message"].get("content")
            blocks = content if isinstance(content, list) else [content]
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    text = _text_of(b.get("content"))
                    extra = line.get("toolUseResult")
                    pending["events"].append({
                        "at": ts, "kind": "tool result",
                        "tool": tool_names.get(b.get("tool_use_id"), "?"),
                        "error": bool(b.get("is_error")),
                        "interrupted": "interrupt" in text.lower()
                        or (isinstance(extra, dict) and extra.get("interrupted") is True),
                        # the field names Claude Code stores beside the result, never values
                        "fields": sorted(extra) if isinstance(extra, dict) else
                        type(extra).__name__})
                elif isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text"):
                    text = b if isinstance(b, str) else b.get("text")
                    pending["events"].append({
                        "at": ts, "kind": "meta message" if line.get("isMeta") else "message",
                        "tag": _tag(text), "notification": _is_notification(text)})
        elif kind is not None:
            sub = line.get("subtype")
            att = line.get("attachment")
            if isinstance(att, dict) and isinstance(att.get("type"), str):
                sub = att["type"]
            pending["events"].append({"at": ts, "kind": kind if not sub else f"{kind}:{sub}"})
    return agent


def find_transcripts(root):
    return sorted(Path(root).glob("**/subagents/agent-*.jsonl"))


# --- what each request was -----------------------------------------------------------------------

def written(req):
    return req["write_5m"] + req["write_1h"]


def cached(req):
    """What a request left in the cache: the prefix it read plus the part it wrote."""
    return req["read"] + written(req)


def classify(agent, min_write=MIN_WRITE, lifetimes_s=(FIVE_MINUTES_S, ONE_HOUR_S)):
    """Mark each request of an agent: is it a cold rewrite, its gap, its cause, and how many
    of its tokens it wrote again.

    gap_s is from the previous request's start to this one's (what the cache's clock sees);
    idle_s is from the previous response's end to this request's start. lifetime_s is the
    agent's cache lifetime: the second of lifetimes_s once any of its requests so far has
    written to the 1-hour cache, the first otherwise. rewritten is the part of a rewrite's
    write that the previous request had cached, the tokens a warm request would have read;
    the rest is the new tail, which a warm request writes too.
    """
    prev = None
    one_hour = False
    for req in agent["requests"]:
        req["rewrite"] = False
        req["rewritten"] = 0
        req["large_write"] = written(req) >= min_write and prev is not None
        req["gap_s"] = req["idle_s"] = None
        req["lifetime_s"] = lifetimes_s[1] if one_hour else lifetimes_s[0]
        req["cause"] = None
        req["prev"] = None
        if prev is not None:
            req["prev"] = {"tools": prev["tools"], "stop_reason": prev["stop_reason"],
                           "end": prev["end"], "output": prev["output"],
                           "ran_s": (prev["end"] - prev["start"]).total_seconds()
                           if prev["end"] and prev["start"] else None}
            if req["start"] and prev["start"]:
                req["gap_s"] = (req["start"] - prev["start"]).total_seconds()
            if req["start"] and prev["end"]:
                req["idle_s"] = max((req["start"] - prev["end"]).total_seconds(), 0.0)
            missed = cached(prev) - req["read"]
            req["rewrite"] = written(req) >= min_write and missed >= min_write
            if req["rewrite"]:
                req["rewritten"] = min(written(req), missed)
            req["cause"] = cause_of(req, prev)
        one_hour = one_hour or req["write_1h"] > 0
        prev = req
    return agent


def waited_on(events):
    """The tool result or message the agent was waiting for: the last of them to arrive. A
    tool result wins over a message that came with it (within TIE_S), such as a reminder that
    Claude Code adds beside the result."""
    last = None
    for e in events:
        if e["kind"] not in ("tool result", "message", "meta message") or e.get("at") is None:
            continue
        if last is None or (e["at"] - last["at"]).total_seconds() > TIE_S:
            last = e
        elif e["kind"] == "tool result":
            last = e
    return last


def cause_of(req, prev):
    """Why the cache was no longer there, as far as the transcript shows (see CAUSES). A long
    request needs the gap to have passed the agent's lifetime: under it, the previous request
    did not outlive its cache, whatever its length."""
    if (req["idle_s"] is not None and req["gap_s"] is not None
            and req["gap_s"] >= req["lifetime_s"] and prev["start"] and prev["end"]):
        ran_s = (prev["end"] - prev["start"]).total_seconds()
        if ran_s > req["idle_s"]:
            return LONG_REQUEST
    last = waited_on(req["events"])
    if last is None:
        return OTHER
    if last["kind"] == "tool result":
        return TOOL_WAIT
    if last.get("notification"):
        return NOTIFICATION
    return RESUME


# --- prices --------------------------------------------------------------------------------------

class Prices:
    """List prices from config/prices.toml, per model; an unknown model is priced at the price
    file's default model and recorded."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.default = cfg["model"]
        self.unknown = set()

    def model(self, name):
        key = model_key(name)
        if key in self.cfg["models"]:
            return key
        self.unknown.add(name or "(none)")
        return self.default

    def rates(self, name):
        m = self.cfg["multipliers"]
        model = self.cfg["models"][self.model(name)]
        return {"w5": m["write_5m"], "w1": m["write_1h"], "r": model["read"], "o": m["output"],
                "usd": model["base_usd_per_mtok"] / 1e6}

    def input_cost(self, req):
        """USD for the prompt side of a request: uncached input, cache reads, cache writes."""
        p = self.rates(req["model"])
        return p["usd"] * (req["input"] + p["r"] * req["read"]
                           + p["w5"] * req["write_5m"] + p["w1"] * req["write_1h"])

    def output_cost(self, req):
        p = self.rates(req["model"])
        return p["usd"] * p["o"] * req["output"]

    def _rewritten_share(self, req):
        """The rewritten tokens' share of the request's write. Its 5-minute and 1-hour parts
        are not told apart in the transcript, so the rewritten tokens take the write's mix."""
        total = written(req)
        return req.get("rewritten", 0) / total if total else 0.0

    def rewrite_cost(self, req):
        """USD for writing a rewrite's rewritten tokens, the new tail left out."""
        p = self.rates(req["model"])
        return self._rewritten_share(req) * p["usd"] * (
            p["w5"] * req["write_5m"] + p["w1"] * req["write_1h"])

    def avoidable(self, req):
        """USD a rewrite would have saved had it read its rewritten tokens instead of writing
        them; the new tail costs the same either way."""
        p = self.rates(req["model"])
        return self._rewritten_share(req) * p["usd"] * (
            (p["w5"] - p["r"]) * req["write_5m"] + (p["w1"] - p["r"]) * req["write_1h"])

    def breakeven_share(self):
        """f*: the share of writes that are rewrites at which a 1-hour cache breaks even, for
        the default model (docs/math.md, section 2)."""
        p = self.rates(self.default)
        return thresholds.rewrite_share(p["w1"], p["w5"], p["r"])


# --- one window ----------------------------------------------------------------------------------

def gap_detail(req):
    """What happened between a request and the one before it, for --detail: the previous
    request's tool calls and stop reason, whether it ended the agent's turn, and each line in the
    gap with its time in seconds after the previous response ended."""
    prev = req.get("prev") or {}
    end = prev.get("end")
    events = []
    for e in req.get("events", []):
        e = dict(e)
        at = e.pop("at", None)
        e["after_s"] = round((at - end).total_seconds(), 1) if at and end else None
        events.append(e)
    return {"prev_tools": prev.get("tools", []), "prev_stop_reason": prev.get("stop_reason"),
            "prev_ran_s": prev.get("ran_s"), "prev_output_tokens": prev.get("output"),
            "prev_ended_turn": bool(prev) and not prev.get("tools")
            and prev.get("stop_reason") != "tool_use",
            "events": events}


def in_window(t, since, until):
    return t is not None and (since is None or t >= since) and (until is None or t < until)


def summarize(agents, prices, since=None, until=None, relay_type=RELAY):
    """Every figure the report prints, for requests that started in [since, until)."""
    s = {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "agents": 0, "requests": 0,
        "rewrites": 0, "rewrite_tokens": 0, "rewrite_usd": 0.0, "avoidable_usd": 0.0,
        "by_cause": {c: 0 for c in CAUSES}, "under_5_minutes": 0,
        "gap_min_s": None, "gap_median_s": None, "gap_max_s": None,
        "large_writes_not_cold": 0,
        "write_tokens": 0, "write_1h_tokens": 0, "read_tokens": 0, "input_tokens": 0,
        "output_tokens": 0, "input_usd": 0.0, "output_usd": 0.0,
        "relay_agents": 0, "relay_requests": 0, "relay_requests_writing": 0,
        "relay_requests_writing_1h": 0, "relay_rewrites": 0,
        "agents_writing_1h": 0, "agents_untyped": 0,
        "rewrite_list": [],
    }
    gaps = []
    for agent in agents:
        reqs = [r for r in agent["requests"] if in_window(r["start"], since, until)]
        if not reqs:
            continue
        s["agents"] += 1
        is_relay = agent["type"] == relay_type
        s["relay_agents"] += is_relay
        s["agents_untyped"] += agent["type"] is None
        s["agents_writing_1h"] += any(r["write_1h"] > 0 for r in reqs)
        for r in reqs:
            s["requests"] += 1
            s["write_tokens"] += written(r)
            s["write_1h_tokens"] += r["write_1h"]
            s["read_tokens"] += r["read"]
            s["input_tokens"] += r["input"]
            s["output_tokens"] += r["output"]
            s["input_usd"] += prices.input_cost(r)
            s["output_usd"] += prices.output_cost(r)
            if is_relay:
                s["relay_requests"] += 1
                s["relay_requests_writing"] += written(r) > 0
                s["relay_requests_writing_1h"] += r["write_1h"] > 0
            if r["large_write"] and not r["rewrite"]:
                s["large_writes_not_cold"] += 1
            if not r["rewrite"]:
                continue
            s["rewrites"] += 1
            s["relay_rewrites"] += is_relay
            s["rewrite_tokens"] += r["rewritten"]
            s["rewrite_usd"] += prices.rewrite_cost(r)
            s["avoidable_usd"] += prices.avoidable(r)
            s["by_cause"][r["cause"]] += 1
            if r["gap_s"] is not None:
                gaps.append(r["gap_s"])
                s["under_5_minutes"] += r["gap_s"] < FIVE_MINUTES_S
            s["rewrite_list"].append({
                "start": r["start"].isoformat() if r["start"] else None,
                "agent": agent["id"], "agent_type": agent["type"], "model": r["model"],
                "tokens": r["rewritten"], "written": written(r),
                "gap_s": r["gap_s"], "idle_s": r["idle_s"],
                "cause": r["cause"], **gap_detail(r)})
    if gaps:
        s["gap_min_s"], s["gap_max_s"] = min(gaps), max(gaps)
        s["gap_median_s"] = statistics.median(gaps)
    s["rewrite_share_of_writes"] = s["rewrite_tokens"] / s["write_tokens"] if s["write_tokens"] else None
    s["rewrite_share_of_input_usd"] = s["rewrite_usd"] / s["input_usd"] if s["input_usd"] else None
    s["rewrite_list"].sort(key=lambda x: x["start"] or "")
    return s


# --- printing ------------------------------------------------------------------------------------

def _minutes(seconds):
    return "-" if seconds is None else f"{seconds / 60:,.1f} min"


def _share(x):
    return "-" if x is None else f"{x:.1%}"


ROWS = [
    ("subagents seen", lambda s: f"{s['agents']:,}"),
    ("requests", lambda s: f"{s['requests']:,}"),
    ("cold rewrites", lambda s: f"{s['rewrites']:,}"),
    ("  after a tool wait", lambda s: f"{s['by_cause'][TOOL_WAIT]:,}"),
    ("  on a resume", lambda s: f"{s['by_cause'][RESUME]:,}"),
    ("  on a system notification", lambda s: f"{s['by_cause'][NOTIFICATION]:,}"),
    ("  after a long request", lambda s: f"{s['by_cause'][LONG_REQUEST]:,}"),
    ("  other or unknown", lambda s: f"{s['by_cause'][OTHER]:,}"),
    ("  gap under 5 minutes", lambda s: f"{s['under_5_minutes']:,}"),
    ("gap before a rewrite, min", lambda s: _minutes(s["gap_min_s"])),
    ("gap before a rewrite, median", lambda s: _minutes(s["gap_median_s"])),
    ("gap before a rewrite, max", lambda s: _minutes(s["gap_max_s"])),
    ("tokens rewritten", lambda s: f"{s['rewrite_tokens']:,}"),
    ("rewrites at list price", lambda s: f"${s['rewrite_usd']:,.2f}"),
    ("  avoidable had each been warm", lambda s: f"${s['avoidable_usd']:,.2f}"),
    ("large writes, not cold", lambda s: f"{s['large_writes_not_cold']:,}"),
    ("cache writes, all requests", lambda s: f"{s['write_tokens']:,}"),
    ("  of which 1-hour", lambda s: f"{s['write_1h_tokens']:,}"),
    ("cache reads, all requests", lambda s: f"{s['read_tokens']:,}"),
    ("prompt cost, all requests", lambda s: f"${s['input_usd']:,.2f}"),
    ("output cost, all requests", lambda s: f"${s['output_usd']:,.2f}"),
    ("rewrites, share of writes", lambda s: _share(s["rewrite_share_of_writes"])),
    ("rewrites, share of prompt cost", lambda s: _share(s["rewrite_share_of_input_usd"])),
    ("relay agents", lambda s: f"{s['relay_agents']:,}"),
    ("  relay requests", lambda s: f"{s['relay_requests']:,}"),
    ("  writing to the cache", lambda s: f"{s['relay_requests_writing']:,}"),
    ("  writing to the 1-hour cache", lambda s: f"{s['relay_requests_writing_1h']:,}"),
    ("  relay rewrites", lambda s: f"{s['relay_rewrites']:,}"),
    ("subagents writing 1-hour", lambda s: f"{s['agents_writing_1h']:,}"),
    ("subagents of unknown type", lambda s: f"{s['agents_untyped']:,}"),
]


def window_label(s):
    since = s["since"][:10] if s["since"] else "start"
    until = s["until"][:10] if s["until"] else "now"
    return f"{since} .. {until}"


def render(windows, min_write, breakeven, unknown_models, listing=False, detail=False,
           unreadable=0):
    out = [f"cold rewrite: a later request writing >= {min_write:,} tokens and reading >= "
           f"{min_write:,} fewer than the previous request had cached"]
    labels = [window_label(s) for s in windows]
    width = max(len(name) for name, _ in ROWS)
    cols = [max(len(label), 14) for label in labels]
    out.append(f"{'':<{width}}  " + "  ".join(f"{l:>{c}}" for l, c in zip(labels, cols)))
    for name, cell in ROWS:
        out.append(f"{name:<{width}}  " + "  ".join(f"{cell(s):>{c}}" for s, c in zip(windows, cols)))
    out.append(f"(a 1-hour cache breaks even when rewrites are {breakeven:.1%} of writes; "
               "until is exclusive; times are UTC)")
    if unknown_models:
        out.append("priced at the default model, not in the price file: "
                   + ", ".join(sorted(unknown_models)))
    if unreadable:
        out.append(f"{unreadable:,} transcript{'s' if unreadable != 1 else ''} could not be "
                   "read, and nothing in them is counted")
    if listing or detail:
        for s in windows:
            out.append("")
            out.append(f"rewrites, {window_label(s)}:")
            for r in s["rewrite_list"]:
                out.append(f"  {r['start'][:19]}  agent {r['agent'][:12]:<12}  "
                           f"{(r['agent_type'] or '?'):<18}  {r['model']:<18}  "
                           f"{r['tokens']:>9,}  gap {_minutes(r['gap_s']):>10}  "
                           f"idle {_minutes(r['idle_s']):>10}  {r['cause']}")
                if detail:
                    out.extend("      " + line for line in detail_lines(r))
    return "\n".join(out)


def _event_text(e):
    if e["kind"] == "tool result":
        flags = [f for f in ("error", "interrupted") if e.get(f)]
        fields = e.get("fields")
        fields = ",".join(fields) if isinstance(fields, list) else fields
        return (f"tool result {e['tool']}" + (f" ({', '.join(flags)})" if flags else "")
                + (f"  [{fields}]" if fields else ""))
    if "tag" in e:
        return f"{e['kind']} {e['tag']}"
    return e["kind"]


def detail_lines(r):
    """The previous request and the gap, one event per line; repeated events are folded."""
    tools = ", ".join(t["name"] + "".join(
        f" {k}={v}" for k, v in t.items() if k != "name") for t in r["prev_tools"]) or "none"
    lines = [f"previous request: ran {_minutes(r['prev_ran_s'])}, "
             f"{r['prev_output_tokens'] or 0:,} output tokens, stop {r['prev_stop_reason']}, "
             f"tool calls: {tools}, ended the turn: {'yes' if r['prev_ended_turn'] else 'no'}"]
    folded = []
    for e in r["events"]:
        text = _event_text(e)
        if folded and folded[-1][1] == text:
            folded[-1][2] += 1
            folded[-1][3] = e["after_s"]
        else:
            folded.append([e["after_s"], text, 1, e["after_s"]])
    for first, text, n, last in folded:
        when = "?" if first is None else f"+{first:,.1f} s"
        more = f"  x{n} (last +{last:,.1f} s)" if n > 1 and last is not None else ""
        lines.append(f"{when:>14}  {text}{more}")
    return lines


# --- the command ---------------------------------------------------------------------------------

def _date_arg(value):
    t = parse_time(value)
    if t is None:
        raise argparse.ArgumentTypeError(f"not a date or ISO time: {value}")
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", default=None,
                    help="transcript root (default: $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects)")
    ap.add_argument("--since", type=_date_arg, help="first day counted (UTC), e.g. 2026-11-05")
    ap.add_argument("--until", type=_date_arg, help="first day NOT counted (UTC)")
    ap.add_argument("--split", type=_date_arg,
                    help="print two windows side by side: since..split and split..until")
    ap.add_argument("--min-write", type=int, default=MIN_WRITE,
                    help=f"tokens a request must write, and miss, to count (default {MIN_WRITE:,})")
    ap.add_argument("--relay-type", default=RELAY, help=f"agent type of the relay (default {RELAY})")
    ap.add_argument("--prices", default=str(thresholds.PRICES), help="price file")
    ap.add_argument("--list", action="store_true", help="also print every rewrite")
    ap.add_argument("--detail", action="store_true",
                    help="like --list, and under each rewrite the previous request's tool calls\n"
                         "and every line in the gap (kinds, tool names and tags only, no content)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)
    if args.min_write <= 0:
        ap.error("--min-write must be positive")
    if args.split and ((args.since and args.split <= args.since)
                       or (args.until and args.split >= args.until)):
        ap.error("--split must fall between --since and --until")

    root = Path(args.root) if args.root else default_root()
    paths = find_transcripts(root)
    if not paths:
        print(f"no subagent transcripts under {root}", file=sys.stderr)
    cfg = thresholds.load(args.prices)
    prices = Prices(cfg)
    lifetimes = (cfg["ttl"]["five_minutes_s"], cfg["ttl"]["one_hour_s"])
    agents = [classify(read_agent(p), args.min_write, lifetimes) for p in paths]
    unreadable = sum(a["unreadable"] for a in agents)
    bounds = ([(args.since, args.split), (args.split, args.until)] if args.split
              else [(args.since, args.until)])
    windows = [summarize(agents, prices, a, b, args.relay_type) for a, b in bounds]

    if args.json:
        print(json.dumps({"root": str(root), "min_write": args.min_write,
                          "breakeven_share": prices.breakeven_share(),
                          "unknown_models": sorted(prices.unknown),
                          "bad_lines": sum(a["bad_lines"] for a in agents),
                          "unreadable_transcripts": unreadable,
                          "windows": windows}, indent=2))
    else:
        print(render(windows, args.min_write, prices.breakeven_share(), prices.unknown,
                     args.list, args.detail, unreadable))
    return 0


if __name__ == "__main__":
    sys.exit(main())
