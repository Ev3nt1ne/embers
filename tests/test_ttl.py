"""The effective-lifetime decision: every precedence case, and what is found on disk.

Run: python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import ttl  # noqa: E402

U = ttl.UNREADABLE
F5 = {"FORCE_PROMPT_CACHING_5M": "1"}
E1 = {"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h"}
E5 = {"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "5m"}
EN = {"ENABLE_PROMPT_CACHING_1H": "1"}

# (case, env, setting, agent cacheTtl, expected lifetime)
TABLE = [
    # 1. FORCE_PROMPT_CACHING_5M beats everything
    ("force beats env 1h", {**F5, **E1}, "1h", "1h", "5m"),
    ("force beats setting and agent", F5, "1h", "1h", "5m"),
    ("force beats ENABLE_1H", {**F5, **EN}, None, None, "5m"),
    ("force as 'true' still read as set", {"FORCE_PROMPT_CACHING_5M": "true"}, None, "1h", "5m"),
    ("force '0' is not set", {"FORCE_PROMPT_CACHING_5M": "0"}, None, "1h", "1h"),
    # 2. the env var
    ("env 1h beats setting 5m", E1, "5m", None, "1h"),
    ("env 5m beats agent 1h", E5, None, "1h", "5m"),
    ("env 5m beats ENABLE_1H", {**E5, **EN}, None, None, "5m"),
    ("env beats an unreadable setting", E1, U, U, "1h"),
    ("env with another value is ignored", {"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "2h"},
     None, "1h", "1h"),
    # 3. the setting
    ("setting 5m beats agent 1h", {}, "5m", "1h", "5m"),
    ("setting 1h on a plain agent", {}, "1h", None, "1h"),
    ("setting beats ENABLE_1H", EN, "5m", None, "5m"),
    ("unreadable setting counts as 5m", {}, U, "1h", "5m"),
    # 4. the agent definition
    ("agent 1h", {}, None, "1h", "1h"),
    ("agent 5m beats ENABLE_1H", EN, None, "5m", "5m"),
    ("unreadable agent counts as 5m", EN, None, U, "5m"),
    # 5. ENABLE_PROMPT_CACHING_1H
    ("ENABLE_1H on a plain agent", EN, None, None, "1h"),
    ("ENABLE_1H only as '1'", {"ENABLE_PROMPT_CACHING_1H": "true"}, None, None, "5m"),
    # 6. default
    ("nothing set", {}, None, None, "5m"),
    ("empty env values are unset", {"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "",
                                    "FORCE_PROMPT_CACHING_5M": ""}, None, None, "5m"),
]


class Decision(unittest.TestCase):
    def test_table(self):
        for case, env, setting, agent, expected in TABLE:
            with self.subTest(case):
                self.assertEqual(ttl.decide(env, setting, agent)[0], expected)

    def test_reasons_name_the_unknown(self):
        self.assertIn("unknown", ttl.decide({}, U, None)[1])
        self.assertIn("unknown", ttl.decide({}, None, U)[1])


class Frontmatter(unittest.TestCase):
    def parse(self, text):
        return ttl.parse_frontmatter(text)

    def test_block_map(self):
        fm = self.parse("---\nname: a\ndescription: x\nexperimental:\n  cacheTtl: 1h\n---\nbody\n")
        self.assertEqual(fm, {"name": "a", "cacheTtl": "1h"})

    def test_flow_map_and_quotes(self):
        fm = self.parse('---\nname: "a"\nexperimental: { cacheTtl: "1h" }\n---\n')
        self.assertEqual(fm, {"name": "a", "cacheTtl": "1h"})

    def test_other_value_is_ignored(self):
        self.assertIsNone(self.parse("---\nname: a\nexperimental:\n  cacheTtl: 2h\n---\n")["cacheTtl"])

    def test_top_level_cachettl_is_not_read(self):
        # the docs: write cacheTtl inside experimental, not at the top level
        self.assertIsNone(self.parse("---\nname: a\ncacheTtl: 1h\n---\n")["cacheTtl"])

    def test_no_frontmatter(self):
        self.assertEqual(self.parse("# notes\n"), {"name": None, "cacheTtl": None})

    def test_broken(self):
        with self.assertRaises(ttl.FrontmatterError):
            self.parse("---\nname: a\nexperimental:\n  cacheTtl: 1h\n")
        with self.assertRaises(ttl.FrontmatterError):
            self.parse("---\nname: a\nexperimental: 1h\n---\n")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


AGENT_1H = "---\nname: {}\ndescription: d\nexperimental:\n  cacheTtl: 1h\n---\nbody\n"
AGENT_PLAIN = "---\nname: {}\ndescription: d\n---\nbody\n"


class OnDisk(unittest.TestCase):
    """effective_ttl() against a fake home, project and plugin tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home" / ".claude"
        self.proj = self.root / "proj"
        (self.proj / ".git").mkdir(parents=True)
        self.env = {"CLAUDE_CONFIG_DIR": str(self.home), "CLAUDE_PROJECT_DIR": str(self.proj)}
        patcher = mock.patch.object(ttl, "managed_dirs", lambda: [self.root / "managed"])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def life(self, agent_type, own_root=None, cwd=None):
        return ttl.effective_ttl(agent_type, self.env, cwd or str(self.proj), own_root)

    def test_user_agent_1h(self):
        write(self.home / "agents" / "sub" / "x.md", AGENT_1H.format("slow"))
        self.assertEqual(self.life("slow")[0], "1h")
        self.assertEqual(self.life("other")[0], "5m")

    def test_project_beats_user(self):
        write(self.home / "agents" / "a.md", AGENT_1H.format("a"))
        write(self.proj / ".claude" / "agents" / "a.md", AGENT_PLAIN.format("a"))
        self.assertEqual(self.life("a")[0], "5m")

    def test_closest_project_dir_wins(self):
        sub = self.proj / "pkg"
        write(self.proj / ".claude" / "agents" / "a.md", AGENT_PLAIN.format("a"))
        write(sub / ".claude" / "agents" / "a.md", AGENT_1H.format("a"))
        self.assertEqual(self.life("a", cwd=str(sub))[0], "1h")

    def test_managed_beats_project(self):
        write(self.root / "managed" / ".claude" / "agents" / "a.md", AGENT_PLAIN.format("a"))
        write(self.proj / ".claude" / "agents" / "a.md", AGENT_1H.format("a"))
        self.assertEqual(self.life("a")[0], "5m")

    def test_name_not_filename(self):
        write(self.home / "agents" / "slow.md", AGENT_1H.format("renamed"))
        self.assertEqual(self.life("slow")[0], "5m")
        self.assertEqual(self.life("renamed")[0], "1h")

    def test_unreadable_agent_file_counts_as_5m(self):
        write(self.home / "agents" / "a.md", "---\nname: a\nexperimental:\n  cacheTtl: 1h\n")
        self.assertEqual(self.life("a"), ("5m", "unknown: the agent definition could not be read"))
        # an unrelated broken file does not make other agents unknown
        write(self.home / "agents" / "b.md", AGENT_1H.format("b"))
        self.assertEqual(self.life("b")[0], "1h")

    def test_setting_overrides_agent(self):
        write(self.home / "agents" / "a.md", AGENT_1H.format("a"))
        write(self.home / "settings.json", json.dumps({"subagentPromptCacheTtl": "5m"}))
        self.assertEqual(self.life("a"), ("5m", "setting subagentPromptCacheTtl"))

    def test_settings_precedence(self):
        write(self.home / "settings.json", json.dumps({"subagentPromptCacheTtl": "1h"}))
        write(self.proj / ".claude" / "settings.json", json.dumps({"subagentPromptCacheTtl": "5m"}))
        self.assertEqual(self.life("x")[0], "5m")
        write(self.proj / ".claude" / "settings.local.json",
              json.dumps({"subagentPromptCacheTtl": "1h"}))
        self.assertEqual(self.life("x")[0], "1h")
        write(self.root / "managed" / "managed-settings.json",
              json.dumps({"subagentPromptCacheTtl": "5m"}))
        self.assertEqual(self.life("x")[0], "5m")

    def test_setting_with_another_value_falls_through(self):
        write(self.home / "agents" / "a.md", AGENT_1H.format("a"))
        write(self.home / "settings.json", json.dumps({"subagentPromptCacheTtl": "10m"}))
        self.assertEqual(self.life("a")[0], "1h")

    def test_unreadable_settings_counts_as_5m(self):
        write(self.home / "agents" / "a.md", AGENT_1H.format("a"))
        write(self.proj / ".claude" / "settings.json", "{ not json")
        self.assertEqual(self.life("a"), ("5m", "unknown: a settings file could not be read"))

    def test_env_var_beats_settings_file(self):
        write(self.home / "settings.json", json.dumps({"subagentPromptCacheTtl": "5m"}))
        self.env["CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"] = "1h"
        self.assertEqual(self.life("x")[0], "1h")

    def test_own_plugin_agent(self):
        plugin = self.root / "embers"
        write(plugin / ".claude-plugin" / "plugin.json", json.dumps({"name": "embers"}))
        write(plugin / "agents" / "relay.md", AGENT_1H.format("relay"))
        self.assertEqual(self.life("embers:relay", own_root=str(plugin))[0], "1h")
        self.assertEqual(self.life("embers:other", own_root=str(plugin))[0], "5m")

    def test_the_real_relay_agent(self):
        repo = Path(__file__).resolve().parent.parent
        self.assertEqual(self.life("embers:relay", own_root=str(repo))[0], "1h")

    def test_installed_plugin_and_subfolders(self):
        cache = self.home / "plugins" / "cache" / "m" / "tools" / "1.0.0"
        write(cache / "agents" / "review" / "deep.md", AGENT_1H.format("deep"))
        write(self.home / "plugins" / "installed_plugins.json", json.dumps(
            {"version": 2, "plugins": {"tools@m": [{"scope": "user", "installPath": str(cache)}]}}))
        write(self.home / "plugins" / "known_marketplaces.json", json.dumps(
            {"m": {"source": {"source": "github", "repo": "o/r"}, "installLocation": "/nowhere"}}))
        self.assertEqual(self.life("tools:review:deep")[0], "1h")
        self.assertEqual(self.life("tools:deep")[0], "5m")

    def test_local_marketplace_loads_in_place(self):
        market = self.root / "market"
        write(market / ".claude-plugin" / "marketplace.json", json.dumps(
            {"name": "local", "metadata": {"pluginRoot": "./plugins"},
             "plugins": [{"name": "kit", "source": "kit"}]}))
        write(market / "plugins" / "kit" / "agents" / "a.md", AGENT_1H.format("a"))
        stale = self.home / "plugins" / "cache" / "local" / "kit" / "0.1.0"
        write(stale / "agents" / "a.md", AGENT_PLAIN.format("a"))
        write(self.home / "plugins" / "installed_plugins.json", json.dumps(
            {"plugins": {"kit@local": [{"scope": "user", "installPath": str(stale)}]}}))
        write(self.home / "plugins" / "known_marketplaces.json", json.dumps(
            {"local": {"source": {"source": "directory", "path": str(market)},
                       "installLocation": str(market)}}))
        self.assertEqual(self.life("kit:a")[0], "1h")

    def kit(self, agents_field):
        """A plugin `kit` whose manifest lists `agents_field`, with a 1-hour agents/slow.md."""
        plugin = self.root / "kit"
        write(plugin / ".claude-plugin" / "plugin.json",
              json.dumps({"name": "kit", "agents": agents_field}))
        write(plugin / "agents" / "slow.md", AGENT_1H.format("slow"))
        return plugin

    def test_manifest_listed_file_keeps_the_default_directory(self):
        # the review's reproduced case: a listed file used to hide agents/ (kit:slow gave 5m)
        plugin = self.kit(["./extra/helper.md"])
        write(plugin / "extra" / "helper.md", AGENT_1H.format("helper"))
        self.assertEqual(self.life("kit:slow", own_root=str(plugin))[0], "1h")
        self.assertEqual(self.life("kit:helper", own_root=str(plugin))[0], "1h")

    def test_manifest_listing_the_default_directory(self):
        # the review's second case: "./agents/" was read as a file and nothing matched
        plugin = self.kit("./agents/")
        self.assertEqual(self.life("kit:slow", own_root=str(plugin)), (
            "1h", "agent definition experimental.cacheTtl"))

    def test_manifest_listed_directory_is_scanned_with_namespaces(self):
        plugin = self.kit(["./more"])
        write(plugin / "more" / "review" / "deep.md", AGENT_1H.format("deep"))
        self.assertEqual(self.life("kit:review:deep", own_root=str(plugin))[0], "1h")
        self.assertEqual(self.life("kit:deep", own_root=str(plugin))[0], "5m")
        self.assertEqual(self.life("kit:slow", own_root=str(plugin))[0], "1h")

    def test_manifest_listed_file_must_be_markdown(self):
        plugin = self.kit(["./extra/notes.txt", "./missing.md", 3])
        write(plugin / "extra" / "notes.txt", AGENT_1H.format("notes"))
        self.assertEqual(self.life("kit:notes", own_root=str(plugin))[0], "5m")
        self.assertEqual(self.life("kit:slow", own_root=str(plugin))[0], "1h")

    def test_manifest_listed_plain_file_beside_a_1h_default(self):
        # a listed 5-minute agent of another name does not change agents/slow.md
        plugin = self.kit(["./extra/quick.md"])
        write(plugin / "extra" / "quick.md", AGENT_PLAIN.format("quick"))
        self.assertEqual(self.life("kit:quick", own_root=str(plugin))[0], "5m")
        self.assertEqual(self.life("kit:slow", own_root=str(plugin))[0], "1h")

    def test_builtin_agents_get_the_default(self):
        for name in ("general-purpose", "Explore", "Plan", "fork", ""):
            with self.subTest(name):
                self.assertEqual(self.life(name)[0], "5m")


if __name__ == "__main__":
    unittest.main()
