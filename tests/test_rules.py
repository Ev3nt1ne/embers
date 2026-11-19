"""The injected texts carry the numbers from the price file, and the hooks behave.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(REPO / "tools"))
import rules  # noqa: E402
import thresholds  # noqa: E402


def copy_with(text_changes):
    """A copy of the plugin's config and tools with the price file edited; returns its root."""
    d = Path(tempfile.mkdtemp())
    shutil.copytree(REPO / "tools", d / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    (d / "config").mkdir()
    text = (REPO / "config" / "prices.toml").read_text()
    for old, new in text_changes:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    (d / "config" / "prices.toml").write_text(text)
    return d


GUIDE_TODAY = """\
Embers: agents and prompt cache.

Why: an agent's prompt cache expires 5 minutes after its last request (1 hour for embers:relay and background sessions). A message sent after that makes the agent reprocess its whole context, at about 25 times the cost of a message while the cache is warm.

Choosing an agent:
- Long project work that edits files, or must outlive this session: a background session.
- A one-shot task: a subagent (5-minute cache).
- Work that may come back to the same agent later than 240 seconds after it finishes: embers:relay (1-hour cache).

When a subagent finishes and you may send it more work: run `date` and keep the time.

Sending more work to a finished subagent:
- Within 240 seconds of its finish (59 minutes for embers:relay): resume it.
- Later: resume it only if its subagent_tokens is under 150,000, or under 350,000 when it holds work not saved anywhere else.
- Otherwise: start a fresh agent with a brief that points to the files of the earlier work.
- Finish time unknown: treat the cache as cold. No subagent_tokens figure: treat it as over 150,000."""

WAITING_TODAY = """\
Embers: waiting without losing your prompt cache. If your own instructions say your prompt cache lasts 1 hour, ignore this rule.

- A command that may take longer than 170 seconds: start it in the background (run_in_background, or nohup to a log).
- Wait for it in Bash calls of at most 170 seconds, with the Bash timeout set to 200000:
  `timeout 170 bash -c 'until <done-condition>; do sleep 15; done'; echo rc=$?`
- rc=124: write one short progress line from `tail` of the log, then wait again. rc=0: done. Other: fix the wait command.
- Keep waiting this way however long the job runs.
- Never make one long blocking call, and never end your turn to wait for a job."""


class Texts(unittest.TestCase):
    def test_numbers_come_from_the_price_file(self):
        n, from_file = rules.numbers()
        self.assertTrue(from_file)
        self.assertEqual(n, thresholds.load_rule_numbers())

    def test_fallback_matches_the_price_file(self):
        # FALLBACK is used only when the price file cannot be read; it must not drift
        self.assertEqual(rules.FALLBACK, thresholds.load_rule_numbers())

    def test_texts_word_for_word_at_todays_numbers(self):
        # the owner's final texts (2026-11-19); FALLBACK holds today's numbers (test above)
        self.assertEqual(rules.manager_guide(rules.FALLBACK), GUIDE_TODAY)
        self.assertEqual(rules.waiting_rule(rules.FALLBACK), WAITING_TODAY)

    def test_waiting_rule_quotes_the_numbers(self):
        n = rules.numbers()[0]
        text = rules.waiting_rule(n)
        chunk = n["chunk_s"]
        for piece in (f"longer than {chunk} seconds", f"at most {chunk} seconds",
                      f"Bash timeout set to {n['bash_timeout_ms']}:",
                      f"`timeout {chunk} bash -c 'until <done-condition>; "
                      f"do sleep {n['poll_interval_s']}; done'; echo rc=$?`"):
            self.assertIn(piece, text)

    def test_manager_guide_quotes_the_numbers(self):
        n = rules.numbers()[0]
        text = rules.manager_guide(n)
        x, y = f"{n['resume_cold_below']:,}", f"{n['resume_cold_unique_below']:,}"
        for piece in (f"expires {n['ttl5_s'] // 60} minutes after its last request",
                      f"({n['ttl1_s'] // 3600} hour for embers:relay",
                      f"at about {n['write_over_read']:g} times the cost",
                      f"a subagent ({n['ttl5_s'] // 60}-minute cache)",
                      f"later than {n['warm_5m_s']} seconds after it finishes",
                      f"embers:relay ({n['ttl1_s'] // 3600}-hour cache)",
                      f"Within {n['warm_5m_s']} seconds of its finish "
                      f"({n['warm_1h_s'] // 60} minutes for embers:relay)",
                      f"under {x}, or under {y} when", f"treat it as over {x}."):
            self.assertIn(piece, text)

    def test_no_placeholder_is_left(self):
        for n in (rules.numbers()[0], rules.FALLBACK):
            for text in (rules.waiting_rule(n), rules.manager_guide(n)):
                self.assertNotIn("{", text)
                self.assertNotIn("}", text)

    def test_waiting_rule_exit_matches_the_relay(self):
        # the first line holds the 1-hour exit, in the words agents/relay.md states its lifetime;
        # that line must sit in the relay's body, the part the agent reads, not the frontmatter
        lifetime = rules._rendered(rules.numbers()[0])["ttl1_s"]
        self.assertEqual(lifetime, "1 hour")
        first = rules.waiting_rule(rules.numbers()[0]).splitlines()[0]
        self.assertIn(f"If your own instructions say your prompt cache lasts {lifetime}, "
                      "ignore this rule.", first)
        body = (REPO / "agents" / "relay.md").read_text().split("\n---\n", 1)[1]
        self.assertIn(f"Your prompt cache lasts {lifetime}.", body)

    def test_a_change_in_the_price_file_flows_into_the_texts(self):
        root = copy_with([
            ("five_minutes_s = 300", "five_minutes_s = 360"),
            ("chunk_s = 170", "chunk_s = 120"),
            ("safety_margin_s = 60", "safety_margin_s = 90"),
            ("poll_interval_s = 15", "poll_interval_s = 10"),
            ("[models.opus-5-5]\nbase_usd_per_mtok = 4.0\nread = 0.05",
             "[models.opus-5-5]\nbase_usd_per_mtok = 4.0\nread = 0.1"),
            ("resume_cold_below_tokens = 150_000", "resume_cold_below_tokens = 120_000"),
            ("resume_cold_unique_below_tokens = 350_000",
             "resume_cold_unique_below_tokens = 400_000"),
        ])
        try:
            n, from_file = rules.numbers(root)
            self.assertTrue(from_file)
            wait, guide = rules.waiting_rule(n), rules.manager_guide(n)
        finally:
            shutil.rmtree(root)
        self.assertIn("longer than 120 seconds", wait)
        self.assertIn("timeout 120 bash", wait)
        self.assertIn("sleep 10;", wait)
        self.assertIn("Bash timeout set to 150000:", wait)
        self.assertIn("expires 6 minutes after", guide)
        self.assertIn("a subagent (6-minute cache)", guide)
        self.assertIn("about 12.5 times the cost", guide)
        self.assertIn("later than 270 seconds after", guide)
        self.assertIn("(58.5 minutes for embers:relay)", guide)
        self.assertIn("under 120,000, or under 400,000 when", guide)
        self.assertIn("treat it as over 120,000.", guide)
        self.assertNotIn("150,000", guide)

    def test_unreadable_price_file_falls_back(self):
        d = Path(tempfile.mkdtemp())
        try:
            n, from_file = rules.numbers(d)
        finally:
            shutil.rmtree(d)
        self.assertFalse(from_file)
        self.assertEqual(n, rules.FALLBACK)


# Runs a hook script as its own process, with ttl.managed_dirs() pointed at a fake directory,
# so the host's managed settings (/etc/claude-code and the like) never reach a test.
# argv: the fake managed directory, the script.
RUN_WITH_FAKE_MANAGED = """
import runpy, sys
from pathlib import Path
managed, script = sys.argv[1], sys.argv[2]
sys.path.insert(0, str(Path(script).parent))
import ttl
ttl.managed_dirs = lambda: [Path(managed)]
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
"""

AGENT_1H = "---\nname: {}\ndescription: d\nexperimental:\n  cacheTtl: 1h\n---\nbody\n"


class Hooks(unittest.TestCase):
    """The hook scripts as Claude Code runs them: JSON on stdin, JSON or nothing on stdout.

    Each run gets a fake home, config directory, managed-settings directory and project, and an
    environment built from nothing but PATH: no setting, plugin cache or variable of the machine
    running the tests can change a result.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = root = Path(self.tmp.name)
        self.home, self.proj = root / "home" / ".claude", root / "proj"
        self.managed = root / "managed"
        self.managed.mkdir()
        (self.proj / ".git").mkdir(parents=True)
        agents = self.home / "agents"
        agents.mkdir(parents=True)
        (agents / "slow.md").write_text(AGENT_1H.format("slow-reviewer"))
        self.env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(root / "home"),
                    "CLAUDE_CONFIG_DIR": str(self.home), "CLAUDE_PROJECT_DIR": str(self.proj),
                    "CLAUDE_PLUGIN_ROOT": str(REPO)}

    def run_hook(self, script, event, stdin=None):
        return subprocess.run(
            [sys.executable, "-I", "-c", RUN_WITH_FAKE_MANAGED, str(self.managed),
             str(REPO / "hooks" / script)],
            input=stdin if stdin is not None else json.dumps(event),
            capture_output=True, text=True, env=self.env, cwd=str(self.root), timeout=30)

    def install_plugin(self, name, manifest, files):
        """A plugin installed from a (fake) GitHub marketplace into the fake config directory."""
        path = self.home / "plugins" / "cache" / "m" / name / "1.0.0"
        for rel, text in {".claude-plugin/plugin.json": json.dumps(manifest), **files}.items():
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_text(text)
        (self.home / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {f"{name}@m": [{"scope": "user", "installPath": str(path)}]}}))
        (self.home / "plugins" / "known_marketplaces.json").write_text(json.dumps(
            {"m": {"source": {"source": "github", "repo": "o/r"}, "installLocation": "/nowhere"}}))

    def subagent(self, agent_type):
        return self.run_hook("subagent_start.py", {
            "session_id": "s", "cwd": str(self.proj), "hook_event_name": "SubagentStart",
            "agent_id": "a1", "agent_type": agent_type})

    def test_five_minute_agent_gets_the_rule(self):
        r = self.subagent("general-purpose")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SubagentStart")
        self.assertIn("timeout 170 ", out["additionalContext"])

    def test_relay_gets_nothing(self):
        r = self.subagent("embers:relay")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_user_one_hour_agent_gets_nothing(self):
        r = self.subagent("slow-reviewer")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_forced_five_minutes_reaches_the_relay(self):
        self.env["FORCE_PROMPT_CACHING_5M"] = "1"
        self.assertIn("additionalContext", self.subagent("embers:relay").stdout)

    def test_managed_setting_is_read_from_the_fake_directory(self):
        (self.managed / "managed-settings.json").write_text(
            json.dumps({"subagentPromptCacheTtl": "5m"}))
        self.assertIn("additionalContext", self.subagent("embers:relay").stdout)

    def test_plugin_agent_listed_through_the_manifest(self):
        self.install_plugin("kit", {"name": "kit", "agents": ["./extra/helper.md"]}, {
            "agents/slow.md": AGENT_1H.format("slow"),
            "extra/helper.md": AGENT_1H.format("helper")})
        for agent in ("kit:slow", "kit:helper"):
            with self.subTest(agent):
                r = self.subagent(agent)
                self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("additionalContext", self.subagent("kit:other").stdout)

    def test_session_start_gets_the_guide(self):
        r = self.run_hook("session_start.py", {"session_id": "s", "cwd": str(self.proj),
                                               "hook_event_name": "SessionStart",
                                               "source": "startup"})
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("embers:relay (1-hour cache)", out["additionalContext"])

    def test_bad_input_never_fails(self):
        for script in ("subagent_start.py", "session_start.py"):
            with self.subTest(script):
                self.assertEqual(self.run_hook(script, None, stdin="not json").returncode, 0)


if __name__ == "__main__":
    unittest.main()
