# Known limits

Where Embers does not keep a cache warm, and what its hooks cannot see. The README lists the
limits a user is most likely to meet; this page has them all.

- **Usage credits.** While a subscription draws on usage credits, Claude Code ignores
  `cacheTtl: 1h` and drops even the main conversation to 5 minutes. The hooks cannot see this,
  so the relay goes uncovered by the waiting rule then, and the guide's 1-hour figures do not
  hold.
- **A wait over an hour still goes cold**, for the relay and for a main session alike.
- **A response longer than 5 minutes.** A subagent whose one response runs past its 5-minute
  lifetime, counted from the request's start, finds its cache gone on the next request. Neither
  the waiting rule nor the guide covers this: the agent never waits and never finishes. The
  relay's 1-hour cache does. In the [recount](measuring.md#the-measured-case), 7 of the 24
  rewrites were of this kind.
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

How the hook works out a subagent's lifetime, and which settings files it reads, is in
[design.md](design.md#the-waiting-rule).
