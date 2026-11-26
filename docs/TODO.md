# To do

Features to assess later. Nothing here is planned yet.

- **Ask returning work to be written to files.** The guide could tell the main session, when it
  hands off work that may come back, to ask the agent to write its findings to a file, so that
  a fresh agent can pick them up (the guide's line "Otherwise: start a fresh agent with a brief
  that points to the files of the earlier work" assumes such files). Not added for now
  (2026-11-19), for two reasons: it would create many new files, and writing them spends
  tokens. Assess whether that cost is lower than the cold resumes and redone work it would
  avoid. Still open (2026-11-26): a planned return now goes to the relay, so a 5-minute agent
  sent work back is an unplanned return, which the main session cannot foresee to ask a file
  for. The agent's final report already lands in the main session, and copying it into a fresh
  agent's brief costs the main session output tokens. To be evaluated after two more weeks of
  measurement with the new relay wording.

- **Cold caches after pauses under 5 minutes.** In two weeks of one user's transcripts, 9 subagent
  requests found the cache cold even though the pause before them was under 5 minutes. A likely
  cause (inference, unverified): the lifetime counts from the start of the previous request, so a
  long request plus a short pause can pass 5 minutes. Checked against the request timings
  (2026-11-20, docs/math.md, third recount): confirmed. 7 of 24 cold rewrites came within 6 s
  of the previous response's end. That response had run 5 to 8 minutes and produced 28k to 63k
  output tokens. One more, after 1.2 minutes, probably came from a changed prefix (a model change,
  dropped thinking blocks). The 9 here used another criterion, so the counts do not match one to
  one. Then open: whether Embers should do anything about long responses. The relay's 1-hour
  cache covers them; the waiting rule and the guide do not. Investigated (2026-11-26): of the 7,
  3 were one long file written in a single Write call (about 1,600 to 1,700 lines) and 4 were
  long thinking that ended in a small tool call. A rule capping the lines per tool call reaches
  only the Writes; no wording reaches a response while it thinks. Priced on three weeks of the
  same user's transcripts (inference): at 300 lines per call the extra requests would cost more
  than the rewrites they avoid, and at 500 lines the rule saves a little, with a thin margin for
  a response that thinks for minutes before it writes. Decision (2026-11-26): no rule for now.
  Kept for later, should the pattern grow: a 500-line wording, and a trial on one agent to see
  whether it follows the rule and how it appends.
