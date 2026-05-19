# cc-statusline

A Claude Code plugin that ships a Python statusline renderer plus an installer skill. The renderer reads stdin JSON from Claude Code, parses the user's local conversation history, and prints a 3-line Powerline-themed status bar.

## Language

### Statusline rendering

**Segment**:
A single coloured chunk on one line, with a Nerd Font icon, content, and bg/fg from the Catppuccin Macchiato palette.
_Avoid_: chunk, block, panel.

**Chevron**:
The U+E0B0 glyph drawn between two **Segments**, painted `fg=prev.bg, bg=next.bg` to produce the Powerline transition.
_Avoid_: separator, arrow.

**Line**:
One of three statusline rows. Line 1 = vim/cwd/git/metrics. Line 2 = model/context. Line 3 = 5h block/weekly.

**Palette token**:
A named Catppuccin Macchiato colour (e.g. `base`, `crust`, `mauve`, `sapphire`). All segment fg defaults to `crust`; bg is per-segment semantic.

### Usage tracking

**Prompt**:
One external user message in a JSONL transcript that is neither a slash-command output (`<command-name>`) nor a meta message. The unit Anthropic rate-limits on per 5h window.
_Avoid_: message, query, request.

**5h cycle / 5h block**:
A rolling 5-hour window anchored to the earliest **Prompt** still inside it. Matches Anthropic's actual reset behaviour, not a fixed grid.
_Avoid_: session, block, period.

**Weekly window**:
The rolling 7-day window ending now. Sums **Model-hours** across all **JSONL transcripts** whose **Prompts** fall inside it.
_Avoid_: week, billing week.

**Model-hour**:
A unit of weekly usage. Computed per JSONL transcript as `(session_last_ts − session_first_ts) × (responses_for_model / total_responses)`. Summed per model (Sonnet / Opus) across the **Weekly window**.
_Avoid_: hour, conversation hour.

**JSONL transcript**:
A `~/.claude/projects/<project>/<session>.jsonl` file containing one Claude Code session's full message stream. The sole authoritative source for **Prompt** counts and **Model-hour** computation.
_Avoid_: log, history, conversation file.

**Tier**:
The user's Claude plan, selected via `CC_PLAN_TIER` env var. One of `free`, `pro`, `max_5x`, `max_20x`, `team_standard`, `team_premium`. Maps to a `limits.json` entry with `5h_cycle.{min,max}`, `weekly_sonnet.{min,max}`, and optionally `weekly_opus.{min,max}`.
_Avoid_: plan, subscription level.

**Limit basis**:
The `min` side of each tier range. The statusline uses `min` as denominator for `%` to stay conservative — the user is warned before Anthropic's actual cap kicks in.
_Avoid_: cap, quota.

### Plugin shape

**Plugin root**:
The directory referenced by `${CLAUDE_PLUGIN_ROOT}` at runtime. Contains `.claude-plugin/`, `statusline.py`, `config/limits.json`, `skills/`.

**Installer skill**:
`skills/install-statusline/SKILL.md`. Invoked as `/install-statusline`. Merges a `statusLine` block into `~/.claude/settings.json` pointing at `${CLAUDE_PLUGIN_ROOT}/statusline.py`.

## Relationships

- A **Tier** has 1..N usage axes (`5h_cycle` always; `weekly_sonnet` always; `weekly_opus` only for `max_5x`, `max_20x`, `team_premium`).
- A **JSONL transcript** contributes 0..N **Prompts** to the **5h cycle** and 0..N **Model-hours** to the **Weekly window**.
- A **Line** is composed of 1..N **Segments** joined by **Chevrons**.
- The **Installer skill** writes the `statusLine.command` field; the **Plugin root** owns the script that field points at.

## Example dialogue

> **Dev:** "When the user switches from Pro to Team Premium, what changes?"
> **Domain expert:** "The `CC_PLAN_TIER` env var goes from `pro` to `team_premium`. Line 3 now has a weekly Opus sub-segment, and the 5h denominator jumps from `min=10` to `min=63`. Same JSONL parsing, same statusline structure."
>
> **Dev:** "What counts toward the 5h cycle?"
> **Domain expert:** "External user **Prompts** in the last rolling 5 hours. Slash-command outputs and meta messages don't count. The window anchor is the earliest qualifying **Prompt**, not a clock grid."

## Flagged ambiguities

- "tokens" vs "prompts" — Resolved: 5h budget is **Prompts** (Anthropic's enforcement unit), not tokens. Tokens only appear in the Line 2 context-window segment.
- "block" — Used by ccusage docs to mean a 5h billing window; used by Powerline community to mean a rendered chunk. We use **5h cycle** for the former and **Segment** for the latter; "block" is reserved.
- "session" — JSONL files are one session each; a **5h cycle** may span many sessions. Don't conflate.
