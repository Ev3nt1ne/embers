#!/usr/bin/env python3
"""Recompute Embers' cost thresholds from config/prices.toml.

Every cost is in base-input-token equivalents: one token at the model's base input price.
docs/math.md derives each formula. Standard library only.

    python3 tools/thresholds.py                       # table
    python3 tools/thresholds.py --json                # JSON
    python3 tools/thresholds.py --read 0.1            # what if reads cost 0.1x
    python3 tools/thresholds.py --set context.window_tokens=200000 --model sonnet-5-5
"""
import argparse
import json
import sys
import tomllib
from pathlib import Path

PRICES = Path(__file__).resolve().parent.parent / "config" / "prices.toml"


# --- the formulas (docs/math.md, one function each) -----------------------------------------

def warm_window(ttl_s, margin_s):
    """Seconds after an agent's last activity during which its cache still counts as warm."""
    return ttl_s - margin_s


def chunk_headroom(ttl_s, margin_s, chunk_s):
    """Seconds a 5-minute lifetime leaves for the turn between two waiting chunks."""
    return ttl_s - margin_s - chunk_s


def redo_factor(w, r, o, h, k):
    """g: how much a lost token costs a fresh agent, against a token a cold resume rewrites."""
    return 1 + o * h / (w + k * r)


def floor_tokens(S, F):
    """The smallest context a fresh agent starts with: below it a cold resume always wins."""
    return S + F


def implied_lost(threshold, S, F, g):
    """D: the lost work (tokens a fresh agent must rebuild) at which resume-cold and fresh tie,
    for a threshold in tokens."""
    return (threshold - S - F) / g


def threshold_for(D, S, F, g):
    """X (or Y), in tokens, that the prices imply when a fresh agent must rebuild D tokens."""
    return S + F + g * D


def share_of_window(tokens, N):
    """A token count as a share of the context window: a derived view only (docs/math.md)."""
    return tokens / N


def warm_ceiling(D, w, r, o, h, k, S, F):
    """Context above which even a warm resume costs more than a fresh agent over k turns."""
    return ((w + k * r) * (S + F) + D * (w + o * h + k * r)) / ((1 + k) * r)


def one_hour_p(w1, w5, r, L=1.0, z=0.0):
    """p*: the chance of a 5-60 minute gap per resume above which a 1-hour agent is cheaper."""
    return (w1 - w5) * (1 / L + z) / (w5 - r)


def rewrite_share(w1, w5, r):
    """f*: share of a 5-minute agent's cache writes that are rewrites, where 1 hour breaks even."""
    return (w1 - w5) / ((w1 - w5) + (w5 - r))


def polls_breakeven(w5, r, C, t=0.0, o=0.0, y=0.0):
    """n*: poll turns that cost as much as letting the cache go cold once."""
    return (w5 - r) * C / (r * C + w5 * t + o * y)


def poll_cost(w5, r, C, t, o, y):
    """Cost of one poll turn: read the context, write the tail, generate the output."""
    return r * C + w5 * t + o * y


# --- reading the price file -------------------------------------------------------------------

def load(path=PRICES):
    with open(path, "rb") as f:
        return tomllib.load(f)


def apply_set(cfg, assignment):
    """Apply one --set a.b.c=value; the key must already exist in the price file."""
    dotted, _, raw = assignment.partition("=")
    keys = dotted.strip().split(".")
    node = cfg
    for key in keys[:-1]:
        node = node[key]
    if keys[-1] not in node:
        raise KeyError(dotted)
    old = node[keys[-1]]
    if isinstance(old, str):
        node[keys[-1]] = raw.strip()
    else:
        value = float(raw)
        node[keys[-1]] = int(value) if isinstance(old, int) and value.is_integer() else value


def params(cfg):
    """The named numbers the formulas use, flat."""
    model = cfg["models"][cfg["model"]]
    m, t, a = cfg["multipliers"], cfg["ttl"], cfg["agent"]
    return {
        "model": cfg["model"], "usd": model["base_usd_per_mtok"],
        "w5": m["write_5m"], "w1": m["write_1h"], "r": model["read"], "o": m["output"],
        "ttl5": t["five_minutes_s"], "ttl1": t["one_hour_s"],
        "margin": t["safety_margin_s"], "chunk": t["chunk_s"],
        "N": cfg["context"]["window_tokens"],
        "S": a["baseline_tokens"], "F": a["brief_files_tokens"],
        "k": a["follow_up_turns"], "h": a["output_per_context_token"],
        "X": cfg["rule"]["resume_cold_below_tokens"],
        "Y": cfg["rule"]["resume_cold_unique_below_tokens"],
        "bash_margin": t["bash_timeout_margin_s"], "poll_interval": t["poll_interval_s"],
        "L": cfg["one_hour"]["resume_load"], "z": cfg["one_hour"]["p_past_hour"],
        "t": cfg["poll"]["tail_tokens"], "y": cfg["poll"]["output_tokens"],
        "Cref": cfg["poll"]["reference_context_tokens"],
        "rewrites": cfg["measured"]["rewrites"], "Mtok": cfg["measured"]["rewrite_tokens"],
    }


def _ratio(value):
    """A price ratio as the texts quote it: float noise off, 12.5 kept (not rounded to 12)."""
    return round(value, 4)


def rule_numbers(p):
    """The numbers the injected rules quote (hooks/rules.py), all derived from the price file.

    write_over_read is the 5-minute write price over the read price of the default model.
    """
    return {
        "ttl5_s": p["ttl5"],
        "ttl1_s": p["ttl1"],
        "write_over_read": _ratio(p["w5"] / p["r"]),
        "warm_5m_s": warm_window(p["ttl5"], p["margin"]),
        "warm_1h_s": warm_window(p["ttl1"], p["margin"]),
        "chunk_s": p["chunk"],
        "bash_timeout_ms": (p["chunk"] + p["bash_margin"]) * 1000,
        "poll_interval_s": p["poll_interval"],
        "resume_cold_below": p["X"],
        "resume_cold_unique_below": p["Y"],
    }


def load_rule_numbers(path=PRICES):
    """Read the price file and return rule_numbers(); raises on a missing or broken file."""
    return rule_numbers(params(load(path)))


def compute(p):
    """Every threshold as (key, value, unit, formula) rows."""
    w5, w1, r, o = p["w5"], p["w1"], p["r"], p["o"]
    N, S, F, k, h = p["N"], p["S"], p["F"], p["k"], p["h"]
    usd = p["usd"] / 1e6
    g5 = redo_factor(w5, r, o, h, k)
    g1 = redo_factor(w1, r, o, h, k)
    DX = implied_lost(p["X"], S, F, g5)
    DY = implied_lost(p["Y"], S, F, g5)
    n_simple = w5 / r
    n_ref = polls_breakeven(w5, r, p["Cref"], p["t"], o, p["y"])
    per_poll = poll_cost(w5, r, p["Cref"], p["t"], o, p["y"])
    rewrite_ref = (w5 - r) * p["Cref"]
    cold, warm = w5 * p["Mtok"], r * p["Mtok"]
    rows = [
        # 1. send back or start fresh
        ("warm_window_5m_s", warm_window(p["ttl5"], p["margin"]), "s", "TTL5 - margin"),
        ("warm_window_1h_s", warm_window(p["ttl1"], p["margin"]), "s", "TTL1h - margin"),
        ("redo_factor_g", g5, "x", "1 + o*h/(w5 + k*r)"),
        ("fresh_floor", floor_tokens(S, F), "tokens", "S + F"),
        ("fresh_floor_share", share_of_window(floor_tokens(S, F), N), "of window",
         "(S + F)/N, derived"),
        ("X_share", share_of_window(p["X"], N), "of window", "X/N, derived"),
        ("Y_share", share_of_window(p["Y"], N), "of window", "Y/N, derived"),
        ("lost_work_at_X", DX, "tokens", "(X - S - F)/g"),
        ("lost_work_at_Y", DY, "tokens", "(Y - S - F)/g"),
        ("lost_work_at_X_1h_agent", implied_lost(p["X"], S, F, g1), "tokens",
         "(X - S - F)/g, g with w1"),
        ("warm_ceiling_no_lost_work", warm_ceiling(0, w5, r, o, h, k, S, F), "tokens",
         "(w5 + k*r)(S + F)/((1 + k)*r)"),
        ("warm_ceiling_at_X_lost_work", warm_ceiling(DX, w5, r, o, h, k, S, F), "tokens",
         "[(w5 + k*r)(S + F) + D_X(w5 + o*h + k*r)]/((1 + k)*r)"),
        # 2. 5-minute or 1-hour agent
        ("one_hour_p_single", one_hour_p(w1, w5, r), "probability", "(w1 - w5)/(w5 - r)"),
        ("one_hour_p", one_hour_p(w1, w5, r, p["L"], p["z"]), "probability",
         "(w1 - w5)(1/L + z)/(w5 - r)"),
        ("one_hour_rewrite_share", rewrite_share(w1, w5, r), "of writes",
         "(w1 - w5)/((w1 - w5) + (w5 - r))"),
        # 3. waiting on a tool
        ("chunk_headroom_s", chunk_headroom(p["ttl5"], p["margin"], p["chunk"]), "s",
         "TTL5 - margin - chunk"),
        ("polls_simple", n_simple, "polls", "w5/r"),
        ("wait_simple_min", n_simple * p["chunk"] / 60, "min", "w5/r * chunk"),
        ("polls_at_ref", n_ref, "polls", "(w5 - r)C/(r*C + w5*t + o*y)"),
        ("wait_at_ref_min", n_ref * p["chunk"] / 60, "min", "n* * chunk"),
        ("refresh_hour_in_rewrites", 3600 / p["chunk"] * per_poll / rewrite_ref, "rewrites",
         "(3600/chunk)(r*C + w5*t + o*y)/((w5 - r)C)"),
        ("refresh_hour_usd_at_ref", 3600 / p["chunk"] * per_poll * usd, "USD",
         "(3600/chunk)(r*C + w5*t + o*y) * base"),
        # the owner's measured case
        ("measured_cold_usd", cold * usd, "USD", "M*w5 * base"),
        ("measured_warm_usd", warm * usd, "USD", "M*r * base"),
        ("measured_avoidable_usd", (cold - warm) * usd, "USD", "M(w5 - r) * base"),
        ("measured_1h_breakeven_writes", (w5 - r) * p["Mtok"] / (w1 - w5), "tokens",
         "M(w5 - r)/(w1 - w5)"),
    ]
    return rows


def fmt(value, unit):
    if unit in ("tokens",):
        return f"{value:,.0f}"
    if unit in ("of window", "of writes", "probability"):
        return f"{value:.1%}"
    if unit == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--prices", default=str(PRICES), help="price file (default: config/prices.toml)")
    ap.add_argument("--model", help="price this model (a key under [models])")
    ap.add_argument("--read", type=float, help="override the read multiplier of the model priced")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override any number, e.g. --set ttl.chunk_s=120 (repeatable)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)

    cfg = load(args.prices)
    try:
        if args.model:
            apply_set(cfg, f"model={args.model}")
        for assignment in args.set:
            apply_set(cfg, assignment)
        if args.read is not None:
            cfg["models"][cfg["model"]]["read"] = args.read
        p = params(cfg)
    except (KeyError, ValueError) as e:
        ap.error(f"unknown key or bad value: {e}")
    rows = compute(p)

    if args.json:
        out = {"inputs": p, "thresholds": {key: {"value": v, "unit": u, "formula": f}
                                           for key, v, u, f in rows}}
        print(json.dumps(out, indent=2))
        return 0
    print(f"model {p['model']}: read {p['r']}x, writes {p['w5']}x (5m) {p['w1']}x (1h), "
          f"window {p['N']:,}, S {p['S']:,}, F {p['F']:,}, k {p['k']}, X {p['X']:,}, Y {p['Y']:,}")
    width = max(len(key) for key, *_ in rows)
    for key, value, unit, formula in rows:
        print(f"{key:<{width}}  {fmt(value, unit):>12} {unit:<11}  {formula}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
