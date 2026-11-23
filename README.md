<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/embers-lockup-color-dark.svg">
    <img alt="Embers" src="assets/embers-lockup-color.svg" width="380">
  </picture>
</p>

# Embers

A Claude Code plugin that keeps subagent prompt caches from going cold.

Claude Code gives subagents a 5-minute prompt cache, even on a subscription where the main
conversation gets an hour. A subagent that waits longer than that misses its cache: one blocked on
a long Bash command such as a test suite, or one resumed with `SendMessage` after its manager
comes back to it late. Its next request re-writes its whole context at about 25 times the price
of reading it, and on a subscription that comes out of your usage limit. Embers adds three
things: a general-purpose agent with a 1-hour cache for work that comes back late, a waiting rule
that keeps 5-minute subagents warm while a long job runs, and a short guide in every main session
on which agent to use and when to resume one or start a fresh one.

## The problem

Claude Code re-sends an agent's whole context on every request, and prompt caching is what keeps
that cheap: the unchanged prefix is read from the cache instead of being processed again. The
cache expires after a gap with no requests. For subagents the gap is 5 minutes, even on a
subscription where the main conversation gets an hour.

Three patterns blow through it:

- **A subagent blocked on a tool.** It runs a 15-minute test suite in one Bash call, or ends its
  turn to wait for a background job. The first request after the wait rewrites everything.
- **Work sent back late.** The main session hands a finished subagent a follow-up 20 minutes
  later. The resume rewrites the agent's whole context.
- **A response longer than the cache.** The lifetime counts from the start of a request, so a
  single response that takes over 5 minutes to generate, such as a large file written in one
  call, leaves the cache expired for the very next request, with no pause at all.

A measured case, from one user's two weeks of ordinary work: 117 subagents rewrote 19 cold
caches of 150k to 620k tokens, about 7.1M tokens in all. At Opus 5.5 list prices that is about
$35.50, of which about $34 would have been avoided had each found its cache warm. A recount of
the same transcripts (2026-11-05 to 2026-11-19) asked what came before each rewrite. It
counted as a cold rewrite any subagent request that wrote at least 100k tokens to the cache while
reading at least 100k fewer than the request before it had cached, and found 24 in 134 subagents,
about 8.5M tokens:

- 13 (4.9M tokens) were work sent back, 6 to 117 minutes after the agent's previous request began (a little
  less from its hand-back);
- 4 (1.4M) came when an agent that had ended its turn was woken by a system notification, 1 to
  11 minutes later;
- 7 (2.2M) followed a single response of 28k to 63k output tokens that ran 5 to 8 minutes.

None followed a wait on a tool result. Public reports show the first pattern at larger scale: in
one, 190 rewrites of over 100k tokens each followed test suites that ran 10 to 15 minutes and
drained 54% of a weekly limit in under a day
([#103309](https://github.com/anthropics/claude-code/issues/103309)).

## How the cache works

Facts from the Claude Code and API docs (checked 2026-11-19):

- **Two lifetimes.** 5 minutes or 1 hour. Every request that reads the cache resets the timer,
  at no extra cost. The lifetime counts from the *start* of the request, so a slow turn eats
  into it.
- **Prices, as multiples of the model's base input price.** A write to a 5-minute cache costs
  1.25, a write to a 1-hour cache 2, a read 0.05 on Opus 5.5 and Sonnet 5.5 (0.1 on most other
  models). Output costs 5.
- **Who gets which lifetime.** On a subscription within its plan usage, the main conversation
  gets 1 hour and everything else, subagents included, gets 5 minutes. With an API key, a cloud
  provider, or a subscription drawing on usage credits, everything gets 5 minutes.
- **How to change it**, first match wins:
  1. `FORCE_PROMPT_CACHING_5M=1`
  2. the environment variable `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`
  3. the setting `subagentPromptCacheTtl`
  4. the agent file's `experimental: { cacheTtl: 1h }`, read from user, project and plugin agent
     files only
  5. `ENABLE_PROMPT_CACHING_1H=1`
  6. the default: 5 minutes for subagents
- **A resumed subagent** can read the cache its earlier run warmed, if the cache is still alive.

## What Embers does

### The relay agent: `embers:relay`

A general-purpose agent with `experimental: { cacheTtl: 1h }`. It has no `tools` field, so it
inherits every tool, and no `model` field, so it runs on the model of the session that starts it.
Its body is short: its expertise comes from each call's brief, and it ends with a report that
keeps fact and inference apart.

Its description tells the main session when to pick it: work that may come back to the same
agent after the 5-minute cache would lapse, such as review rounds, ping-pong between the main
session and the agent, or follow-ups deferred to later. Not one-shot tasks, where the 5-minute
agent is cheaper.

### The waiting rule, for 5-minute subagents only

A `SubagentStart` hook injects a short rule into every subagent whose effective cache lifetime
is 5 minutes: start a command that may take longer than 170 seconds in the background, then wait
for it in Bash calls of at most 170 seconds, with the Bash tool's timeout set to 200000 ms:

```
timeout 170 bash -c 'until <done-condition>; do sleep 15; done'; echo rc=$?
```

After each call, one short progress line from the log's `tail`, then wait again, however long
the job runs. Never one long blocking call, and never the turn ended to wait for a job.

The rule's first line is an exit: an agent whose own instructions say its prompt cache lasts
1 hour ignores the rule. The relay's body says so ("Your prompt cache lasts 1 hour."), so a
relay the hook misjudges still does not poll for nothing.

The hook works out the effective lifetime with the precedence above. It reads the environment,
the `subagentPromptCacheTtl` setting in the managed, project-local, project and user settings
files, and the agent's own file: the managed, project and user `agents` directories, and for a
plugin agent (`plugin:name`) the plugin's `agents` directory plus any files and folders its
manifest's `agents` field lists, as Claude Code loads both. A 1-hour agent, the relay or any agent of
your own with `cacheTtl: 1h`, gets nothing. Anything the hook cannot read counts as 5 minutes,
and the rule goes in.

Main conversations never get the rule: `SubagentStart` fires only for subagents (spawned,
resumed, and in-process teammates), not for the main conversation of a session, background
sessions included.

**If you write 1-hour agents of your own** (optional): add this line to the agent's body,
below the frontmatter, where the agent reads it:

```
Your prompt cache lasts 1 hour.
```

Should the hook
ever misjudge that agent and inject the rule, the rule's exit lets the agent ignore it. Embers
never reads your agent files for this line and never changes them.

### The guide for the main session

A `SessionStart` hook gives every main session, background sessions included, a few lines: why
(a cache expires 5 minutes after the last request, 1 hour for the relay and background sessions,
and a late message costs about 25 times a warm one), then which agent to use and when to send
work back to one:

- a **background session** for long project work that edits files, or must outlive the current
  session;
- a **5-minute subagent** for a one-shot task;
- **`embers:relay`** for work that may come back more than 240 seconds after the agent finishes;
- **sending work back**: run `date` when a subagent finishes and keep the time; resume it within
  240 seconds of its finish (59 minutes for the relay); later, resume it only if its
  `subagent_tokens` is under 150,000, or under 350,000 when it holds work not saved anywhere
  else; otherwise start a fresh agent with a brief that points to the files of the earlier work.
  An unknown finish time counts as a cold cache, and no `subagent_tokens` figure as over 150,000.

The main session can see a subagent's context size: a background subagent's completion notice
carries `<subagent_tokens>`, the size of its last request (measured against the agents' own
transcripts: within 1% of the last request, not a running total).

Every number in both texts comes from [config/prices.toml](config/prices.toml) through
[tools/thresholds.py](tools/thresholds.py), so a price or a threshold changed there flows into
what the sessions read.

## Design choices, and why

- **Always refresh while waiting, with no cutoff.** Below about an hour of waiting, polling every
  170 seconds is also the cheaper choice: on Opus 5.5 with a 300k context, letting the cache go
  cold once costs as much as about 22 polls, an hour of waiting. Past that, each further hour of
  polling costs about 0.95 of one rewrite, about $1.37 at 300k. A 2-hour wait costs about $2.73
  in polls against $1.44 for one rewrite, so the rule pays about $1.30 there for being simple:
  one rule, no clock to keep. ([docs/math.md](docs/math.md), section 3.)
- **170 seconds, not 4 minutes.** The lifetime counts from the start of the request that launched
  the wait, so the turn's own generation time is part of it; background subagents move a shell
  command to the background after 3 minutes; and the Bash tool's default timeout is 2 minutes,
  so the call needs an explicit one above the loop's.
- **The relay does not get the waiting rule.** A 1-hour cache survives the waits the rule exists
  for, and polling every 170 seconds would spend turns for nothing.
- **A 1-hour cache per agent, not for every subagent.** A 1-hour write costs 2 against 1.25. For
  one resume, a 1-hour agent pays off only when more than about 62% of its resumes come 5 to 60
  minutes late. Public measurements of a blanket 1 hour range from 8.6% worse to 10.5% better,
  depending on the workload. A dedicated agent for the work that comes back is the defensible
  choice. ([docs/math.md](docs/math.md), section 2.)
- **Thresholds in tokens, not in a share of the context window.** What decides between a cold
  resume and a fresh agent is how much work a fresh agent would have to redo, and that does not
  depend on the window. A percentage would move whenever the window changes. 150k and 350k are
  15% and 35% of today's 1M window. ([docs/math.md](docs/math.md), section 1.)
- **Leave `subagentPromptCacheTtl` unset.** Setting it, even to `5m`, outranks every agent's
  `cacheTtl`, so the relay would quietly get 5 minutes. The same holds for
  `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`. Embers sets neither. If you set one where the hook
  can read it (the environment, or a settings file listed above), the hook follows it: every
  subagent gets the lifetime you chose, and the waiting rule goes to all of them or to none.
  One exception: with `5m`, the relay still reads "Your prompt cache lasts 1 hour" in its own
  body, takes the rule's exit, and waits without it.
- **Hooks, not a skill.** A skill loads only when the model decides to use it; a waiting rule has
  to be in force all the time.
- **Unknown counts as 5 minutes.** A rule injected by mistake costs a few hundred tokens; a
  missing one costs a full rewrite.

## Known limits

- **Usage credits.** While a subscription draws on usage credits, Claude Code ignores
  `cacheTtl: 1h` and drops even the main conversation to 5 minutes. The hooks cannot see this,
  so the relay goes uncovered by the waiting rule then, and the guide's 1-hour figures do not
  hold.
- **A wait over an hour still goes cold**, for the relay and for a main session alike.
- **A response longer than 5 minutes.** A subagent whose one response runs past its 5-minute
  lifetime, counted from the request's start, finds its cache gone on the next request. Neither
  the waiting rule nor the guide covers this: the agent never waits and never finishes. The
  relay's 1-hour cache does. In the recount above, 7 of the 24 rewrites were of this kind.
- **Resumes inside the lifetime may still miss.** Open reports show a resumed or woken subagent
  rewriting its context inside a 1-hour lifetime
  ([#87215](https://github.com/anthropics/claude-code/issues/87215),
  [#94728](https://github.com/anthropics/claude-code/issues/94728)); the second ties it to the
  agent's thinking blocks missing from the resumed history. Untested on the current
  version. To check the relay, read its transcript: `ephemeral_1h_input_tokens` should be above
  zero, and a resume 10 to 50 minutes later should show `cache_read_input_tokens` close to the
  previous context size.
- **What the hook cannot see.** A `--settings` flag, `--agents` JSON that shadows an agent file,
  agents in `--add-dir` directories, server-managed settings (set in an organization's admin
  console), MDM policies (a macOS profile or the Windows registry), and, on WSL, managed
  settings inherited from Windows. The hook reads only the managed settings file and its
  drop-in folder. Where one of these applies, the hook's answer may be wrong. In particular, an
  organization that sets `subagentPromptCacheTtl` to `5m` through server-managed settings, MDM
  or Windows gives the relay 5 minutes while the hook still sees its `cacheTtl: 1h`: the relay
  is left without the waiting rule.
- **Time.** The main session sees no clock between turns. The guide tells it to run `date` when
  an agent finishes and keep the time, and to count a cache as cold when it never took that
  time.
- **Python 3.11 or later** for the price file (`tomllib`). With an older Python, the hooks fall
  back to built-in copies of today's numbers, which a test keeps equal to the price file.
- `python3` must be on the `PATH`; the hooks are not set up for Windows without it.

## Install

From a local checkout:

```
claude plugin marketplace add /path/to/embers
claude plugin install embers@embers
```

The repository is its own one-plugin marketplace (`.claude-plugin/marketplace.json`), and a
plugin from a marketplace added from a local path loads in place: edits take effect at the next
session or after `/reload-plugins`. Check the manifest with `claude plugin validate .`.

Leave `subagentPromptCacheTtl` and `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` unset.

## Recompute the thresholds

1. Change a number in [config/prices.toml](config/prices.toml); each entry says where it comes
   from, or that it is an assumption.
2. Run `python3 tools/thresholds.py` (add `--json` for JSON, `--model sonnet-5-5` for another
   model, `--set section.key=value` for any number).
3. Run the tests: `python3 -m unittest discover -s tests -v`.

The injected texts read the same file at every session start, so nothing else needs changing.

## Sources

- [Prompt caching in Claude Code](https://code.claude.com/docs/en/prompt-caching.md): lifetimes,
  who gets which, the precedence, resumed subagents.
- [Subagents](https://code.claude.com/docs/en/sub-agents.md): `experimental.cacheTtl`, scopes,
  resume.
- [Hooks](https://code.claude.com/docs/en/hooks.md): `SubagentStart`, `SessionStart`,
  `additionalContext`, the Agent tool's `totalTokens`.
- [Plugin components](https://code.claude.com/docs/en/plugins/components.md) and the
  [manifest reference](https://code.claude.com/docs/en/plugins-reference.md).
- [Settings reference](https://code.claude.com/docs/en/settings-reference.md) and
  [environment variables](https://code.claude.com/docs/en/env-vars.md).
- [Tools reference](https://code.claude.com/docs/en/tools-reference.md): Bash timeouts and moves
  to the background.
- [API prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) and
  [pricing](https://platform.claude.com/docs/en/about-claude/pricing).
- [Claude Code changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md):
  2.1.248 (`cacheTtl`), 2.1.318 (3-minute move to the background in background subagents).
- Reports: [#99808](https://github.com/anthropics/claude-code/issues/99808),
  [#110403](https://github.com/anthropics/claude-code/issues/110403),
  [#103309](https://github.com/anthropics/claude-code/issues/103309),
  [#87106](https://github.com/anthropics/claude-code/issues/87106),
  [#74318](https://github.com/anthropics/claude-code/issues/74318),
  [#94728](https://github.com/anthropics/claude-code/issues/94728),
  [#87215](https://github.com/anthropics/claude-code/issues/87215).
- [docs/math.md](docs/math.md): every number above, derived.

## Other projects

- [Airone Giallo](https://github.com/Ev3nt1ne/airone-giallo): skills and checks for running a long
  project with Claude Code agents.
- [specgraph](https://github.com/Ev3nt1ne/airone-giallo/tree/main/graph): Airone Giallo's
  specification graph, usable alone; it keeps a project's decisions, requirements and assumptions
  as a graph and computes which of them still hold.
- [cache-watch](https://github.com/Ev3nt1ne/cache-watch): warns you before your own Claude Code
  session's prompt cache goes cold. Embers is for subagents, cache-watch for the session you sit
  in.
- [grammar-guard](https://github.com/Ev3nt1ne/grammar-guard): grammar and spell check for the
  prompts you type or dictate into Claude Code.

## License

MIT, see [LICENSE](LICENSE).

Embers is not affiliated with the Ember.js project or Tilde Inc., nor with Anthropic.
