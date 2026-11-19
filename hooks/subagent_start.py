#!/usr/bin/env python3
"""SubagentStart hook: inject the waiting rule into a subagent whose cache lasts 5 minutes.

Reads the hook input JSON on stdin; prints the hookSpecificOutput JSON, or nothing for a
1-hour agent. It never blocks a subagent: every error ends in exit 0, and an error before the
decision injects the rule (a missing rule costs more than an extra one).
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    if event.get("hook_event_name", "SubagentStart") != "SubagentStart":
        return 0
    agent_type = event.get("agent_type") or ""
    try:
        import ttl
        root = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(HERE.parent)
        lifetime, reason = ttl.effective_ttl(agent_type, cwd=event.get("cwd"), own_root=root)
    except Exception as e:  # unknown counts as 5 minutes
        lifetime, reason = "5m", f"unknown: {type(e).__name__}"
    sys.stderr.write(f"embers: {agent_type or '?'} cache {lifetime} ({reason})\n")
    if lifetime == "1h":
        return 0
    try:
        import rules
        text = rules.waiting_rule(rules.numbers()[0])
    except Exception:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                             "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
