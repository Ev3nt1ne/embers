---
title: The cost math behind Embers' rules
created: 2026-11-19
updated: 2026-11-26
---

# The cost math behind Embers' rules

Embers keeps subagent prompt caches from going cold. Its rules rest on three comparisons: send
work back to an agent or start a fresh one; give an agent a 5-minute or a 1-hour cache; refresh
the cache while waiting on a tool or let it go cold. This page derives each one. The numbers live
in [config/prices.toml](../config/prices.toml); [tools/thresholds.py](../tools/thresholds.py)
recomputes every value below from that file.

Marks used: **fact** is read in the docs (checked 2026-11-19) or measured in the author's
transcripts; **inference** is derived here, and its assumptions are named.

## Prices as multiples

Every cost is counted in *base-input tokens*: one token at the model's base input price. The
price file holds the multiples (fact,
[prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[pricing](https://platform.claude.com/docs/en/about-claude/pricing)):

| Symbol | What | Value |
|---|---|---|
| w5 | write to a 5-minute cache | 1.25 |
| w1 | write to a 1-hour cache | 2 |
| r | read from the cache | 0.05 on Opus 5.5 and Sonnet 5.5, 0.025 on Fable and Mythos 5.1/5.2, 0.1 on the rest |
| o | output token | 5 (every model in the pricing table) |

Opus 5.5 costs $4 per million base-input tokens. The whole 1M window is billed at the same
per-token rate (fact). A cache lives 5 minutes (or an hour) from the *start* of the request that
last read or wrote it, and a hit resets the clock at no extra cost (fact).

The other symbols: C, the agent's context in tokens; N, the context window (1M on every current
model, fact); S, a fresh agent's baseline (system prompt, tools, CLAUDE.md, the brief); F, the
files a fresh agent's brief points to.

**S is measured (fact):** the median first request of the author's 125 subagents
(2026-11-05..19) was 36k tokens, with the middle half between 33k and 37k. **F is an assumption:**
30k.

**When a cache counts as warm.** The manager sees when the agent's last request *ended*, but the
clock started when it *began*. The price file keeps a 60 s safety margin for that request's
generation time (assumption), so a 5-minute agent is warm for **240 s** after its last activity
and a 1-hour agent for **3,540 s**.

## 1. Send back or start fresh

The manager has finished with a subagent and later has more work for it.

**The costs.** The follow-up work takes k turns (assumption: 10). Every turn re-reads the context
the agent holds, so the size of the context costs on every turn, not only the first.

- Resume warm: the first request reads C, and each later turn reads C again: `(1 + k) r C`.
- Resume cold: the first request rewrites C, and the later turns read it: `(w + k r) C`, where w
  is the agent's write price (w5, or w1 for a 1-hour agent).
- Start fresh: write S + F, read it k times, and rebuild the D tokens of working context that the
  old agent held and nobody wrote down. Rebuilding a token costs its write w, the output that
  produced it (h output tokens per context token, assumption: 0.1, so `o h` = 0.5), and its reads
  on the later turns: `(w + k r)(S + F) + D (w + o h + k r)`.

The output, the new tail and the growth of the context during the follow-up are the same on every
path and cancel.

**Cold resume against fresh.** Setting the two equal:

```
C* = S + F + g D,    where  g = 1 + o h / (w + k r)
```

So the threshold has two parts. **S + F is a floor**: below it a cold resume is always cheaper than
a fresh agent, because a fresh agent costs at least that much. **g D is the lost work**, priced a
little above a plain write (g = 1.29 on Opus 5.5) because rebuilding it also costs output.

**The rule is stated in tokens** (the author's decision, 2026-11-19): resume cold below
X = 150,000 tokens, or below Y = 350,000 when the agent holds work that exists nowhere else.
Two points explain why tokens and not a share of the window (inference):

- **The prices alone do not fix X.** Under linear prices the choice turns on D, how much work a
  fresh agent would have to redo, not on the size of C. A threshold on C is a proxy for D.
- **S + F + g D does not depend on the window N.** A threshold written as a percentage of the
  window would move whenever the window changes; one written in tokens does not.

**What 150k and 350k imply at today's prices** (Opus 5.5, S = 36k, F = 30k, k = 10):

| | Threshold | Lost work it implies (D) |
|---|---|---|
| floor S + F | 66k tokens | 0 |
| X (work exists elsewhere) | 150k tokens | **65k** tokens to rebuild |
| Y (work exists nowhere else) | 350k tokens | **221k** tokens to rebuild |

Read it this way. X = 150k is right when a fresh agent, brief and files in hand, would still
spend about 65k tokens of context getting back to where the old agent was. Y = 350k is right when
it would spend about 220k. For a 1-hour agent that went cold (w = w1), D at X is 70k; the read
multiplier moves D by under 5% across models.

The same formula, run the other way, gives X as a function of D:

| D (lost work) | 0 | 25k | 50k | 100k | 200k | 300k |
|---|---|---|---|---|---|---|
| X in tokens | 66k | 98k | 130k | 195k | 323k | 452k |

**Verdict on 150k/350k (inference).** They are reasonable, and not badly off.

- **X = 150k** leans towards resuming. For an agent whose results already sit in files, the redo
  is probably 25–50k tokens (assumption), which gives X ≈ 100–130k. The cost of the lean is
  small. At C = 150k with D = 25k, a cold resume costs 91k base-input tokens more than a fresh
  agent: about $0.36 on Opus 5.5.
- **Y = 350k** fits an agent whose 220k tokens of investigation would really have to be redone,
  which is about the size of the author's larger agents. The median subagent peaked at 164k and
  the top quarter above 252k (fact, the same 125 transcripts).

**The window view, as a derived note only.** On a 1M window, X and Y are 15% and 35%. On a 200k
window, which only older models have (Opus 4.5, Sonnet 4.5, Haiku 4.5), Y is past the whole window
and X is 75% of it: the token rule still holds there, while the same rule written as 15% would
have been 30k tokens, below the 66k floor, and would have started fresh even when a fresh agent
costs more by itself. `tools/thresholds.py` prints the shares as `X_share`, `Y_share` and
`fresh_floor_share`.

**How the manager knows C (fact, measured).** A background subagent's completion notice carries
`<subagent_tokens>`. In the author's sessions (184 notices, 2026-11-19), every notice that
was an agent's last one (144) matched that agent's last request (input, cache read, cache write
and that request's output) to within 1%. The other 40 came from earlier runs of agents that were
resumed later, and were smaller, as expected. The agent's running total was at least 5.9 times
the figure. So the figure is the context size at the end of that run, plus one turn's output. The docs say the same of `totalTokens` in the Agent tool's
result: "Token count from the subagent's final API request … This isn't a total across the
whole run" ([hooks](https://code.claude.com/docs/en/hooks.md#posttooluse)).

**A caveat on "resume when warm" (inference).** A warm resume is cheap on its first request, but
the agent then carries C on every turn. Warm costs more than fresh when

```
C > [(w + k r)(S + F) + D (w + o h + k r)] / ((1 + k) r)
```

That is 210k with no lost work, and 477k with D = 65k, at k = 10 on Opus 5.5. It matters only
for very large agents given long follow-ups. The author's rule stays as decided.

**What would change these numbers:**
- S, if Claude Code's system prompt or tool set grows or shrinks.
- F, and the redo D, which depend on the work.
- k, when follow-ups are long or short.
- The output ratio o.
- Not the window N: the thresholds are in tokens, so N changes only the derived shares (X/N,
  Y/N, (S + F)/N), never a threshold, D or the ceiling.

## 2. A 5-minute or a 1-hour agent

An agent is resumed m times; at resume i it holds C_i. Its cache writes over its life (without
rewrites) add up to its final context C_end, since every token is written once when it arrives.

For each resume, write p for the chance that the gap falls between 5 minutes and an hour, and z
for the chance it runs past an hour.

**Cost of each path:**
- A **1-hour agent** pays `w1 − w5` more on every write: `(w1 − w5) C_end` over its life.
- In a **5 to 60 minute gap** it reads (r) where a 5-minute agent rewrites (w5): it saves
  `(w5 − r) C_i`.
- **Past an hour** both go cold, and the 1-hour agent's rewrite costs more: it loses
  `(w1 − w5) C_i`.

With L = ΣC_i / C_end, the resume load, break-even is at

```
p* = (w1 − w5)(1/L + z) / (w5 − r)
```

**At today's prices**, with one resume near the end (L = 1, z = 0), this is the
`(w1 − w5)/(w5 − r)` of the brief:
- Opus 5.5 and Sonnet 5.5: **p\* = 62.5%**
- Fable and Mythos: 61.2%
- models at r = 0.1: 65.2%

A 1-hour cache pays only for an agent that is usually sent back late. Each further resume lowers
the bar:
- L = 2: p* = 31%
- L = 4: p* = 16%

A z of 10% adds 6 points at L = 1.

**Cross-check (inference against a single-source measurement).** As a share of a 5-minute
agent's writes, the break-even fraction of rewrites is

```
f* = (w1 − w5) / ((w1 − w5) + (w5 − r))
```

This gives 39.5% at r = 0.1 and 38.5% at r = 0.05. It matches the "about 40%" one user measured
([anthropics/claude-code#110403](https://github.com/anthropics/claude-code/issues/110403)).

**What would change it:** the write prices; the agent's resume pattern (L, z); r, which matters
little.

## 3. Waiting on a tool

An agent waits on a long job in chunks of T = 170 s (the author's choice; it stays under the 3-minute
move to the background in background subagents, fact, CHANGELOG 2.1.318).

**Headroom.** 300 − 60 − 170 = 70 s are left for the poll turn's own generation.

**Cost of a poll.** Each poll reads C, writes a small tail t, and generates y output tokens. The
assumptions, for a terse poll, are t = 300 and y = 150:

```
cost of one poll = r C + w5 t + o y
```

Letting the cache go cold instead costs one rewrite in place of one read: `(w5 − r) C`.

**Break-even:**

```
n* = (w5 − r) C / (r C + w5 t + o y)        wait* = n* × T
```

Ignoring the tail, `n* ≈ (w5 − r)/r`, and the brief's `w5/r × T` is the rounded form. At today's
prices, with C = 300k:

| Model | w5/r × T | Refined, C = 300k |
|---|---|---|
| Opus 5.5, Sonnet 5.5 | 25 polls, 71 min | 22.3 polls, **63 min** |
| r = 0.1 models | 12.5 polls, 35 min | 11.1 polls, 31 min |
| Fable, Mythos | 50 polls, 142 min | 42.6 polls, 121 min |

For small contexts the fixed tail and output weigh more. At C = 50k on Opus 5.5, n* is 16.6
polls, or 47 minutes.

**The author's ruling and its cost.** The author has ruled that an agent waiting on a tool always
refreshes, with no cutoff, for simplicity. Below wait* that is also the cheaper choice. Past it,
each further hour of refreshing costs `(3600/T)(r C + w5 t + o y)`. For a 300k context on
Opus 5.5 that is **0.95 of one rewrite, about $1.37 an hour**.

A 2-hour wait at 300k therefore costs about $2.73 in polls. Letting the cache go cold would cost
$1.44, so the ruling pays about $1.30 for the simplicity. [design.md](design.md#design-choices-and-why) records it.

**What would change it:** r above all (it sets n*), the chunk length, and how terse the poll
turns are.

## 4. What Embers saved

`tools/rewrites.py --savings` (code: `with_embers()` and `without_embers()`) prices one group of
subagents as it ran and computes what the same requests would have cost the other way. This
section derives the computed cells; [measuring.md](measuring.md#did-embers-save-you-money) says how
to run it.

**Notation.** For a request: u its uncached input, R what it read from the cache, W5 and W1 what
it wrote to the 5-minute and the 1-hour cache, g its gap from the previous request's start. For a
cold rewrite: X, the tokens written again, the part of its write that the previous request had
cached. Its new tail, which a warm request writes too, is not in X. Costs are in base-input
tokens of the request's own model, as above; the tool multiplies each request by its model's
base price.

**Measured cell.** The prompt side of every request in the group, as it ran:

```
M = Σ (u + r R + w5 W5 + w1 W1)
```

**Without Embers, computed with it (inference; the off column).** Each subagent changes in one of
two ways, or not at all.

- *A subagent with a cold rewrite on a resume would have been the relay.* Every write moves to
  the 1-hour price, and each rewrite under the hour reads its X instead of writing it. With W the
  agent's 5-minute writes and X< the tokens written again by its rewrites under an hour:

  ```
  Δ = (w1 − w5) W − (w1 − r) X<  =  (w1 − w5)(W − X<) − (w5 − r) X<
  ```

  The second form is section 2's comparison on the agent's real gaps: the premium on its
  ordinary writes against the rewrites it avoids. A rewrite past the hour stays, at w1, and pays
  the premium too.
- *In any other subagent, a rewrite on a system notification is replaced by the waiting rule's
  polls.* A wait of g seconds, polled every T = 170 s, takes ⌈g/T⌉ chunks; the request that ends
  the last one is the request that was the rewrite, now reading its cache, so it is no extra
  poll. Each poll costs section 3's `r C + w5 t + o y`, with C taken as X:

  ```
  n = max(1, ⌈g/T⌉ − 1)        Δ = n (r X + w5 t + o y) − (w5 − r) X
  ```

- *Everything else stays as it ran.* A rewrite after a long request cannot be avoided on a
  5-minute cache. A rewrite after a tool wait might have been avoided by the waiting rule, so
  leaving it in understates the saving.

The assumption that carries this column: the relay is picked exactly for the subagents that were
sent work back, and for no other. A relay picked for one-shot work pays `(w1 − w5) W` for
nothing.

**With Embers, computed without it (inference; the on column).** Only relays change. Their 1-hour
writes move to the 5-minute price, and each request that came between the two lifetimes after
the previous request's start, was not itself a rewrite, and read its cache, would have found a
5-minute cache gone. What it read is what it would have written again:

```
Δ = (w5 − r) Σ R  −  (w1 − w5) W1        the sum over relay requests with 300 s < g < 3600 s
```

Subagents with the waiting rule stay as they ran: a rewrite the rule prevented leaves nothing in
the transcript to price. Nor can the transcripts show a cold resume the guide replaced with a
fresh agent. Both make this column's saving a lower bound. The other way round, the column
counts each late return to a relay as a cold resume of the same agent; a main session that would
have started a fresh agent instead (section 1) would have paid less, and the relay saved less.

**Saved.** Per column, without minus with: `−Δ` in the off column, `Δ` in the on column; the
share is the saving over the cost without Embers. The two columns are different subagents doing
different work, so each is read on its own.

**The ceiling.** No way of running can save more than every rewrite finding its cache warm at no
premium: `Σ (w5 − r) X` over the rewrites, the tool's *avoidable had each been warm*. In the
author's two weeks before Embers that was $52.37 of $494.66 of subagent prompt cost, 10.6%
(fact, the tool's output). The off column's computed saving, $33.95, is about two thirds of it
(inference, from the figures in [measuring.md](measuring.md#the-first-week-with-embers)).

**What would change it:** the write prices, which set the relay's premium against what it saves;
how well the main session picks the relay; and, for the polls, the same r, T, t and y as
section 3.

## The author's measured case

**The data (fact; the author's own study of their transcripts, not published).** Over two weeks,
117 subagents rewrote 19 cold caches of 150k–620k tokens, about 7.1M tokens in all. A third
recount, below, asked what came before each rewrite: not all of them were work sent back.

**Priced on Opus 5.5** (inference: most of the author's subagent writes are Opus 5.5, 24.0M of
33.4M, fact):

| | Formula | Base-input tokens | USD |
|---|---|---|---|
| As it happened, cold at 5 minutes | 7.1M × w5 | 8.9M | **$35.50** |
| Had each found its cache warm | 7.1M × r | 0.36M | **$1.42** |
| Avoidable | 7.1M × (w5 − r) | 8.5M | $34.08 |

**On a subscription** (inference): the dollars stand for list-price weight against the weekly
limit, which the study found drains with cache writes.

**Would 1-hour agents have saved it?** Only if the same agents' ordinary writes had stayed under

```
7.1M × (w5 − r)/(w1 − w5) = 11.4M tokens
```

and only for the gaps under an hour. A second recount of the author's transcripts used a different
window and criterion (2026-11-05..19, 125 subagents, a rewrite being any later request writing at
least 100k). It found:
- 25 rewrites of 8.6M tokens, within 33.4M cache writes in all;
- so rewrites were 26% of writes, below the 38.5% break-even.

The conclusion (inference) agrees with the study: a blanket 1 hour would have cost more, and a
1-hour agent pays only where an agent is sent back again and again. One agent in that recount was
rewritten ten times in one afternoon.

**A third recount: what came before each rewrite (fact, the author's transcripts, 2026-11-20).**
Window 2026-11-05 to 2026-11-19 13:00 UTC, 134 subagents. A cold rewrite is a request, not the
agent's first, that writes at least 100k tokens to the cache **and** reads at least 100k fewer
than the previous request had cached (its read plus its write). The second half of the test
leaves out a large tool result read into a warm cache; this window had one such write. The gap
runs from the previous request's start, which is when the cache's clock starts. It found 24
rewrites, 8.1M tokens written again (8,095,708, as
[tools/rewrites.py](../tools/rewrites.py) counts them, recounted 2026-11-26). The requests wrote
8.5M in all; the difference is the new tail each one added, which a warm request writes too:

| What came before | Rewrites | Tokens written again | Gap from the previous request's start |
|---|---|---|---|
| Work sent back: the agent handed back, then its manager sent it more work | 13 | 4.9M | 6 to 117 min |
| A system notification woke an agent that had ended its turn | 4 | 1.4M | 1 to 11 min |
| One response ran 5 to 8 minutes, and the next request started at once | 7 | 1.9M | 5 to 8 min |
| A tool result that came back after a wait | 0 | | |

- **Work sent back.** The manager's message arrived within 20 s before each rewrite. The agent's
  hand-back is itself a tool call whose result returns at once, so what the agent waited on was
  the message, not the tool.
- **The notification** is identified by its tag; its text was not read. That it was a background
  job's notice is an inference. The shortest gap, 1.2 minutes, shows a model change and dropped
  thinking blocks in the transcript. It is more likely a changed prefix than an expired cache
  (inference; see [limits.md](limits.md)).
- **The long responses** produced 28k to 63k output tokens each, at the same pace as the agents'
  short requests, so they were generation, not stalls or retries (inference from that pace). The
  lifetime counts from a request's start, so such a response leaves its own cache expired. The
  agent then went on within 6 s.

What this changes (inference). Work sent back is the larger part, 60% of the rewritten tokens,
but not the whole. A subagent blocked on a long tool, the case public reports show at scale, had
no instance here. The long-response case escapes the waiting rule and the send-back guide, since
the agent never waits and never finishes; a 1-hour cache covers it. The 60 s margin in the warm
window above covers the requests before a hand-back (they ran under a minute here), not these.

## How to recompute

1. Edit a number in `config/prices.toml`; every entry names its source or says it is an
   assumption.
2. Run `python3 tools/thresholds.py`, or add `--json` for JSON.
3. To ask "what if" without editing the file:
   - another model: `--model sonnet-5-5`
   - another read price: `--read 0.1`
   - any other number: `--set section.key=value`, for example
     `--set context.window_tokens=200000` or `--set agent.brief_files_tokens=60000`
4. Run `python3 -m unittest discover -s tests -v`; the hand-computed cases there are the ones
   on this page.
