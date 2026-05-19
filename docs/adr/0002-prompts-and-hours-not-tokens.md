# Track prompts per 5h cycle and model-hours per week, not tokens

Most Claude Code usage trackers (ccusage, claude-monitor) display token counts because tokens are visible in every API response. We display **prompts** for the 5h cycle and **Sonnet-hours / Opus-hours** for the weekly window instead.

Reasoning:

- **Anthropic enforces on prompts and hours.** Pro/Max/Team plan limits are advertised as "X messages per 5 hours" and "Y Sonnet hours per week". A `%` displayed in token terms is decorative — it doesn't tell the user whether they're about to be cut off. A `%` in prompt/hour terms maps directly to the wall the user is going to hit.
- **Prompts are stable across model changes.** Token usage per prompt varies with context-window size, cache reads, and prompt-cache hit rate. Hour-of-conversation is a noisy proxy too, but it's the unit Anthropic publishes for weekly limits.
- **Borrowed model from claude-code-limit-tracker.** That project established this convention and the [[tier limit table]] (see `config/limits.json`). We extended it with `team_standard` (1.25× Pro) and `team_premium` (6.25× Pro, with Opus = Max 5x × 1.25).

## Consequences

The numbers don't reconcile with `ccusage` output. A user comparing this statusline against `ccusage blocks` will see different `%` values for the same session. README must call this out explicitly so users don't file false bug reports.

Anthropic does not publish exact numeric limits — `config/limits.json` ships `min`/`max` ranges and the statusline uses `min` as the denominator (conservative). When Anthropic publishes precise numbers (or community measures them), `limits.json` is the single edit point.
