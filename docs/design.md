# How Embers works, and why

The detail behind the README: the cache facts Embers relies on, how each hook decides what to
inject, the design choices with their reasons, and the sources. [math.md](math.md) derives the
numbers.

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

## How each part decides

The README says what each part does and what you will see. This section adds the detail: what
the relay is made of, how the waiting rule's hook decides whom to give the rule, and the guide's
full text.

### The relay agent

A general-purpose agent with `experimental: { cacheTtl: 1h }`. It has no `tools` field, so it
inherits every tool, and no `model` field, so it runs on the model of the session that starts it.
Its body is short: its expertise comes from each call's brief, and it ends with a report that
keeps fact and inference apart.

Its description tells the main session when to pick it: work that may come back to the same
agent after the 5-minute cache would lapse, such as review rounds, ping-pong between the main
session and the agent, or follow-ups deferred to later. Not one-shot tasks, where the 5-minute
agent is cheaper.

### The waiting rule

The rule itself, and the loop it asks for, are in the
[README](../README.md#the-waiting-rule-for-5-minute-subagents-only).

The rule's first line is an exit: an agent whose own instructions say its prompt cache lasts
1 hour ignores the rule. The relay's body says so ("Your prompt cache lasts 1 hour."), so a
relay the hook misjudges still does not poll for nothing.

The hook works out the effective lifetime with the precedence in [How the cache
works](#how-the-cache-works). It reads the environment, the `subagentPromptCacheTtl` setting in
the managed, project-local, project and user settings files, and the agent's own file: the
managed, project and user `agents` directories, and for a plugin agent (`plugin:name`) the
plugin's `agents` directory plus any files and folders its manifest's `agents` field lists, as
Claude Code loads both. A 1-hour agent, the relay or any agent of your own with `cacheTtl: 1h`,
gets nothing. Anything the hook cannot read counts as 5 minutes, and the rule goes in.

Main conversations never get the rule: `SubagentStart` fires only for subagents (spawned,
resumed, and in-process teammates), not for the main conversation of a session, background
sessions included.

**If you write 1-hour agents of your own** (optional): add this line to the agent's body,
below the frontmatter, where the agent reads it:

```
Your prompt cache lasts 1 hour.
```

Should the hook ever misjudge that agent and inject the rule, the rule's exit lets the agent
ignore it. Embers never reads your agent files for this line and never changes them.

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

Every number in both texts comes from [config/prices.toml](../config/prices.toml) through
[tools/thresholds.py](../tools/thresholds.py), so a price or a threshold changed there flows into
what the sessions read.

## Design choices, and why

- **Always refresh while waiting, with no cutoff.** Below about an hour of waiting, polling every
  170 seconds is also the cheaper choice: on Opus 5.5 with a 300k context, letting the cache go
  cold once costs as much as about 22 polls, an hour of waiting. Past that, each further hour of
  polling costs about 0.95 of one rewrite, about $1.37 at 300k. A 2-hour wait costs about $2.73
  in polls against $1.44 for one rewrite, so the rule pays about $1.30 there for being simple:
  one rule, no clock to keep. ([math.md](math.md#3-waiting-on-a-tool), section 3.)
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
  choice. ([math.md](math.md#2-a-5-minute-or-a-1-hour-agent), section 2.)
- **Thresholds in tokens, not in a share of the context window.** What decides between a cold
  resume and a fresh agent is how much work a fresh agent would have to redo, and that does not
  depend on the window. A percentage would move whenever the window changes. 150k and 350k are
  15% and 35% of today's 1M window. ([math.md](math.md#1-send-back-or-start-fresh), section 1.)
- **Leave `subagentPromptCacheTtl` unset.** Setting it, even to `5m`, outranks every agent's
  `cacheTtl`, so the relay would quietly get 5 minutes. The same holds for
  `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`. Embers sets neither. If you set one where the hook
  can read it (the environment, or a settings file listed in [The waiting rule](#the-waiting-rule)),
  the hook follows it: every
  subagent gets the lifetime you chose, and the waiting rule goes to all of them or to none.
  One exception: with `5m`, the relay still reads "Your prompt cache lasts 1 hour" in its own
  body, takes the rule's exit, and waits without it.
- **Hooks, not a skill.** A skill loads only when the model decides to use it; a waiting rule has
  to be in force all the time.
- **Unknown counts as 5 minutes.** A rule injected by mistake costs a few hundred tokens; a
  missing one costs a full rewrite.

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
- [math.md](math.md): every number above, derived.
