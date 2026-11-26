"""The two texts Embers injects, with their numbers taken from config/prices.toml.

- waiting_rule(): for a subagent whose cache lasts 5 minutes (SubagentStart hook).
- manager_guide(): for every main session (SessionStart hook): which agent to use, and when to
  send work back to an existing one.

Every number comes from tools/thresholds.py rule_numbers(), which reads the price file, so a
price or a decision changed there flows into the texts. FALLBACK is used only when the price
file cannot be read (or Python lacks tomllib); a test keeps it equal to the price file.

Each text is a str.format template: a {placeholder} is a key of _rendered(), built from
rule_numbers(). The texts hold no literal brace.
"""
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

FALLBACK = {
    "ttl5_s": 300,
    "ttl1_s": 3600,
    "write_over_read": 25.0,
    "warm_5m_s": 240,
    "warm_1h_s": 3540,
    "chunk_s": 170,
    "bash_timeout_ms": 200_000,
    "poll_interval_s": 15,
    "resume_cold_below": 150_000,
    "resume_cold_unique_below": 350_000,
}


def numbers(root=PLUGIN_ROOT):
    """(numbers, from_price_file): the rule numbers, or FALLBACK if the price file is unusable."""
    try:
        tools = str(Path(root) / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import thresholds
        return thresholds.load_rule_numbers(Path(root) / "config" / "prices.toml"), True
    except Exception:
        return dict(FALLBACK), False


def _num(value):
    """A plain number: an int as written, a float without noise (12.5 stays 12.5, 25.0 is 25)."""
    return str(value) if isinstance(value, int) else f"{value:g}"


def _lifetime(seconds):
    """A cache lifetime in hours or minutes: "1 hour", "5 minutes"."""
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{seconds / 60:g} minutes"


def _duration(seconds):
    """A wait: in seconds below 10 minutes ("240 seconds"), else as a lifetime ("59 minutes")."""
    return f"{seconds:g} seconds" if seconds < 600 else _lifetime(seconds)


def _adjective(seconds):
    """A lifetime before a noun: "5-minute", "1-hour" (as in "a 1-hour cache")."""
    if seconds % 3600 == 0:
        return f"{seconds // 3600}-hour"
    if seconds % 60 == 0:
        return f"{seconds // 60}-minute"
    return f"{seconds:g}-second"


def _rendered(n):
    """The rule numbers as the texts quote them."""
    return {
        "ttl5_s": _lifetime(n["ttl5_s"]),
        "ttl1_s": _lifetime(n["ttl1_s"]),
        "ttl5_adj": _adjective(n["ttl5_s"]),
        "ttl1_adj": _adjective(n["ttl1_s"]),
        "warm_5m_s": _duration(n["warm_5m_s"]),
        "warm_1h_s": _duration(n["warm_1h_s"]),
        "resume_cold_below": f"{n['resume_cold_below']:,}",
        "resume_cold_unique_below": f"{n['resume_cold_unique_below']:,}",
        **{key: _num(n[key]) for key in (
            "write_over_read", "chunk_s", "bash_timeout_ms", "poll_interval_s")},
    }


# The first line's exit is for 1-hour agents the hook misjudged: agents/relay.md states its own
# lifetime in the same words, and the README suggests other 1-hour agents do the same.
WAITING_RULE = """\
Embers: waiting without losing your prompt cache. If your own instructions say your prompt cache lasts {ttl1_s}, ignore this rule.

- A command that may take longer than {chunk_s} seconds: start it in the background (run_in_background, or nohup to a log).
- Wait for it in Bash calls of at most {chunk_s} seconds, one per response, with the Bash timeout set to {bash_timeout_ms}:
  `timeout {chunk_s} bash -c 'until <done-condition>; do sleep {poll_interval_s}; done'; echo rc=$?`
- rc=124: write one short progress line from `tail` of the log, then wait again. rc=0: done. Other: fix the wait command.
- Keep waiting this way however long the job runs.
- Never make one long blocking call, and never end your turn to wait for a job."""


MANAGER_GUIDE = """\
Embers: agents and prompt cache.

Why: an agent's prompt cache expires {ttl5_s} after its last request ({ttl1_s} for embers:relay and background sessions). A message sent after that makes the agent reprocess its whole context, at about {write_over_read} times the cost of a message while the cache is warm.

Choosing an agent:
- Long project work that edits files, or must outlive this session: a background session.
- A one-shot task: a subagent ({ttl5_adj} cache).
- Work you plan to send back to the same agent later than {warm_5m_s} after it finishes, such as review rounds: embers:relay ({ttl1_adj} cache). Not for work that only might come back.

When a subagent finishes and you may send it more work: run `date` and keep the time.

Sending more work to a finished subagent:
- Within {warm_5m_s} of its finish ({warm_1h_s} for embers:relay): resume it.
- Later: resume it only if its subagent_tokens is under {resume_cold_below}, or under {resume_cold_unique_below} when it holds work not saved anywhere else.
- Otherwise: start a fresh agent with a brief that points to the files of the earlier work.
- Finish time unknown: treat the cache as cold. No subagent_tokens figure: treat it as over {resume_cold_below}."""


def waiting_rule(n):
    return WAITING_RULE.format(**_rendered(n))


def manager_guide(n):
    return MANAGER_GUIDE.format(**_rendered(n))
