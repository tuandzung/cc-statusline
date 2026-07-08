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

Three data sources, ordered by priority:

1. **codexbar source** (preferred) — third-party `codexbar serve` local daemon, queried for the same Anthropic quota data plus **Pace**.
2. **Quota snapshot** (fallback) — Anthropic's authoritative `utilization` returned by `GET /api/oauth/usage`. Requires **OAuth credentials**.
3. **JSONL-derived stats** (last resort) — local heuristic over **JSONL transcripts**. Used when OAuth credentials absent or `/api/oauth/usage` unreachable.

**codexbar source**:
The `codexbar serve` daemon (third-party, [steipete/CodexBar](https://github.com/steipete/CodexBar)), queried via `GET http://127.0.0.1:8080/usage?provider=claude`. cc-statusline is a pure client — it never spawns, supervises, or restarts the daemon; if nothing answers, that render falls back to **Quota snapshot**. Only source that carries **Pace**.
_Avoid_: codexbar API, the daemon.

**Pace**:
A weekly-only trend indicator, present only when **codexbar source** served the render. Rendered as a single icon appended to the weekly segment, driven by **willLastToReset**. Has no equivalent on the **Quota snapshot** or **JSONL-derived stats** paths — Line 3's shape is not stable across sources for this indicator.
_Avoid_: pace stage, trend, projection.

**willLastToReset**:
The boolean codexbar returns alongside **Pace** indicating whether current usage will exhaust the weekly window before it resets. Drives the **Pace** icon directly. Deliberately used in place of codexbar's `stage` field, whose full set of string values isn't documented and would risk silently falling through on future codexbar releases.
_Avoid_: stage, pace stage.

**Quota snapshot**:
A point-in-time response from `https://api.anthropic.com/api/oauth/usage`. Map keyed by quota axis (`five_hour`, `seven_day`, `seven_day_sonnet`, `monthly_limit`, `extra_usage`). Each entry: `{utilization: 0..1, resets_at: RFC3339, is_enabled: bool}`. `utilization` is the **Authoritative %** — denominator is Anthropic's own enforced cap.
_Avoid_: usage response, anthropic API.

**OAuth credentials**:
The `{accessToken, refreshToken, expiresAt}` pair Claude Code stores. Located in macOS Keychain (`Claude Code-credentials`), Linux libsecret (`secret-tool service=Claude Code-credentials`), or `~/.claude/.credentials.json` (`claudeAiOauth` field). Read-only from cc-statusline's perspective in the current design.
_Avoid_: token, API key.

**Authoritative %**:
Anthropic-returned `utilization` × 100 for a given quota axis. Distinct from the **Limit basis %** used in the fallback path.
_Avoid_: real %, true %.

**Prompt** _(fallback path only)_:
One external user message in a JSONL transcript that is neither a slash-command output (`<command-name>`) nor a meta message. Used as the fallback denominator unit when **Quota snapshot** unavailable.
_Avoid_: message, query, request.

**5h cycle / 5h block** _(fallback path only)_:
A rolling 5-hour window anchored to the earliest **Prompt** still inside it. In the OAuth path, replaced by `five_hour` quota with its own `resets_at`.
_Avoid_: session, block, period.

**Weekly window** _(fallback path only)_:
The rolling 7-day window ending now. In the OAuth path, replaced by `seven_day` + `seven_day_sonnet` quotas with their own `resets_at`.
_Avoid_: week, billing week.

**Model-hour** _(fallback path only)_:
Per-JSONL `wall_time × (responses_for_model / total_responses)`, summed per model across the **Weekly window**. Has no OAuth equivalent — Anthropic exposes utilization, not hours.
_Avoid_: hour, conversation hour.

**JSONL transcript**:
A `~/.claude/projects/<project>/<session>.jsonl` file containing one Claude Code session's full message stream. Authoritative source for the **fallback path** only.
_Avoid_: log, history, conversation file.

**Tier** _(fallback path only)_:
The user's Claude plan, selected via `CC_PLAN_TIER` env var. One of `free`, `pro`, `max_5x`, `max_20x`, `team_standard`, `team_premium`. Maps to a `limits.json` entry. Unused in OAuth path since Anthropic returns utilization against its own cap.
_Avoid_: plan, subscription level.

**Limit basis** _(fallback path only)_:
The `min` side of each tier range, used as fallback denominator. Distinct from **Authoritative %**.
_Avoid_: cap, quota.

### Plugin shape

**Plugin root**:
The directory referenced by `${CLAUDE_PLUGIN_ROOT}` at runtime. Contains `.claude-plugin/`, `statusline.py`, `config/limits.json`, `skills/`.

**Installer skill**:
`skills/install-statusline/SKILL.md`. Invoked as `/install-statusline`. Merges a `statusLine` block into `~/.claude/settings.json` pointing at `${CLAUDE_PLUGIN_ROOT}/statusline.py`.

## Relationships

- A **Tier** has 1..N usage axes (`5h_cycle` always; `weekly_sonnet` always; `weekly_opus` only for `max_5x`, `max_20x`, `team_premium`).
- The **codexbar source** carries the same axes as **Quota snapshot** (it reads the same Anthropic OAuth API under the hood) plus **Pace**, which has no counterpart on the other two sources.
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
