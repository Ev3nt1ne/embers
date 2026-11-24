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
cache expires after a gap with no requests, and three patterns blow through it:

- **A subagent blocked on a tool.** It runs a 15-minute test suite in one Bash call, or ends its
  turn to wait for a background job. The first request after the wait rewrites everything.
- **Work sent back late.** The main session hands a finished subagent a follow-up 20 minutes
  later. The resume rewrites the agent's whole context.
- **A response longer than the cache.** The lifetime counts from the start of a request, so a
  single response that takes over 5 minutes to generate, such as a large file written in one
  call, leaves the cache expired for the very next request, with no pause at all.

In one user's two weeks of ordinary work, 117 subagents rewrote 19 cold caches of 150k to 620k
tokens, about 7.1M tokens in all: about $35.50 at Opus 5.5 list prices, of which about $34 would
have been avoided had each found its cache warm. A later recount of that user's transcripts
found 24 cold rewrites: 13 were work sent back, 4 followed a system notification, 7 a single
response that ran 5 to 8 minutes, and none a wait on a tool result. In a public report, 190
rewrites of over 100k tokens each followed test suites that ran 10 to 15 minutes and drained 54%
of a weekly limit in under a day
([#103309](https://github.com/anthropics/claude-code/issues/103309)). The method and the full
figures: [docs/measuring.md](docs/measuring.md#the-measured-case).

## How the cache works

- A cache lives 5 minutes or 1 hour, counted from the *start* of the request that last read or
  wrote it. Every read resets the timer, at no extra cost.
- Subagents get 5 minutes. On a subscription within its plan usage the main conversation gets
  1 hour; with an API key, a cloud provider, or a subscription drawing on usage credits,
  everything gets 5 minutes.
- A write to a 5-minute cache costs 1.25 times the model's base input price, a write to a 1-hour
  cache 2, and a read 0.05 on Opus 5.5 and Sonnet 5.5 (0.1 on most other models).

The settings that change the lifetime, and which one wins:
[docs/design.md](docs/design.md#how-the-cache-works).

## What Embers does

### The relay agent: `embers:relay`

A general-purpose agent with `experimental: { cacheTtl: 1h }`, every tool, and the model of the
session that starts it. The main session picks it for work that may come back to the same agent
after the 5-minute cache would lapse: review rounds, ping-pong between the main session and the
agent, follow-ups deferred to later. One-shot tasks stay with the 5-minute agent, which is
cheaper for them.

### The waiting rule, for 5-minute subagents only

A `SubagentStart` hook injects a short rule into every subagent whose effective cache lifetime
is 5 minutes: start a command that may take longer than 170 seconds in the background, then wait
for it in Bash calls of at most 170 seconds, with the Bash tool's timeout set to 200000 ms:

```
timeout 170 bash -c 'until <done-condition>; do sleep 15; done'; echo rc=$?
```

After each call, one short progress line from the log's `tail`, then wait again, however long
the job runs. Never one long blocking call, and never the turn ended to wait for a job. A
1-hour agent, the relay or any agent of your own with `cacheTtl: 1h`, gets nothing, and main
conversations never get the rule. Anything the hook cannot read counts as 5 minutes, and the
rule goes in.

If you write 1-hour agents of your own, you may add this line to the agent's body, below the
frontmatter:

```
Your prompt cache lasts 1 hour.
```

Should the hook misjudge that agent, the rule's first line lets it ignore the rule. Embers
never reads your agent files for this line and never changes them.

### The guide for the main session

A `SessionStart` hook tells every main session, background sessions included, which agent to
use: a background session for long project work that edits files, a 5-minute subagent for a
one-shot task, and `embers:relay` for work that may come back more than 240 seconds after the
agent finishes. It also says when to resume a finished subagent and when to start a fresh one:
[docs/design.md](docs/design.md#the-guide-for-the-main-session).

## Install

From GitHub:

```
claude plugin marketplace add Ev3nt1ne/embers
claude plugin install embers@embers
```

Claude Code clones the repository over SSH, and over HTTPS when SSH fails. To update, run
`claude plugin marketplace update embers`, then `claude plugin uninstall embers@embers` and
install again: `claude plugin update embers@embers` installs a newer commit only when the
version in `.claude-plugin/plugin.json` has changed. To uninstall, run
`claude plugin uninstall embers@embers`, then `claude plugin marketplace remove embers`.

From a local checkout, to edit the plugin:

```
claude plugin marketplace add /path/to/embers
claude plugin install embers@embers
```

The repository is its own one-plugin marketplace (`.claude-plugin/marketplace.json`), and a
plugin from a marketplace added from a local path loads in place: edits take effect at the next
session or after `/reload-plugins`. Check the manifest with `claude plugin validate .`.

Leave `subagentPromptCacheTtl` and `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` unset: either one
outranks every agent's `cacheTtl`, so the relay would quietly get 5 minutes.

## Known limits

- While a subscription draws on usage credits, Claude Code ignores `cacheTtl: 1h`, so the
  relay gets 5 minutes and no waiting rule.
- A wait over an hour still goes cold, for the relay and for a main session alike.
- A single response longer than 5 minutes leaves a 5-minute subagent's cache cold; only the
  relay's 1-hour cache covers it.
- Resumes inside the lifetime may still miss
  ([#87215](https://github.com/anthropics/claude-code/issues/87215),
  [#94728](https://github.com/anthropics/claude-code/issues/94728)).
- Settings the hook cannot read, such as server-managed settings or a `--settings` flag, may
  make its answer wrong.
- The hooks need `python3` on the `PATH`.

All of them, with what to check: [docs/limits.md](docs/limits.md).

## Going further

[docs/math.md](docs/math.md) derives every number above.

### Design choices, and why

[docs/design.md](docs/design.md#design-choices-and-why) gives the reason for each choice, such as
the waiting rule's lack of a cutoff and its 170 seconds.

### Recompute the thresholds

Change a number in [config/prices.toml](config/prices.toml) and run
[tools/thresholds.py](tools/thresholds.py):
[docs/measuring.md](docs/measuring.md#recompute-the-thresholds).

### Measure your own

[tools/rewrites.py](tools/rewrites.py) counts and prices the cold rewrites in your own subagent
transcripts, before Embers and after: [docs/measuring.md](docs/measuring.md#measure-your-own).

### Sources

[docs/design.md](docs/design.md#sources) lists the docs, changelog entries and issue reports
behind each fact.

## More Claude Code projects by Ev3nt1ne

<table>
  <tr>
    <td width="66"><a href="https://github.com/Ev3nt1ne/airone-giallo"><img src="https://raw.githubusercontent.com/Ev3nt1ne/airone-giallo/main/brand/airone-standing-tight-circle-notte.svg" alt="Airone Giallo" width="40" height="40"></a></td>
    <td><a href="https://github.com/Ev3nt1ne/airone-giallo"><b>Airone Giallo</b></a><br>Skills and checks for running a long project with Claude Code agents.</td>
  </tr>
  <tr>
    <td width="66"><a href="https://github.com/Ev3nt1ne/airone-giallo/tree/main/graph"><img src="https://raw.githubusercontent.com/Ev3nt1ne/airone-giallo/main/brand/specgraph.svg" alt="SpecGraph" width="40" height="40"></a></td>
    <td><a href="https://github.com/Ev3nt1ne/airone-giallo/tree/main/graph"><b>SpecGraph</b></a><br>Airone Giallo's specification graph, usable alone; it keeps a project's decisions, requirements and assumptions as a graph and computes which of them still hold.</td>
  </tr>
  <tr>
    <td width="66"><a href="https://github.com/Ev3nt1ne/cache-watch"><img src="https://raw.githubusercontent.com/Ev3nt1ne/cache-watch/main/assets/cache-watch-mark.svg" alt="Cache Watch" width="40" height="40"></a></td>
    <td><a href="https://github.com/Ev3nt1ne/cache-watch"><b>Cache Watch</b></a><br>Warns you before your own Claude Code session's prompt cache goes cold. Embers is for subagents, Cache Watch for the session you sit in.</td>
  </tr>
  <tr>
    <td width="66"><a href="https://github.com/Ev3nt1ne/grammar-guard"><img src="https://raw.githubusercontent.com/Ev3nt1ne/grammar-guard/master/assets/grammar-guard-mark.svg" alt="Grammar Guard" width="40" height="40"></a></td>
    <td><a href="https://github.com/Ev3nt1ne/grammar-guard"><b>Grammar Guard</b></a><br>Grammar and spell check for the prompts you type or dictate into Claude Code.</td>
  </tr>
</table>

## License

MIT, see [LICENSE](LICENSE).

Embers is not affiliated with the Ember.js project or Tilde Inc., nor with Anthropic.
