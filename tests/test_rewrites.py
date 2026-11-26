"""The rewrite counter on synthetic subagent transcripts, built here in Claude Code's format.

Run: python3 -m unittest discover -s tests -v

Each fixture agent is a list of steps. A request step writes, as Claude Code does, an
`api-request` line when the request is sent and then one `assistant` line per content block,
all with the same requestId and usage. Between requests come `tool_result` or plain user lines.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import rewrites as rw  # noqa: E402
import thresholds  # noqa: E402

T0 = datetime(2030, 1, 7, 9, 0, 0, tzinfo=timezone.utc)
OPUS = "claude-opus-5-5"
USD = 4.0 / 1e6        # Opus 5.5 base input, per token
W5, W1, R, O = 1.25, 2.0, 0.05, 5.0


def ts(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def req(at, read, write, *, write_1h=0, output=100, tool=True, model=OPUS, took=5,
        blocks=2, api_line=True, rid=None, tool_input=None):
    """A request sent at `at` seconds: read and write in tokens (write is the 5-minute part).
    `tool` is False (the turn ends), True (a Bash call) or a tool name."""
    return {"kind": "req", "at": at, "read": read, "w5": write, "w1": write_1h, "out": output,
            "tool": tool, "model": model, "took": took, "blocks": blocks, "api": api_line,
            "rid": rid, "tool_input": tool_input or {"command": "secret command"}}


def tool_result(at, text="ok", error=False):
    """The result of the last tool call made before it."""
    return {"kind": "tool_result", "at": at, "text": text, "error": error}


def message(at, text="more work", meta=False):
    """A user message; meta=True marks it as Claude Code marks the text it adds itself."""
    return {"kind": "message", "at": at, "text": text, "meta": meta}


def api_request(at):
    """An `api-request` line alone: a request sent that never produced a response."""
    return {"kind": "api", "at": at}


def lines_for(agent_id, steps):
    out = [{"type": "user", "timestamp": ts(0), "agentId": agent_id, "isSidechain": True,
            "message": {"role": "user", "content": "the brief"}}]
    n = 0
    last_tool_id = None
    for step in steps:
        if step["kind"] == "tool_result":
            result = {"type": "tool_result", "tool_use_id": last_tool_id, "content": step["text"]}
            if step["error"]:
                result["is_error"] = True
            out.append({"type": "user", "timestamp": ts(step["at"]), "agentId": agent_id,
                        "message": {"role": "user", "content": [result]},
                        "toolUseResult": {}})
        elif step["kind"] == "message":
            line = {"type": "user", "timestamp": ts(step["at"]), "agentId": agent_id,
                    "message": {"role": "user", "content": step["text"]}}
            if step["meta"]:
                line["isMeta"] = True
            out.append(line)
        elif step["kind"] == "api":
            out.append({"type": "api-request", "timestamp": ts(step["at"]), "agentId": agent_id})
        else:
            n += 1
            rid = step["rid"] or f"req_{agent_id}_{n}"
            if step["api"]:
                out.append({"type": "api-request", "timestamp": ts(step["at"]),
                            "agentId": agent_id})
            usage = {"input_tokens": 3, "cache_read_input_tokens": step["read"],
                     "cache_creation_input_tokens": step["w5"] + step["w1"],
                     "output_tokens": step["out"],
                     "cache_creation": {"ephemeral_5m_input_tokens": step["w5"],
                                        "ephemeral_1h_input_tokens": step["w1"]}}
            kinds = ["thinking"] * (step["blocks"] - 1) + ["tool_use" if step["tool"] else "text"]
            for i, kind in enumerate(kinds):
                block = {"type": kind}
                if kind == "tool_use":
                    last_tool_id = f"tu_{rid}"
                    block.update(id=last_tool_id, input=step["tool_input"],
                                 name=step["tool"] if isinstance(step["tool"], str) else "Bash")
                out.append({"type": "assistant", "requestId": rid, "agentId": agent_id,
                            "timestamp": ts(step["at"] + step["took"] * (i + 1) / len(kinds)),
                            "message": {"role": "assistant", "model": step["model"],
                                        "stop_reason": "tool_use" if step["tool"] else "end_turn",
                                        "usage": usage, "content": [block]}})
    return out


class Fixture:
    """A temporary transcript root: <root>/<project>/<session>/subagents/agent-<id>.jsonl."""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def agent(self, agent_id, steps, agent_type="general-purpose", session="s1", extra=()):
        d = self.root / "project-a" / session / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"agent-{agent_id}.jsonl"
        body = [json.dumps(x) for x in lines_for(agent_id, steps)] + list(extra)
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        if agent_type is not None:
            (d / f"agent-{agent_id}.meta.json").write_text(
                json.dumps({"agentType": agent_type, "description": "synthetic"}))
        return path

    def run(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rw.main(["--root", str(self.root), *args])
        return code, buf.getvalue()

    def summary(self, *args):
        code, out = self.run("--json", *args)
        assert code == 0
        return json.loads(out)

    def close(self):
        self.dir.cleanup()


def warm_agent():
    return [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 2_000),
            tool_result(23), req(24, 42_000, 1_000, tool=False)]


def tool_wait_agent():
    # a 12-minute tool call, then the context of 160k is written again
    return [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 120_000),
            tool_result(17 + 5 + 720), req(17 + 5 + 721, 30_000, 131_000)]


def resume_agent():
    # the agent ends its turn; 20 minutes later it is sent more work and rewrites 200k
    return [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
            message(17 + 1200), req(17 + 1201, 0, 201_000)]


class Parsing(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_one_request_per_request_id(self):
        path = self.fx.agent("a1", [req(10, 0, 40_000, blocks=4), tool_result(16),
                                    req(17, 40_000, 2_000, blocks=3)])
        agent = rw.read_agent(path)
        self.assertEqual(len(agent["requests"]), 2)
        self.assertEqual(agent["requests"][0]["write_5m"], 40_000)
        self.assertEqual(agent["id"], "a1")
        self.assertEqual(agent["type"], "general-purpose")

    def test_start_is_the_api_request_line(self):
        path = self.fx.agent("a1", [req(10, 0, 40_000, took=8), tool_result(30),
                                    req(31, 40_000, 2_000)])
        a = rw.classify(rw.read_agent(path))
        second = a["requests"][1]
        self.assertEqual(second["start"], T0 + timedelta(seconds=31))
        self.assertEqual(second["gap_s"], 21.0)          # from the previous start, at 10 s
        self.assertEqual(second["idle_s"], 13.0)         # from the previous end, at 18 s

    def test_start_falls_back_to_the_last_line_before(self):
        path = self.fx.agent("a1", [req(10, 0, 40_000, api_line=False), tool_result(30),
                                    req(31, 40_000, 2_000, api_line=False)])
        a = rw.classify(rw.read_agent(path))
        self.assertEqual(a["requests"][1]["start"], T0 + timedelta(seconds=30))

    def test_bad_lines_and_synthetic_messages_are_skipped(self):
        synthetic = json.dumps({"type": "assistant", "timestamp": ts(500), "requestId": "x",
                                "message": {"model": "<synthetic>", "usage": {}}})
        path = self.fx.agent("a1", warm_agent(), extra=["{not json", "[1, 2]", synthetic])
        agent = rw.read_agent(path)
        self.assertEqual(len(agent["requests"]), 3)
        self.assertEqual(agent["bad_lines"], 2)

    def test_missing_meta_gives_unknown_type(self):
        path = self.fx.agent("a1", warm_agent(), agent_type=None)
        self.assertIsNone(rw.read_agent(path)["type"])

    def test_write_without_split_counts_as_5_minutes(self):
        path = self.fx.agent("a1", [req(10, 0, 40_000)])
        text = path.read_text().replace(', "cache_creation": {"ephemeral_5m_input_tokens": 40000, '
                                        '"ephemeral_1h_input_tokens": 0}', "")
        path.write_text(text)
        r = rw.read_agent(path)["requests"][0]
        self.assertEqual((r["write_5m"], r["write_1h"]), (40_000, 0))

    def test_model_key(self):
        self.assertEqual(rw.model_key("claude-opus-5-5"), "opus-5-5")
        self.assertEqual(rw.model_key("claude-sonnet-5-5-20301101"), "sonnet-5-5")
        self.assertEqual(rw.model_key("claude-opus-5-5[1m]"), "opus-5-5")

    def test_a_time_with_an_offset_is_converted_to_utc(self):
        self.assertEqual(rw.parse_time("2030-01-07T02:00:00+02:00"),
                         datetime(2030, 1, 7, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(rw.parse_time("2030-01-06T19:00:00-05:00"),
                         datetime(2030, 1, 7, 0, 0, tzinfo=timezone.utc))

    def test_a_date_or_naive_time_is_utc_whatever_the_local_zone(self):
        if not hasattr(time, "tzset"):
            self.skipTest("time.tzset is not available here")
        old = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Kolkata"
        time.tzset()
        try:
            utc_midnight = datetime(2030, 1, 7, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(rw.parse_time("2030-01-07"), utc_midnight)
            self.assertEqual(rw.parse_time("2030-01-07T00:00:00"), utc_midnight)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_a_request_that_got_no_response_does_not_move_the_start(self):
        # An api-request line with no response, then a retry: the retry's line is the start.
        path = self.fx.agent("a1", [req(10, 0, 40_000), tool_result(16), api_request(17),
                                    req(30, 40_000, 2_000)])
        a = rw.classify(rw.read_agent(path))
        self.assertEqual(len(a["requests"]), 2)
        self.assertEqual(a["requests"][1]["start"], T0 + timedelta(seconds=30))
        self.assertEqual(a["requests"][1]["gap_s"], 20.0)

    def test_an_unreadable_transcript_is_marked(self):
        d = self.fx.root / "project-a" / "s1" / "subagents" / "agent-broken.jsonl"
        d.mkdir(parents=True)
        agent = rw.read_agent(d)
        self.assertTrue(agent["unreadable"])
        self.assertEqual(agent["requests"], [])
        self.assertFalse(rw.read_agent(self.fx.agent("a1", warm_agent()))["unreadable"])

    def test_tags_keep_no_content(self):
        self.assertEqual(rw._tag("<task-notification>secret"), "<task-notification>")
        self.assertEqual(rw._tag("[SYSTEM NOTIFICATION - NOT USER INPUT] secret"),
                         "[SYSTEM NOTIFICATION - NOT USER INPUT]")
        self.assertEqual(rw._tag("[Fix the secret login bug] now"), rw.BRACKET)
        self.assertEqual(rw._tag("  [secret]"), rw.BRACKET)
        self.assertEqual(rw._tag("<secret thing to do"), "text")
        self.assertEqual(rw._tag("secret plain text"), "text")


class Rewrites(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def classified(self, steps, **kw):
        return rw.classify(rw.read_agent(self.fx.agent("a1", steps)), **kw)["requests"]

    def test_warm_agent_has_none(self):
        self.assertFalse(any(r["rewrite"] for r in self.classified(warm_agent())))

    def test_tool_wait(self):
        last = self.classified(tool_wait_agent())[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["cause"], rw.TOOL_WAIT)
        self.assertEqual(last["gap_s"], 721.0 + 5)       # 17 -> 743
        self.assertEqual(last["idle_s"], 721.0)          # previous end at 22 s

    def test_resume(self):
        last = self.classified(resume_agent())[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["cause"], rw.RESUME)
        self.assertEqual(last["gap_s"], 1201.0)

    def test_resume_after_a_hand_back_tool_is_a_resume(self):
        # The agent hands back to its manager with a tool call whose result is immediate; the
        # manager sends more work 15 minutes later. Waited on: the message, not the tool.
        steps = [req(10, 0, 40_000), tool_result(16),
                 req(17, 40_000, 160_000, tool="SubagentHandback"),
                 tool_result(22.2), message(17 + 900, text="more work", meta=True),
                 req(17 + 901, 0, 201_000)]
        last = self.classified(steps)[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["cause"], rw.RESUME)

    def test_a_reminder_beside_a_tool_result_keeps_it_a_tool_wait(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 120_000),
                 tool_result(742), message(742.5, text="reminder", meta=True),
                 req(743, 30_000, 131_000)]
        self.assertEqual(self.classified(steps)[-1]["cause"], rw.TOOL_WAIT)

    def test_long_request(self):
        # The previous request ran 7 minutes and the agent went on at once: the request
        # outlived its own cache, which counts from the request's start.
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 120_000, took=420),
                 tool_result(17 + 420.4), req(17 + 421, 30_000, 131_000)]
        last = self.classified(steps)[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["cause"], rw.LONG_REQUEST)
        self.assertLessEqual(last["idle_s"], 1)

    def test_a_quick_resume_after_a_longer_request_is_a_resume(self):
        # The previous request ran 90 s and the message came 2 s after it: it ran longer than
        # the agent sat idle, but the gap (93 s) is inside the 5-minute lifetime.
        steps = [req(10, 0, 40_000), tool_result(16),
                 req(17, 40_000, 160_000, tool=False, took=90),
                 message(17 + 92), req(17 + 93, 0, 201_000)]
        last = self.classified(steps)[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["gap_s"], 93.0)
        self.assertEqual(last["cause"], rw.RESUME)

    def test_a_quick_tool_result_after_a_longer_request_is_a_tool_wait(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 120_000, took=100),
                 tool_result(17 + 102), req(17 + 103, 30_000, 131_000)]
        last = self.classified(steps)[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["cause"], rw.TOOL_WAIT)

    def test_a_long_request_needs_the_gap_to_reach_the_lifetime(self):
        # took 284 s, tool result at once: gap 285 s, under 300 s -> not a long request;
        # took 300 s: gap 301 s -> a long request.
        def steps(took):
            return [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 120_000, took=took),
                    tool_result(17 + took + 0.5), req(17 + took + 1, 30_000, 131_000)]
        self.assertEqual(self.classified(steps(284))[-1]["cause"], rw.TOOL_WAIT)
        self.assertEqual(self.classified(steps(300))[-1]["cause"], rw.LONG_REQUEST)

    def test_a_1_hour_agent_has_a_1_hour_lifetime_for_long_requests(self):
        # A 7-minute request does not outlive a 1-hour cache; a 61-minute one does.
        def steps(took):
            return [req(10, 0, 0, write_1h=40_000), tool_result(16),
                    req(17, 40_000, 0, write_1h=120_000, took=took),
                    tool_result(17 + took + 0.5), req(17 + took + 1, 30_000, 0, write_1h=131_000)]
        last = self.classified(steps(420))[-1]
        self.assertEqual(last["lifetime_s"], 3600)
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["cause"], rw.TOOL_WAIT)
        self.assertEqual(self.classified(steps(3660))[-1]["cause"], rw.LONG_REQUEST)

    def test_the_first_requests_lifetime_is_5_minutes(self):
        reqs = self.classified(warm_agent())
        self.assertEqual({r["lifetime_s"] for r in reqs}, {300})

    def test_rewritten_tokens_leave_out_the_new_tail(self):
        # 160k cached; the resume adds a 100k message and writes 260k with nothing read: 160k
        # were written again, 100k are the tail a warm request would also write.
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 120_000, tool=False),
                 message(17 + 1200), req(17 + 1201, 0, 260_000)]
        last = self.classified(steps)[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["rewritten"], 160_000)
        # part of the prefix read: 20k read, 140k missed
        steps[-1] = req(17 + 1201, 20_000, 240_000)
        self.assertEqual(self.classified(steps)[-1]["rewritten"], 140_000)

    def test_a_notification_is_read_from_the_raw_text(self):
        # A tag with attributes is not kept by _tag, but the message is still a notification.
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
                 message(17 + 500, text='<task-notification id="x">done</task-notification>',
                         meta=True),
                 req(17 + 501, 0, 201_000)]
        last = self.classified(steps)[-1]
        self.assertEqual(last["cause"], rw.NOTIFICATION)
        self.assertEqual(last["events"][-1]["tag"], "text")

    def test_a_bracketed_message_from_the_manager_is_a_resume(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
                 message(17 + 500, text="[Fix the login bug] now"),
                 req(17 + 501, 0, 201_000)]
        self.assertEqual(self.classified(steps)[-1]["cause"], rw.RESUME)

    def test_system_notification_after_the_turn_ended(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
                 message(17 + 500, text="[SYSTEM NOTIFICATION - NOT USER INPUT] done",
                         meta=True),
                 req(17 + 501, 0, 201_000)]
        last = self.classified(steps)[-1]
        self.assertTrue(last["rewrite"])
        self.assertEqual(last["cause"], rw.NOTIFICATION)

    def test_large_tool_result_is_not_cold(self):
        # 150k written, but the whole previous prefix (42k) was read: a big file, not a miss
        reqs = self.classified([req(10, 0, 40_000), tool_result(16), req(17, 40_000, 2_000),
                                tool_result(20), req(21, 42_000, 150_000)])
        self.assertFalse(reqs[-1]["rewrite"])
        self.assertTrue(reqs[-1]["large_write"])

    def test_threshold_is_an_option(self):
        steps = [req(10, 0, 30_000), tool_result(16), req(17, 30_000, 50_000),
                 tool_result(700), req(701, 0, 81_000)]
        self.assertFalse(self.classified(steps)[-1]["rewrite"])
        self.assertTrue(self.classified(steps, min_write=50_000)[-1]["rewrite"])

    def test_first_request_is_never_a_rewrite(self):
        self.assertFalse(self.classified([req(10, 0, 300_000)])[0]["rewrite"])

    def test_cold_after_a_short_pause_is_counted(self):
        # the docs/TODO.md case: a pause under 5 minutes that still finds the cache cold
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 120_000),
                 tool_result(200), req(201, 20_000, 141_000)]
        last = self.classified(steps)[-1]
        self.assertTrue(last["rewrite"])
        self.assertLess(last["gap_s"], 300)


class Summary(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.prices = rw.Prices(thresholds.load())

    def tearDown(self):
        self.fx.close()

    def window(self, *args):
        return self.fx.summary(*args)["windows"]

    def test_costs_by_hand(self):
        self.fx.agent("a1", resume_agent())
        s = self.window()[0]
        self.assertEqual(s["agents"], 1)
        self.assertEqual(s["requests"], 3)
        self.assertEqual(s["rewrites"], 1)
        # 200k were cached before the resume; the 1k beyond is the new tail
        self.assertEqual(s["rewrite_tokens"], 200_000)
        self.assertAlmostEqual(s["rewrite_usd"], 200_000 * W5 * USD)
        self.assertAlmostEqual(s["avoidable_usd"], 200_000 * (W5 - R) * USD)
        writes = 40_000 + 160_000 + 201_000
        reads = 40_000
        self.assertEqual(s["write_tokens"], writes)
        self.assertAlmostEqual(s["input_usd"], (3 * 3 + R * reads + W5 * writes) * USD)
        self.assertAlmostEqual(s["output_usd"], 3 * 100 * O * USD)
        self.assertAlmostEqual(s["rewrite_share_of_writes"], 200_000 / writes)
        self.assertEqual((s["rewrite_list"][0]["tokens"], s["rewrite_list"][0]["written"]),
                         (200_000, 201_000))
        self.assertEqual(s["by_cause"], {rw.TOOL_WAIT: 0, rw.RESUME: 1, rw.NOTIFICATION: 0,
                                         rw.LONG_REQUEST: 0, rw.OTHER: 0})

    def test_gaps_and_causes_across_agents(self):
        self.fx.agent("a1", tool_wait_agent())
        self.fx.agent("a2", resume_agent())
        self.fx.agent("a3", warm_agent())
        s = self.window()[0]
        self.assertEqual(s["agents"], 3)
        self.assertEqual(s["rewrites"], 2)
        self.assertEqual(s["by_cause"][rw.TOOL_WAIT], 1)
        self.assertEqual(s["by_cause"][rw.RESUME], 1)
        self.assertEqual(s["gap_min_s"], 726.0)
        self.assertEqual(s["gap_max_s"], 1201.0)
        self.assertEqual(s["gap_median_s"], (726.0 + 1201.0) / 2)
        self.assertEqual(s["under_5_minutes"], 0)
        self.assertEqual([r["agent"] for r in s["rewrite_list"]], ["a1", "a2"])

    def test_split_puts_each_request_in_its_window(self):
        self.fx.agent("a1", resume_agent())                       # all on day 0
        later = [dict(step, at=step["at"] + 86_400) for step in tool_wait_agent()]
        self.fx.agent("a2", later)                                # all on day 1
        day1 = (T0 + timedelta(days=1)).date().isoformat()
        before, after = self.window("--since", T0.date().isoformat(), "--split", day1)
        self.assertEqual((before["agents"], before["rewrites"]), (1, 1))
        self.assertEqual(before["by_cause"][rw.RESUME], 1)
        self.assertEqual((after["agents"], after["rewrites"]), (1, 1))
        self.assertEqual(after["by_cause"][rw.TOOL_WAIT], 1)

    def test_an_agent_across_the_split_counts_in_both(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
                 message(86_400), req(86_401, 0, 201_000)]
        self.fx.agent("a1", steps)
        day1 = (T0 + timedelta(days=1)).date().isoformat()
        before, after = self.window("--split", day1)
        self.assertEqual((before["agents"], before["rewrites"]), (1, 0))
        self.assertEqual((after["agents"], after["requests"], after["rewrites"]), (1, 1, 1))

    def test_until_is_exclusive(self):
        self.fx.agent("a1", warm_agent())
        self.assertEqual(self.window("--until", T0.date().isoformat())[0]["agents"], 0)
        self.assertEqual(self.window("--since", T0.date().isoformat())[0]["agents"], 1)

    def test_relay_and_one_hour_writes(self):
        relay = [req(10, 0, 0, write_1h=40_000), tool_result(16),
                 req(17, 40_000, 0, write_1h=3_000, tool=False),
                 message(17 + 1800), req(17 + 1801, 43_000, 0, write_1h=500)]
        self.fx.agent("r1", relay, agent_type="embers:relay")
        self.fx.agent("a1", warm_agent())
        s = self.window()[0]
        self.assertEqual(s["relay_agents"], 1)
        self.assertEqual(s["relay_requests"], 3)
        self.assertEqual(s["relay_requests_writing"], 3)
        self.assertEqual(s["relay_requests_writing_1h"], 3)
        self.assertEqual(s["relay_rewrites"], 0)
        self.assertEqual(s["agents_writing_1h"], 1)
        self.assertEqual(s["write_1h_tokens"], 43_500)

    def test_a_cold_1_hour_rewrite_is_priced_at_the_1_hour_write(self):
        steps = [req(10, 0, 0, write_1h=40_000), tool_result(16),
                 req(17, 40_000, 0, write_1h=160_000, tool=False),
                 message(17 + 4000), req(17 + 4001, 0, 0, write_1h=201_000)]
        self.fx.agent("r1", steps, agent_type="embers:relay")
        s = self.window()[0]
        self.assertEqual(s["relay_rewrites"], 1)
        self.assertAlmostEqual(s["rewrite_usd"], 200_000 * W1 * USD)
        self.assertAlmostEqual(s["avoidable_usd"], 200_000 * (W1 - R) * USD)

    def test_the_new_tail_is_not_avoidable(self):
        # the review's case: 160k cached, 260k written on the resume, 160k of it rewritten
        self.fx.agent("a1", [req(10, 0, 40_000), tool_result(16),
                             req(17, 40_000, 120_000, tool=False),
                             message(17 + 1200), req(17 + 1201, 0, 260_000)])
        s = self.window()[0]
        self.assertEqual(s["rewrite_tokens"], 160_000)
        self.assertAlmostEqual(s["rewrite_usd"], 160_000 * W5 * USD)
        self.assertAlmostEqual(s["avoidable_usd"], 160_000 * (W5 - R) * USD)

    def test_each_request_priced_at_its_model(self):
        self.fx.agent("a1", [req(10, 0, 100_000, model="claude-sonnet-5-5")])
        s = self.window()[0]
        self.assertAlmostEqual(s["input_usd"], (3 + W5 * 100_000) * 2.0 / 1e6)

    def test_reads_priced_at_each_models_own_read_rate(self):
        # Fable 5.2 reads at 0.025 and Haiku 5.5 at 0.1, not the default model's 0.05.
        for model, base, read in (("claude-fable-5-2", 8.0, 0.025), ("claude-haiku-5-5", 0.5, 0.1)):
            with self.subTest(model=model):
                fx = Fixture()
                try:
                    fx.agent("a1", [req(10, 0, 100_000, model=model), tool_result(16),
                                    req(17, 100_000, 1_000, model=model)])
                    s = fx.summary()["windows"][0]
                    self.assertAlmostEqual(
                        s["input_usd"], (3 + W5 * 100_000 + 3 + read * 100_000 + W5 * 1_000)
                        * base / 1e6)
                finally:
                    fx.close()

    def test_window_bounds_at_the_exact_instant(self):
        # One request a second before midnight UTC of 2030-01-08 and one exactly at it.
        midnight = 86_400 - 9 * 3_600                      # T0 is 09:00 on 2030-01-07
        self.fx.agent("a1", [req(10, 0, 40_000), tool_result(16),
                             req(midnight - 1, 40_000, 1_000, took=0.5),
                             tool_result(midnight - 0.2), req(midnight, 41_000, 1_000)])
        day1 = "2030-01-08"
        before, after = self.window("--split", day1)
        self.assertEqual((before["requests"], after["requests"]), (2, 1))
        self.assertEqual(self.window("--since", day1)[0]["requests"], 1)
        self.assertEqual(self.window("--until", day1)[0]["requests"], 2)
        self.assertEqual(self.window("--since", "2030-01-08T00:00:00Z")[0]["requests"], 1)

    def test_a_since_with_an_offset_is_read_in_utc(self):
        # 11:00+02:00 is 09:00Z, before the warm agent's requests at 09:00:10 and after.
        self.fx.agent("a1", warm_agent())
        self.assertEqual(self.window("--since", "2030-01-07T11:00:00+02:00")[0]["requests"], 3)
        self.assertEqual(self.window("--until", "2030-01-07T11:00:00+02:00")[0]["requests"], 0)
        self.assertEqual(self.window("--since", "2030-01-07T04:00:20-05:00")[0]["requests"], 1)

    def test_under_5_minutes_counts_gaps_below_300_s(self):
        for gap, agent_id in ((299, "a1"), (300, "a2"), (301, "a3")):
            self.fx.agent(agent_id, [req(10, 0, 40_000), tool_result(16),
                                     req(17, 40_000, 160_000, tool=False),
                                     message(17 + gap - 1), req(17 + gap, 0, 201_000)])
        s = self.window()[0]
        self.assertEqual(s["rewrites"], 3)
        self.assertEqual(s["under_5_minutes"], 1)
        self.assertEqual(s["gap_min_s"], 299.0)

    def test_unknown_model_priced_at_the_default_and_named(self):
        self.fx.agent("a1", [req(10, 0, 100_000, model="claude-future-9")])
        out = self.fx.summary()
        self.assertEqual(out["unknown_models"], ["claude-future-9"])
        self.assertAlmostEqual(out["windows"][0]["input_usd"], (3 + W5 * 100_000) * USD)

    def test_breakeven_share_is_the_math_page_value(self):
        self.assertAlmostEqual(self.prices.breakeven_share(), 0.75 / (0.75 + 1.2))


class Command(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_table_side_by_side(self):
        self.fx.agent("a1", resume_agent())
        day1 = (T0 + timedelta(days=1)).date().isoformat()
        code, out = self.fx.run("--since", T0.date().isoformat(), "--split", day1, "--list")
        self.assertEqual(code, 0)
        self.assertIn(f"{T0.date().isoformat()} .. {day1}", out)
        self.assertIn(f"{day1} .. now", out)
        row = next(line for line in out.splitlines() if line.startswith("cold rewrites"))
        self.assertEqual(row.split()[-2:], ["1", "0"])
        self.assertIn("agent a1", out)
        self.assertIn("resume", out)

    def test_detail_names_the_tool_and_the_gap_without_content(self):
        steps = [req(10, 0, 40_000), tool_result(16),
                 req(17, 40_000, 160_000, tool="Agent",
                     tool_input={"prompt": "secret brief", "run_in_background": False}),
                 tool_result(17 + 600, text="secret result"),
                 message(17 + 601, text="<task-notification>secret</task-notification>"),
                 req(17 + 602, 0, 201_000)]
        self.fx.agent("a1", steps)
        code, out = self.fx.run("--detail")
        self.assertEqual(code, 0)
        self.assertIn("tool calls: Agent background=False, ended the turn: no", out)
        self.assertIn("previous request: ran 0.1 min, 100 output tokens", out)
        self.assertIn("tool result Agent", out)
        self.assertIn("message <task-notification>", out)
        self.assertNotIn("secret", out)
        r = self.fx.summary()["windows"][0]["rewrite_list"][0]
        self.assertEqual([e["kind"] for e in r["events"]], ["tool result", "message"])
        self.assertEqual(r["events"][0]["after_s"], 600 - 5)   # previous response ended at 22 s
        self.assertFalse(r["prev_ended_turn"])

    def test_detail_and_json_print_no_content_of_a_bracketed_message(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
                 message(17 + 600, text="[Fix the secret login bug] please"),
                 req(17 + 601, 0, 201_000),
                 message(17 + 1300, text="[SYSTEM NOTIFICATION - NOT USER INPUT] secret job",
                         meta=True),
                 req(17 + 1301, 0, 202_000)]
        self.fx.agent("a1", steps)
        code, out = self.fx.run("--detail")
        self.assertEqual(code, 0)
        self.assertIn(f"message {rw.BRACKET}", out)
        self.assertIn("meta message [SYSTEM NOTIFICATION - NOT USER INPUT]", out)
        self.assertNotIn("secret", out)
        self.assertNotIn("Fix", out)
        _, js = self.fx.run("--json")
        self.assertNotIn("secret", js)
        self.assertNotIn("Fix", js)

    def test_an_unreadable_transcript_is_reported(self):
        self.fx.agent("a1", warm_agent())
        broken = self.fx.root / "project-a" / "s1" / "subagents" / "agent-broken.jsonl"
        broken.mkdir()
        out = self.fx.summary()
        self.assertEqual(out["unreadable_transcripts"], 1)
        self.assertEqual(out["windows"][0]["agents"], 1)
        _, table = self.fx.run()
        self.assertIn("1 transcript could not be read", table)
        self.fx.close()
        self.fx = Fixture()
        self.fx.agent("a1", warm_agent())
        _, table = self.fx.run()
        self.assertNotIn("could not be read", table)
        self.assertEqual(self.fx.summary()["unreadable_transcripts"], 0)

    def test_detail_flags_an_interrupted_tool_result(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000),
                 tool_result(17 + 900, text="[Request interrupted by user for tool use]",
                             error=True),
                 message(17 + 901), req(17 + 902, 0, 201_000)]
        self.fx.agent("a1", steps)
        _, out = self.fx.run("--detail")
        self.assertIn("tool result Bash (error, interrupted)", out)

    def test_empty_root_still_exits_0(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, out = self.fx.run()
        self.assertEqual(code, 0)
        self.assertIn("no subagent transcripts", err.getvalue())
        self.assertIn("subagents seen", out)

    def test_split_outside_the_window_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rw.main(["--root", str(self.fx.root), "--since", "2030-01-07", "--split", "2030-01-01"])

    def test_only_subagent_transcripts_are_read(self):
        self.fx.agent("a1", warm_agent())
        main_session = self.fx.root / "project-a" / "s1.jsonl"
        main_session.write_text(json.dumps(lines_for("main", resume_agent())[1]) + "\n")
        self.assertEqual(self.fx.summary()["windows"][0]["agents"], 1)


# --- with and without Embers ---------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import rules  # noqa: E402

RULE_TEXT = rules.waiting_rule(rules.FALLBACK)  # the text the hook injects


def hook_context(text=RULE_TEXT, event="SubagentStart"):
    """The line Claude Code writes for the context a SubagentStart hook added."""
    return json.dumps({"type": "attachment", "timestamp": ts(1), "attachment": {
        "type": "hook_additional_context", "content": [text], "hookName": event,
        "toolUseID": "t1", "hookEvent": event}})


def hook_run(text=RULE_TEXT):
    """The line Claude Code writes for the hook's run, the rule in its stdout."""
    stdout = json.dumps({"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                                "additionalContext": text}})
    return json.dumps({"type": "attachment", "timestamp": ts(1), "attachment": {
        "type": "hook_success", "hookName": "SubagentStart:general-purpose",
        "hookEvent": "SubagentStart", "content": "", "stdout": stdout,
        "stderr": "embers: general-purpose cache 5m\n", "exitCode": 0}})


def relay_agent(resume_after=1800):
    # a relay: 1-hour writes, its turn ends, work comes back after `resume_after` seconds and
    # finds the whole prefix (43k) in the cache
    return [req(10, 0, 0, write_1h=40_000), tool_result(16),
            req(17, 40_000, 0, write_1h=3_000, tool=False),
            message(17 + resume_after - 1), req(17 + resume_after, 43_000, 0, write_1h=500)]


def notification_agent(gap=501):
    # the agent ends its turn to wait for a job; the job's notice wakes it `gap` s later, cold
    return [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
            message(17 + gap - 1, text="[SYSTEM NOTIFICATION - NOT USER INPUT] done", meta=True),
            req(17 + gap, 0, 201_000)]


class Detection(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def read(self, extra=(), agent_type="general-purpose"):
        return rw.read_agent(self.fx.agent("a1", warm_agent(), agent_type, extra=extra))

    def test_the_mark_is_the_waiting_rules_first_words(self):
        self.assertTrue(rules.waiting_rule(rules.FALLBACK).startswith(rw.EMBERS_RULE_MARK))

    def test_the_hooks_added_context_marks_the_agent(self):
        self.assertTrue(self.read([hook_context()])["embers_rule"])
        self.assertTrue(self.read([hook_run()])["embers_rule"])
        self.assertFalse(self.read()["embers_rule"])

    def test_a_brief_quoting_the_rule_does_not(self):
        brief = json.dumps({"type": "user", "timestamp": ts(1),
                            "message": {"role": "user", "content": RULE_TEXT}})
        self.assertFalse(self.read([brief])["embers_rule"])

    def test_another_hook_or_another_text_does_not(self):
        self.assertFalse(self.read([hook_context(event="SessionStart")])["embers_rule"])
        self.assertFalse(self.read([hook_context(text="another plugin's rule")])["embers_rule"])

    def test_on_by_the_rule_or_the_relay_type(self):
        for extra, agent_type, on in (([hook_context()], "general-purpose", True),
                                      ((), "embers:relay", True),
                                      ((), "general-purpose", False),
                                      ((), None, False)):
            with self.subTest(agent_type=agent_type, rule=bool(extra)):
                fx = Fixture()
                try:
                    agent = rw.read_agent(fx.agent("a1", warm_agent(), agent_type, extra=extra))
                    parts = rw.embers_parts(agent)
                    self.assertEqual([(p_on, len(r)) for p_on, r in parts], [(on, 3)])
                finally:
                    fx.close()
        agent = self.read((), "my-relay")
        self.assertTrue(rw.embers_parts(agent, relay_type="my-relay")[0][0])

    def test_dates_decide_alone_and_split_an_agent(self):
        # requests at 10, 17 and 24 s; Embers on from 15 s: the first is off, the other two on,
        # though the transcript holds the rule
        agent = self.read([hook_context()])
        parts = rw.embers_parts(agent, ranges=[(T0 + timedelta(seconds=15), None)])
        self.assertEqual([(on, len(r)) for on, r in parts], [(True, 2), (False, 1)])
        parts = rw.embers_parts(agent, ranges=[(None, T0), (T0 + timedelta(days=1), None)])
        self.assertEqual([(on, len(r)) for on, r in parts], [(False, 3)])

    def test_the_window_applies(self):
        agent = self.read([hook_context()])
        self.assertEqual(rw.embers_parts(agent, since=T0 + timedelta(seconds=20))[0][1],
                         agent["requests"][2:])
        self.assertEqual(rw.embers_parts(agent, until=T0), [])


class Counterfactual(unittest.TestCase):
    """Each rule of the cost model on one agent, priced by hand (Opus 5.5)."""

    def setUp(self):
        self.fx = Fixture()
        self.prices = rw.Prices(thresholds.load())

    def tearDown(self):
        self.fx.close()

    def reqs(self, steps, agent_type="general-purpose"):
        return rw.classify(rw.read_agent(self.fx.agent("a1", steps, agent_type)))["requests"]

    def with_embers(self, steps):
        return rw.with_embers(self.reqs(steps), self.prices, 3600, 170,
                              {"tail_tokens": 300, "output_tokens": 150})

    def test_sent_back_under_the_hour_becomes_a_relay_that_reads(self):
        # writes 40k + 160k + 201k, all at the 1-hour price; the resume's 200k rewritten
        # tokens are read instead, its 1k tail written
        reqs = self.reqs(resume_agent())
        d = self.with_embers(resume_agent())
        self.assertTrue(d["relay"])
        self.assertEqual(d["relay_rewrites_read"], 1)
        self.assertAlmostEqual(d["relay_1h_usd"], (W1 - W5) * 401_000 * USD)
        self.assertAlmostEqual(d["relay_read_usd"], (W1 - R) * 200_000 * USD)
        measured = sum(self.prices.input_cost(r) for r in reqs)
        self.assertAlmostEqual(measured + d["change_usd"],
                               (9 + R * (40_000 + 200_000) + W1 * (40_000 + 160_000 + 1_000)) * USD)

    def test_sent_back_past_the_hour_stays_cold_at_the_1_hour_price(self):
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
                 message(17 + 3999), req(17 + 4000, 0, 201_000)]
        d = self.with_embers(steps)
        self.assertTrue(d["relay"])
        self.assertEqual(d["relay_rewrites_read"], 0)
        self.assertAlmostEqual(d["change_usd"], (W1 - W5) * 401_000 * USD)

    def test_a_notification_rewrite_is_polled_instead(self):
        # gap 501 s: ceil(501 / 170) - 1 = 2 polls, each reading the 200k context and writing
        # the poll's 300-token tail, with 150 output tokens
        d = self.with_embers(notification_agent(501))
        self.assertFalse(d["relay"])
        self.assertEqual((d["notifications_polled"], d["polls"]), (1, 2))
        self.assertAlmostEqual(d["notification_usd"], (W5 - R) * 200_000 * USD)
        self.assertAlmostEqual(d["poll_usd"], 2 * (R * 200_000 + W5 * 300 + O * 150) * USD)
        self.assertAlmostEqual(d["change_usd"], d["poll_usd"] - d["notification_usd"])
        self.assertEqual(self.with_embers(notification_agent(100))["polls"], 1)  # at least one

    def test_a_notification_in_an_agent_sent_back_goes_the_relay_way(self):
        steps = resume_agent() + [
            message(1300, text="<task-notification>done</task-notification>", meta=True),
            req(1301, 0, 202_000)]
        reqs = self.reqs(steps)
        self.assertEqual([r["cause"] for r in reqs if r["rewrite"]], [rw.RESUME, rw.NOTIFICATION])
        d = self.with_embers(steps)
        self.assertTrue(d["relay"])
        self.assertEqual((d["relay_rewrites_read"], d["notifications_polled"]), (2, 0))

    def test_only_a_cold_resume_makes_a_relay(self):
        # the agent ends its turn and is sent more work 2 minutes later, which reads warm
        steps = [req(10, 0, 40_000), tool_result(16), req(17, 40_000, 160_000, tool=False),
                 message(17 + 120), req(17 + 121, 200_000, 1_000)]
        reqs = self.reqs(steps)
        self.assertEqual((reqs[-1]["cause"], reqs[-1]["rewrite"]), (rw.RESUME, False))
        d = self.with_embers(steps)
        self.assertFalse(d["relay"])
        self.assertEqual(d["change_usd"], 0.0)

    def test_a_1_hour_agent_is_left_as_it_ran(self):
        # an agent of the user's own with a 1-hour cache: it ends its turn on a 2-hour job, and
        # the job's notice finds 300k cold; then it is sent work back past the hour, cold again.
        # Embers injects nothing into it and it has the relay's cache already: no change.
        steps = [req(10, 0, 0, write_1h=40_000), tool_result(16),
                 req(17, 40_000, 0, write_1h=260_000, tool=False),
                 message(17 + 7199, text="<task-notification>done</task-notification>",
                         meta=True),
                 req(17 + 7200, 0, 0, write_1h=301_000, tool=False),
                 message(17 + 7200 + 3999), req(17 + 7200 + 4000, 0, 0, write_1h=302_000)]
        reqs = self.reqs(steps)
        self.assertEqual([r["cause"] for r in reqs if r["rewrite"]],
                         [rw.NOTIFICATION, rw.RESUME])
        d = rw.with_embers(reqs, self.prices, 3600, 170,
                           {"tail_tokens": 300, "output_tokens": 150}, one_hour=True)
        self.assertEqual((d["relay"], d["one_hour"], d["polls"]), (False, True, 0))
        self.assertEqual(d["change_usd"], 0.0)
        self.fx.agent("own1h", steps, "my-1h-agent")
        off = self.fx.summary("--savings")["savings"]["off"]
        self.assertEqual((off["agents"], off["one_hour_agents"]), (2, 2))
        self.assertEqual((off["would_be_relays"], off["notifications_polled"]), (0, 0))
        self.assertEqual(off["change_usd"], 0.0)

    def test_other_rewrites_stay(self):
        long_request = [req(10, 0, 40_000), tool_result(16),
                        req(17, 40_000, 120_000, took=420), tool_result(17 + 420.4),
                        req(17 + 421, 30_000, 131_000)]
        for steps in (tool_wait_agent(), long_request, warm_agent()):
            with self.subTest(causes=[r["cause"] for r in self.reqs(steps) if r["rewrite"]]):
                self.assertEqual(self.with_embers(steps)["change_usd"], 0.0)

    def test_a_relay_without_embers_rewrites_after_5_minutes(self):
        reqs = self.reqs(relay_agent(1800), "embers:relay")
        d = rw.without_embers(reqs, self.prices, True)
        self.assertEqual(d["requests_cold"], 1)
        self.assertAlmostEqual(d["relay_1h_usd"], (W1 - W5) * 43_500 * USD)
        self.assertAlmostEqual(d["cold_usd"], (W5 - R) * 43_000 * USD)
        measured = sum(self.prices.input_cost(r) for r in reqs)
        self.assertAlmostEqual(measured + d["change_usd"],
                               (9 + R * 40_000 + W5 * (43_500 + 43_000)) * USD)

    def test_a_relay_gap_under_5_minutes_or_past_the_hour_changes_only_the_write_price(self):
        for gap in (200, 300, 3600, 4000):
            with self.subTest(gap=gap):
                fx = Fixture()
                try:
                    reqs = rw.classify(rw.read_agent(
                        fx.agent("r1", relay_agent(gap), "embers:relay")))["requests"]
                    d = rw.without_embers(reqs, self.prices, True)
                    self.assertEqual(d["requests_cold"], 0)
                    self.assertAlmostEqual(d["change_usd"], -(W1 - W5) * 43_500 * USD)
                finally:
                    fx.close()

    def test_a_relay_that_went_cold_anyway_is_not_counted_twice(self):
        steps = [req(10, 0, 0, write_1h=40_000), tool_result(16),
                 req(17, 40_000, 0, write_1h=160_000, tool=False),
                 message(17 + 1799), req(17 + 1800, 0, 0, write_1h=201_000)]
        d = rw.without_embers(self.reqs(steps, "embers:relay"), self.prices, True)
        self.assertEqual(d["requests_cold"], 0)

    def test_a_5_minute_agent_with_embers_is_left_as_it_ran(self):
        d = rw.without_embers(self.reqs(notification_agent()), self.prices, False)
        self.assertEqual((d["change_usd"], d["requests_cold"]), (0.0, 0))


class SavingsTable(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.prices = rw.Prices(thresholds.load())

    def tearDown(self):
        self.fx.close()

    def cost(self, agent_id):
        path = next(self.fx.root.glob(f"**/agent-{agent_id}.jsonl"))
        return sum(self.prices.input_cost(r)
                   for r in rw.classify(rw.read_agent(path))["requests"])

    def two_groups(self):
        # off: an agent sent work back and a warm one; on: a relay and a 5-minute agent with
        # the rule, a day later
        self.fx.agent("off1", resume_agent())
        self.fx.agent("off2", warm_agent())
        day = 86_400
        self.fx.agent("on1", [dict(s, at=s["at"] + day) for s in relay_agent(1800)],
                      "embers:relay")
        self.fx.agent("on2", [dict(s, at=s["at"] + day) for s in tool_wait_agent()],
                      extra=[hook_context()])

    def test_the_four_cells_by_hand(self):
        self.two_groups()
        s = self.fx.summary("--savings")["savings"]
        off, on = s["off"], s["on"]
        self.assertEqual(s["detection"], "transcript")
        self.assertEqual((off["agents"], on["agents"]), (2, 2))
        self.assertEqual((off["would_be_relays"], on["relays"], on["with_rule"]), (1, 1, 1))
        off_measured = self.cost("off1") + self.cost("off2")
        on_measured = self.cost("on1") + self.cost("on2")
        off_change = ((W1 - W5) * 401_000 - (W1 - R) * 200_000) * USD
        on_change = ((W5 - R) * 43_000 - (W1 - W5) * 43_500) * USD
        self.assertAlmostEqual(off["without_usd"], off_measured)
        self.assertAlmostEqual(off["with_usd"], off_measured + off_change)
        self.assertAlmostEqual(on["with_usd"], on_measured)
        self.assertAlmostEqual(on["without_usd"], on_measured + on_change)
        for g in (off, on):
            self.assertAlmostEqual(g["saved_usd"], g["without_usd"] - g["with_usd"])
            self.assertAlmostEqual(g["saved_share"], g["saved_usd"] / g["without_usd"])

    def test_the_table_prints_the_json_figures(self):
        self.two_groups()
        s = self.fx.summary("--savings")["savings"]
        code, out = self.fx.run("--savings")
        self.assertEqual(code, 0)
        lines = out.splitlines()

        def row(name):
            return next(line for line in lines if line.startswith(name))[len(name):].split()

        usd = lambda x: f"${x:,.2f}"  # noqa: E731
        self.assertEqual(row("without Embers"), [usd(s["off"]["without_usd"]), "measured",
                                                 usd(s["on"]["without_usd"]), "computed"])
        self.assertEqual(row("with Embers"), [usd(s["off"]["with_usd"]), "computed",
                                              usd(s["on"]["with_usd"]), "measured"])
        self.assertEqual(row("Embers saved"),
                         [usd(s["off"]["saved_usd"]), f"{s['off']['saved_share']:.1%}",
                          usd(s["on"]["saved_usd"]), f"{s['on']['saved_share']:.1%}"])
        self.assertEqual(row("subagents"), ["2", "2"])
        self.assertIn("What it cannot see", out)
        text = " ".join(line.strip() for line in lines)
        self.assertIn("cold resumes the guide replaced with fresh agents, make the real saving "
                      "larger", text)
        self.assertIn("polls the waiting rule kept up past the break-even", text)
        self.assertIn("make it smaller", text)
        self.assertNotIn("lower bound", out)

    def test_the_part_lines_print_the_json_figures(self):
        # every part nonzero and distinct: a would-be relay, a notification polled, a relay with
        # a late request, a 5-minute agent with the rule
        self.two_groups()
        self.fx.agent("off3", notification_agent(1001))
        s = self.fx.summary("--savings")["savings"]
        off, on = s["off"], s["on"]
        _, out = self.fx.run("--savings")
        usd = lambda x: f"${x:,.2f}"  # noqa: E731
        expected = [
            f"  {off['would_be_relays']} subagents sent work back would have been the relay: "
            f"{usd(off['relay_1h_usd'])} more for 1-hour writes, {usd(off['relay_read_usd'])} "
            f"less as {off['relay_rewrites_read']} rewrites under an hour read their cache",
            f"  {off['notifications_polled']} rewrites on a system notification polled instead: "
            f"{usd(off['notification_usd'])} less in rewrites, {usd(off['poll_usd'])} more for "
            f"{off['polls']} polls",
            f"  {on['relays']} relays: {usd(on['relay_1h_usd'])} less with 5-minute writes, "
            f"{usd(on['cold_usd'])} more as {on['requests_cold']} requests after a gap of 5 to "
            "60 minutes rewrite their cache",
            f"  {on['with_rule']} subagents with the waiting rule, left as they ran"]
        lines = out.splitlines()
        for line in expected:
            with self.subTest(line=line[:40]):
                self.assertIn(line, lines)
        # the figures the lines carry are all different, so a swapped key shows
        figures = [usd(off[k]) for k in ("relay_1h_usd", "relay_read_usd", "notification_usd",
                                         "poll_usd")] + [usd(on[k]) for k in ("relay_1h_usd",
                                                                              "cold_usd")]
        self.assertEqual(len(set(figures)), len(figures))
        counts = [off[k] for k in ("would_be_relays", "relay_rewrites_read",
                                   "notifications_polled", "polls")]
        self.assertEqual(counts, [1, 1, 1, 5])
        self.assertEqual((on["relays"], on["requests_cold"], on["with_rule"]), (1, 1, 1))
        self.assertNotIn("by date alone", out)
        self.assertNotIn("already on a 1-hour cache", out)

    def test_date_mode_accounts_for_subagents_without_the_rule(self):
        # on by date: the relay, the agent with the rule, and two agents without it
        self.two_groups()
        s = self.fx.summary("--savings", "--embers-on", "..2030-12-31")["savings"]["on"]
        self.assertEqual((s["agents"], s["relays"], s["with_rule"], s["without_rule"]),
                         (4, 1, 1, 2))
        self.assertEqual(s["relays"] + s["with_rule"] + s["without_rule"], s["agents"])
        _, out = self.fx.run("--savings", "--embers-on", "..2030-12-31")
        self.assertIn("  2 subagents on by date alone, neither the relay nor with the rule in "
                      "their transcript, left as they ran", out.splitlines())

    def test_dates_in_place_of_the_transcript(self):
        self.two_groups()
        day1 = (T0 + timedelta(days=1)).date().isoformat()
        by_date = self.fx.summary("--savings", "--embers-on", f"{day1}..")["savings"]
        by_rule = self.fx.summary("--savings")["savings"]
        self.assertEqual(by_date["detection"], "dates")
        for key in ("without_usd", "with_usd"):
            for group in ("off", "on"):
                self.assertAlmostEqual(by_date[group][key], by_rule[group][key])
        # all on from the start: the off column is empty
        s = self.fx.summary("--savings", "--embers-on", "..2030-12-31")["savings"]
        self.assertEqual((s["off"]["agents"], s["on"]["agents"]), (0, 4))
        self.assertIsNone(s["off"]["saved_share"])
        code, out = self.fx.run("--savings", "--embers-on", "..2030-12-31")
        self.assertEqual(code, 0)
        self.assertIn("$0.00 -", out)

    def test_repeated_ranges_and_the_window(self):
        self.two_groups()
        day1 = (T0 + timedelta(days=1)).date().isoformat()
        day2 = (T0 + timedelta(days=2)).date().isoformat()
        s = self.fx.summary("--savings", "--embers-on", f"..{T0.date().isoformat()}",
                            "--embers-on", f"{day1}..{day2}", "--since", day1)["savings"]
        self.assertEqual((s["off"]["agents"], s["on"]["agents"]), (0, 2))
        self.assertEqual(len(s["embers_on"]), 2)
        # the window left open: a request is on by the first range as well as by the last.
        # Day 0's agents ran requests at 10, 17 and 24 or 1218 s; on until 15 s, their first
        # request is on and the rest off; day 1's two agents are on by the second range.
        s = self.fx.summary("--savings", "--embers-on", "..2030-01-07T09:00:15Z",
                            "--embers-on", f"{day1}..")["savings"]
        self.assertEqual((s["on"]["agents"], s["on"]["requests"]), (4, 2 + 6))
        self.assertEqual((s["off"]["agents"], s["off"]["requests"]), (2, 4))

    def test_options_that_do_not_go_together_are_refused(self):
        for args in (["--embers-on", "2030-01-01.."], ["--savings", "--split", "2030-01-08"],
                     ["--savings", "--list"], ["--savings", "--embers-on", "2030-01-01"],
                     ["--savings", "--embers-on", "2030-01-08..2030-01-01"],
                     ["--savings", "--embers-on", "soon.."],
                     ["--savings", "--since", "2030-02-01", "--until", "2030-01-01"],
                     ["--since", "2030-01-01", "--until", "2030-01-01"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                rw.main(["--root", str(self.fx.root), *args])

    def test_an_empty_root_still_prints_the_table(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, out = self.fx.run("--savings")
        self.assertEqual(code, 0)
        self.assertIn("Embers saved", out)


if __name__ == "__main__":
    unittest.main()
