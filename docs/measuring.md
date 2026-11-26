# Measuring cold rewrites, and recomputing the thresholds

The measured case behind Embers with its method, the tool that counts cold rewrites in your own
transcripts, and how to recompute the thresholds from the price file. [math.md](math.md) derives
the formulas.

## The measured case

A measured case, from one user's two weeks of ordinary work: 117 subagents rewrote 19 cold
caches of 150k to 620k tokens, about 7.1M tokens in all. At Opus 5.5 list prices that is about
$35.50, of which about $34 would have been avoided had each found its cache warm. A later
recount covered the same two weeks (2026-11-05 to 2026-11-19 13:00 UTC) with a different
criterion, so its counts differ from the study's: it counted as a cold rewrite any subagent
request that wrote at least 100k tokens to the cache while reading at least 100k fewer than the
request before it had cached, and asked what came before each one. It found 24 in 134
subagents, about 8.1M tokens written again (8.5M written in all, the new tails included):

- 13 (4.9M tokens) were work sent back, 6 to 117 minutes after the agent's previous request began (a little
  less from its hand-back);
- 4 (1.4M) came when an agent that had ended its turn was woken by a system notification, 1 to
  11 minutes later;
- 7 (1.9M) followed a single response of 28k to 63k output tokens that ran 5 to 8 minutes.

None followed a wait on a tool result. Public reports show that pattern, a subagent blocked on a
long tool, at larger scale: in one, 190 rewrites of over 100k tokens each followed test suites that ran 10 to 15 minutes and
drained 54% of a weekly limit in under a day
([#103309](https://github.com/anthropics/claude-code/issues/103309)).

The pricing and the three recounts in full are in
[math.md, the author's measured case](math.md#the-authors-measured-case).

### The first week with Embers

The same user installed Embers on 2026-11-19 at about 12:30 UTC. The tool compares the week
that followed with the two weeks of the recount, on the recount's criterion:

```
python3 tools/rewrites.py --since 2026-11-05 --split 2026-11-19T13:00Z --until 2026-11-26T13:00Z
```

| | Before: 2026-11-05 to 11-19 13:00 | After: 11-19 13:00 to 11-26 13:00 |
|---|---|---|
| subagents seen | 134 | 146 |
| requests | 7,633 | 6,410 |
| cold rewrites | 24 | 7 |
| on a resume | 13 | 2 |
| on a system notification | 4 | 0 |
| after a long request | 7 | 0 |
| after a tool wait | 0 | 5 |
| tokens rewritten | 8,095,708 | 966,717 |
| rewrites at list price | $53.99 | $4.12 |
| cache writes, all requests | 34,134,199 | 25,233,260 |
| of which 1-hour | 38,021 | 7,532,275 |
| prompt cost, all requests | $494.66 | $310.91 |
| rewrites, share of writes | 23.7% | 3.8% |
| rewrites, share of prompt cost | 10.9% | 1.3% |
| relay agents | 1 | 24 |
| relay requests, all writing to the 1-hour cache | 3 | 2,203 |

The before window is two weeks and the after window one, so compare the shares, not the totals.
The one relay before the split is the install day's test.

The seven rewrites after:

- **2 were work sent back, both small**: a 5-minute agent of 106k tokens resumed 6.4 minutes
  after its previous request began, and a relay of 161k resumed after 94.7 minutes, past its
  hour ($1.82 together). Before, a rewrite averaged 337k tokens.
- **3 were waits chained in one response** (480k tokens, $1.20). One agent followed the waiting
  rule's form, with each Bash call under 170 seconds, but made two or three of them in one
  response. Claude Code runs them one after another, so the next request came 5.2 to 7.9
  minutes after the previous one began. The rule now asks for one wait per response.
- **2 were a permission prompt** (220k tokens, $1.10). One agent's command waited about 13
  minutes on a prompt, twice, before anyone answered it: see
  [limits.md](limits.md). The tool counts these as tool waits, since a tool result came last;
  the result line's `permissionDecision` shows that a person decided.

None came on a system notification or after a long response, the two kinds the relay and the
waiting rule were built for besides work sent back.

**The relay.** 24 relays ran 2,203 requests, every one writing to the 1-hour cache: 7,532,275
tokens, $22.60 more than the same writes at 5 minutes. 25 of their requests, in 14 relays, came
5 to 43 minutes after the previous request began, and every one read the whole prefix the
previous request had cached. On a 5-minute cache each would have written that prefix again:
$32.81. The relay saved $10.21 net in the week. That counts each late return as a cold resume
of the same agent, which is what the main session did without Embers (13 times in the two weeks
before). A main session that started fresh agents instead would have saved less on those
returns, and the relay's margin would shrink (inference).

**What Embers saved**, by date:

```
python3 tools/rewrites.py --since 2026-11-05 --until 2026-11-26T13:00Z --savings --embers-on 2026-11-19T13:00Z..
```

| Subagent prompt cost | Embers off: the two weeks before | Embers on: the week after |
|---|---|---|
| subagents | 134 | 146 |
| requests | 7,633 | 6,410 |
| without Embers | $494.66 measured | $321.12 computed |
| with Embers | $460.71 computed | $310.91 measured |
| Embers saved | $33.95, 6.9% | $10.21, 3.2% |

How to read it:

- **Each column on its own.** The off column is the two weeks before, re-priced as if Embers had
  been installed: the 7 subagents that were sent work back become relays, which costs $45.46
  more in 1-hour writes, and 22 of their rewrites, all under an hour, read their cache instead
  ($79.41 less). The 4 notification rewrites were all in those 7 agents, so no polls are
  computed. The install day's relay test, already on a 1-hour cache, is left as it ran. The on
  column is the week after, re-priced as if Embers had not been installed: the 24 relays lose
  their premium ($22.60 less) and the 25 late requests above rewrite their cache ($32.81 more).
  The 113 subagents with the waiting rule, and the 9 counted on by date that ran without it, are
  left as they ran.
- **The on column's 3.2% is neither a floor nor a ceiling.** The tool cannot see the rewrites
  the waiting rule prevented, nor the cold resumes the guide replaced with fresh agents: both
  would make the saving larger. Nor can it see the polls the rule kept up past the break-even
  of [math.md](math.md#3-waiting-on-a-tool), where letting the cache go cold costs less: those
  would make it smaller.
- **The off column's 6.9% assumes the relay is picked exactly where work comes back.** In the
  week after, 8 of the 24 relays never had a gap over 240 seconds: one-shot work, which pays the
  1-hour write for nothing.
- **The ceiling was low.** Before Embers, all the rewrites together were 10.9% of subagent prompt
  cost, and had every one found its cache warm at no premium, $52.37 (10.6%) would have been
  saved: the tool's *avoidable had each been warm* row.

Sorted by transcript instead of by date (no `--embers-on`), each column moves by about 1%, and
the savings stay at $33.95 and $10.24: 3 subagents that ran before the split, tests on the
install day, held the rule, and 9 after it ran without the hook. Five of those were in a session started
the day before the install; four were `web-fetch` agents, which get no hook (see
[limits.md](limits.md)).

## Measure your own

[tools/rewrites.py](../tools/rewrites.py) counts the cold rewrites in your own subagent transcripts
and prices them, so you can see what the problem costs you before installing Embers, and what
changed after. It only reads `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR/projects`, or
`--root`), and prints a table:

```
python3 tools/rewrites.py --since 2026-11-01                       # one window
python3 tools/rewrites.py --since 2026-11-01 --split 2026-11-15    # before and after, side by side
python3 tools/rewrites.py --since 2026-11-01 --list                # and every rewrite, one per line
python3 tools/rewrites.py --since 2026-11-01 --detail              # and what came before each one
```

`--detail` prints, under each rewrite, the tool calls of the agent's previous request and every
line of the transcript in the gap: tool results, messages, attachments, with their times. It
prints tool names, line kinds and leading tags only, never a command, a prompt or a result. A
message that starts with a bracket shows as `[...]`, unless the bracket is Claude Code's own
wording, such as `[SYSTEM NOTIFICATION - NOT USER INPUT]`.

Dates are UTC days; `--split` and `--until` name the first day not counted in the window before
them. `--json` prints everything as JSON.

**What it counts.** A *cold rewrite* is a subagent request, not the agent's first, that writes
at least 100,000 tokens to the cache and reads at least 100,000 fewer than the previous request
had cached: the cache it should have found was gone. Change the threshold with `--min-write`. A
large write that read the whole previous prefix, such as a big file coming back from a tool, is
not a rewrite; the table counts those apart, as *large writes, not cold*. For each window:

- subagents seen and their requests, by the time each request was sent;
- cold rewrites, the tokens they wrote again, and what writing those cost at list prices from
  [config/prices.toml](../config/prices.toml), each request priced at its own model. The tokens
  written again are the part of the write that the previous request had cached; the new tail,
  which a warm request writes too, is left out;
- what those tokens would have cost had each rewrite found its cache warm and read them, and
  the difference;
- the gap before each rewrite, from the previous request's start (the cache's clock), and its
  cause. A *long request* when the gap reached the agent's cache lifetime (5 minutes, or 1 hour
  once the agent has written to the 1-hour cache) and the previous request ran longer than the
  agent then sat idle: the request outlived its own cache, which counts from its start. A
  rewrite after a shorter gap is never a long request. Otherwise, by what the agent
  was waiting for, the last tool result or message to arrive: a *tool wait* for a tool result,
  a *notification* for a system notification, a *resume* for any other message, which is work
  sent back by the manager. An agent that hands back to its manager with a tool call and is
  sent more work later counts as a resume, because the message came last;
- the cache cost of every subagent request in the window, and the rewrites' share of it;
- how often `embers:relay` ran, and whether its requests wrote to the 1-hour cache.

**Its limits.**

- A *notification* is recognised by the tag Claude Code puts at the start of its system
  notifications. What the notification was about, a background job or something else, is not
  read. A message without such a tag counts as a resume.
- The cause says what the agent was waiting for, not why the cache was gone. A rewrite after a
  short gap may come from a changed prefix (a model switch, thinking blocks dropped) rather than
  from an expired cache; `--detail` shows such lines in the gap.
- An agent's type comes from the `.meta.json` beside its transcript. Without one, the agent is
  counted but cannot be recognised as the relay.
- It reads only `<session>/subagents/agent-*.jsonl`. Subagents recorded anywhere else, for
  example by a Claude Code version that kept them elsewhere, are not counted. A transcript that
  cannot be read is not counted either; the table says how many there were.
- On a subscription, list prices stand for weight against your usage limit, not for money you
  were charged.
- A before and after compares two different stretches of work. Fewer rewrites after may also
  mean fewer subagents, or shorter waits; read the requests and the gaps beside the count.

### Did Embers save you money?

`--savings` prints a different table: what your subagents' prompts cost without Embers and with
it, each group measured the way it ran and computed the other way.
[math.md](math.md#4-what-embers-saved) derives the computed cells, and
[the first week with Embers](#the-first-week-with-embers) reads one such table.

```
python3 tools/rewrites.py --since 2026-11-01 --savings                           # by transcript
python3 tools/rewrites.py --since 2026-11-01 --savings --embers-on 2026-11-15..  # by date
```

**Which subagents had Embers.** A subagent counts as *Embers on* when its transcript holds the
waiting rule that Embers' `SubagentStart` hook injects, or its type is the relay
(`--relay-type`); any other subagent counts as *Embers off*. A 1-hour agent of your own gets no
rule, so it counts as off, and so does a subagent in which the hook did not run, whatever the
date. When your transcripts predate the hook's current wording, or you switched Embers on and off,
name the dates instead with `--embers-on FROM..TO` (UTC, repeatable, either end may be left out).
Each request then counts by when it started, whatever its transcript holds, and an agent that
spans a boundary counts on both sides. `--since` and `--until` set the window as usual;
`--split`, `--list` and `--detail` do not go with `--savings`.

**The table.** One column per group, one row per way of running:

| Subagent prompt cost | Embers off | Embers on |
|---|---|---|
| without Embers | measured | computed |
| with Embers | computed | measured |
| Embers saved | $ and % | $ and % |

A measured cell is the prompt side of every request in the window as it ran: uncached input,
cache reads and cache writes, at list prices from [config/prices.toml](../config/prices.toml),
each request at its own model. A computed cell prices the same requests the other way:

- *Off, computed with Embers.* A subagent with a cold rewrite on a resume would have been the
  relay. All its writes are priced at the 1-hour write, and each of its rewrites less than an
  hour after the previous request's start reads the tokens it wrote again; its new tail is still
  written. Its rewrites past the hour stay. In any other subagent, a rewrite on a system
  notification is replaced by the waiting rule's polls: one per `chunk_s` of the gap (170 s),
  less the request that ends the wait, at least one. Each poll reads the context and writes the
  `[poll]` tail; its output is counted too. Every other request stays as it ran, a rewrite after
  a long request included. A subagent that already writes to the 1-hour cache, such as an agent
  of your own with `cacheTtl: 1h`, stays as it ran: Embers gives it nothing.
- *On, computed without Embers.* The relay's 1-hour writes are priced at the 5-minute write.
  Each relay request that started between 5 minutes and an hour after the previous request's
  start, and read its cache, would have found a 5-minute cache gone: what it read is priced as a
  5-minute write. Every other subagent is left as it ran: those with the waiting rule, and
  with `--embers-on` those counted on by date without it.

Under the table, a few lines give the parts of each computed cell: the subagents that would
have been the relay, with what their 1-hour writes add and their warm resumes save; the
notifications polled instead; any subagents already on a 1-hour cache; the relays, with what
5-minute writes save and what their late requests would have cost cold; the subagents with the
rule and, by date, those without it. `--json` carries the same figures and parts.

**How to read it.**

- Read each column's *Embers saved* row on its own. The two columns are different subagents
  doing different work, so their costs are not comparable with each other.
- The on column's saving is no bound either way: two effects the transcripts cannot show pull
  opposite ways. The rewrites the waiting rule prevented in 5-minute subagents (a subagent that
  kept its cache warm shows no rewrite to price) and the cold resumes the guide replaced with
  fresh agents make the real saving larger. The polls the rule kept up past the break-even of
  [math.md](math.md#3-waiting-on-a-tool), where letting the cache go cold costs less, make it
  smaller. The tool prices those polls the same in both cells, since agents polled long jobs
  before Embers too.
- The off column's computed cell assumes the relay is picked exactly where work comes back, and
  nowhere else. A relay picked for one-shot work pays the 1-hour write for nothing, so the real
  saving there may be smaller.
- A negative saving means Embers cost that group more than it saved.
- Prompt side only, at list prices; the polls' output is the one output cost counted. On a
  subscription, the dollars stand for weight against your usage limit.

## Recompute the thresholds

1. Change a number in [config/prices.toml](../config/prices.toml); each entry says where it comes
   from, or that it is an assumption.
2. Run `python3 tools/thresholds.py` (add `--json` for JSON, `--model sonnet-5-5` for another
   model, `--set section.key=value` for any number).
3. Run the tests: `python3 -m unittest discover -s tests -v`.

The injected texts read the same file at every session start, so nothing else needs changing.
