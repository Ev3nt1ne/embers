# Embers

A Claude Code plugin that keeps subagent prompt caches from going cold. README.md says what it
does and why; docs/math.md derives every number.

This repository is public: nothing private goes in it (no personal
paths, names, transcripts or account details). Only the owner pushes.

## Layout

- `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`: the manifest, and a one-plugin
  marketplace whose plugin is the repository root.
- `agents/relay.md`: `embers:relay`, the general-purpose agent with a 1-hour cache.
- `hooks/hooks.json`: two hooks. `subagent_start.py` injects the waiting rule into 5-minute
  subagents; `session_start.py` gives main sessions the agent guide. `ttl.py` decides a
  subagent's effective cache lifetime; `rules.py` holds both texts.
- `config/prices.toml`: every number, with its source. `tools/thresholds.py` computes from it,
  and `rule_numbers()` feeds the texts.
- `tools/rewrites.py`: counts and prices cold rewrites in a user's own subagent transcripts, for
  a before/after measurement; reads only, never imported by the hooks.
- `docs/`: `math.md` derives every number; `design.md` holds the cache facts, how each hook
  decides, the design choices and the sources; `limits.md` every known limit; `measuring.md` the
  measured case, how to run `tools/rewrites.py`, and how to recompute the thresholds. The README
  is for deciding and installing, and links to them; a section moved out keeps its heading there,
  so outside links to its anchor still land.
- `tests/`: unittest, standard library.

Python 3 standard library only. The hooks must never block a session: every error exits 0.

## Done means

```
python3 -m unittest discover -s tests -v
python3 tools/thresholds.py
claude plugin validate .
```
