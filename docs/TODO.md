# To do

Features to assess later. Nothing here is planned yet.

- **Ask returning work to be written to files.** The guide could tell the main session, when it
  hands off work that may come back, to ask the agent to write its findings to a file, so that
  a fresh agent can pick them up (the guide's line "Otherwise: start a fresh agent with a brief
  that points to the files of the earlier work" assumes such files). Not added for now
  (2026-11-19), for two reasons: it would create many new files, and writing them spends
  tokens. Assess whether that cost is lower than the cold resumes and redone work it would
  avoid.

- **Cold caches after pauses under 5 minutes.** In two weeks of one user's transcripts, 9 subagent
  requests found the cache cold even though the pause before them was under 5 minutes. A likely
  cause (inference, unverified): the lifetime counts from the start of the previous request, so a
  long request plus a short pause can pass 5 minutes. Checked against the request timings
  (2026-11-20, docs/math.md, third recount): confirmed. 7 of 24 cold rewrites came within 6 s
  of the previous response's end. That response had run 5 to 8 minutes and produced 28k to 63k
  output tokens. One more, after 1.2 minutes, probably came from a changed prefix (a model change,
  dropped thinking blocks). The 9 here used another criterion, so the counts do not match one to
  one. Still open: whether Embers should do anything about long responses. The relay's 1-hour
  cache covers them; the waiting rule and the guide do not. Nothing changed yet.
