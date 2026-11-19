#!/usr/bin/env python3
"""SessionStart hook: give every main session the guide to choosing and reusing agents.

SessionStart fires for main conversations only (new, resumed, cleared, compacted, forked), so
background sessions get the guide too; subagents never do. Prints the hookSpecificOutput JSON;
any error ends in exit 0 with nothing printed, so a session start is never held up.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    if event.get("hook_event_name", "SessionStart") != "SessionStart":
        return 0
    import rules
    text = rules.manager_guide(rules.numbers()[0])
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
