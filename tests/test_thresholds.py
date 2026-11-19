"""Each formula against a hand-computed case, and the price file driving the result.

Run: python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import thresholds as t  # noqa: E402

# Opus 5.5 at the defaults: w5 1.25, w1 2, r 0.05, o 5, h 0.1, k 10, S 36k, F 30k, N 1M.
W5, W1, R, O, H, K, S, F, N = 1.25, 2.0, 0.05, 5.0, 0.1, 10, 36_000, 30_000, 1_000_000


def rows_of(cfg):
    return {key: value for key, value, _, _ in t.compute(t.params(cfg))}


class Formulas(unittest.TestCase):
    def test_warm_window(self):
        self.assertEqual(t.warm_window(300, 60), 240)

    def test_redo_factor(self):
        # 1 + 5*0.1/(1.25 + 10*0.05) = 1 + 0.5/1.75
        self.assertAlmostEqual(t.redo_factor(W5, R, O, H, K), 1 + 0.5 / 1.75)

    def test_floor(self):
        self.assertEqual(t.floor_tokens(S, F), 66_000)
        # the window share is a derived view only
        self.assertAlmostEqual(t.share_of_window(t.floor_tokens(S, F), N), 0.066)

    def test_implied_lost_and_back(self):
        g = 1 + 0.5 / 1.75
        # thresholds in tokens: (150k - 66k)/g = 84k * 1.75/2.25 = 65,333.3
        self.assertAlmostEqual(t.implied_lost(150_000, S, F, g), 84_000 * 1.75 / 2.25)
        # threshold_for is its inverse, in tokens
        self.assertAlmostEqual(t.threshold_for(84_000 * 1.75 / 2.25, S, F, g), 150_000)

    def test_thresholds_do_not_move_with_the_window(self):
        # the lost work implied by X depends on X in tokens, not on the window
        cfg = t.load()
        before = rows_of(cfg)
        cfg["context"]["window_tokens"] = 200_000
        after = rows_of(cfg)
        self.assertAlmostEqual(before["lost_work_at_X"], after["lost_work_at_X"])
        self.assertAlmostEqual(after["X_share"], 0.75)

    def test_rule_numbers(self):
        n = t.load_rule_numbers()
        self.assertEqual(n["warm_5m_s"], 240)
        self.assertEqual(n["warm_1h_s"], 3540)
        self.assertEqual(n["chunk_s"], 170)
        self.assertEqual(n["bash_timeout_ms"], 200_000)
        self.assertEqual(n["resume_cold_below"], 150_000)
        self.assertEqual(n["resume_cold_unique_below"], 350_000)
        self.assertEqual(n["poll_interval_s"], 15)
        self.assertEqual(n["ttl5_s"], 300)
        self.assertEqual(n["ttl1_s"], 3600)
        # the default model's 5-minute write over its read: Opus 5.5, 1.25/0.05
        self.assertEqual(n["write_over_read"], 25)

    def test_warm_ceiling(self):
        # no lost work: 1.75 * 66k / 0.55 = 210,000
        self.assertAlmostEqual(t.warm_ceiling(0, W5, R, O, H, K, S, F), 210_000)
        # 10k lost: (115,500 + 10k * 2.25)/0.55 = 250,909.09
        self.assertAlmostEqual(t.warm_ceiling(10_000, W5, R, O, H, K, S, F), 138_000 / 0.55)

    def test_one_hour_p(self):
        self.assertAlmostEqual(t.one_hour_p(W1, W5, R), 0.75 / 1.2)          # 0.625
        self.assertAlmostEqual(t.one_hour_p(W1, W5, 0.1), 0.75 / 1.15)       # 0.652
        # two resumes' worth of context, 10% of gaps past the hour: 0.75*(0.5 + 0.1)/1.2
        self.assertAlmostEqual(t.one_hour_p(W1, W5, R, L=2, z=0.1), 0.375)

    def test_rewrite_share(self):
        # matches the "about 40%" of anthropics/claude-code#110403 at r = 0.1
        self.assertAlmostEqual(t.rewrite_share(W1, W5, 0.1), 0.75 / 1.9)
        self.assertAlmostEqual(t.rewrite_share(W1, W5, R), 0.75 / 1.95)

    def test_polls(self):
        # no tail, no output: (w5 - r)/r = 24
        self.assertAlmostEqual(t.polls_breakeven(W5, R, 300_000), 24)
        # 300k context, 300 tail, 150 output: 360,000/(15,000 + 375 + 750)
        self.assertAlmostEqual(t.polls_breakeven(W5, R, 300_000, 300, O, 150), 360_000 / 16_125)
        self.assertAlmostEqual(t.poll_cost(W5, R, 300_000, 300, O, 150), 16_125)

    def test_measured_case(self):
        rows = rows_of(t.load())
        self.assertAlmostEqual(rows["measured_cold_usd"], 7.1 * 1.25 * 4)   # $35.50
        self.assertAlmostEqual(rows["measured_warm_usd"], 7.1 * 0.05 * 4)   # $1.42
        self.assertAlmostEqual(rows["measured_1h_breakeven_writes"], 7_100_000 * 1.2 / 0.75)


class PriceFile(unittest.TestCase):
    def test_changing_a_price_changes_the_threshold(self):
        text = t.PRICES.read_text()
        start = text.index("[models.opus-5-5]")
        at = text.index("read = 0.05", start)
        changed = text[:at] + "read = 0.1" + text[at + len("read = 0.05"):]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "prices.toml"
            path.write_text(changed)
            before, after = rows_of(t.load()), rows_of(t.load(path))
        self.assertAlmostEqual(before["one_hour_p_single"], 0.625)
        self.assertAlmostEqual(after["one_hour_p_single"], 0.75 / 1.15)
        self.assertAlmostEqual(before["polls_simple"], 25)
        self.assertAlmostEqual(after["polls_simple"], 12.5)

    def test_cli_json_and_overrides(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = t.main(["--json", "--read", "0.1", "--set", "context.window_tokens=200000"])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["inputs"]["N"], 200_000)
        self.assertAlmostEqual(data["thresholds"]["polls_simple"]["value"], 12.5)
        self.assertEqual(data["thresholds"]["fresh_floor"]["value"], 66_000)
        self.assertAlmostEqual(data["thresholds"]["fresh_floor_share"]["value"], 0.33)

    def test_unknown_key_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            t.main(["--set", "agent.no_such_number=1"])
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
