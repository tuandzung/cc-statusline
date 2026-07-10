# cc-statusline

3-line Powerline statusline for [Claude Code](https://code.claude.com) — Catppuccin Macchiato palette, Nerd Font v3 icons, quota tracking via a local [CodexBar](https://github.com/steipete/CodexBar) daemon (with weekly Pace), Anthropic's OAuth usage API, or a local JSONL fallback.

![statusline preview](docs/preview.png)

```
 N  ~/Working/Personal/cc-statusline   main  +2  0s   +120   -45 
  Sonnet 4.6   22% (45k/200k) 
 󰺉 26% (2h 14m)   3% S  5% A (5d 19h) 
```

## Features

- **Line 1** — VIM mode (when enabled) · CWD · git branch/ahead/behind/dirty · session duration + lines changed
- **Line 2** — model name · context window usage % with raw token counts
- **Line 3** — 5h block % with time remaining · weekly %. Three sources, in priority order: a local `codexbar serve` daemon (only source with the weekly Pace indicator), Anthropic's authoritative `/api/oauth/usage` when Claude Code OAuth credentials are present, else a local JSONL heuristic.
- **Zero runtime deps** — pure Python 3.10+ stdlib, no pip install required
- **Per-file mtime cache** — JSONL re-parsed only when changed; aggregate refreshed every 30s. codexbar/OAuth snapshots cached 5 minutes; OAuth adds a 15-minute 429 cooldown.
- **6 plan tiers** (fallback path) — `free`, `pro`, `max_5x`, `max_20x`, `team_standard`, `team_premium`

## Install

### As a Claude Code plugin (recommended)

```sh
claude plugin install github:tuandzung/cc-statusline
```

Then in Claude Code:

```
/install-statusline
```

The skill writes the `statusLine` config block into `~/.claude/settings.json` automatically.

### Manual

1. Clone or download this repo.
2. Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "command": "python3 /absolute/path/to/cc-statusline/statusline.py",
    "refreshInterval": 30,
    "hideVimModeIndicator": true
  }
}
```

## Configuration

`CC_PLAN_TIER` and the limits table below apply **only on the JSONL fallback path**. The OAuth path uses Anthropic's own enforced cap as the denominator, so no plan tier is required.

```sh
export CC_PLAN_TIER=pro   # default; ignored when OAuth credentials are present
# options: free | pro | max_5x | max_20x | team_standard | team_premium
```

| Tier | 5h prompts (min) | Weekly Sonnet (min) | Weekly Opus (min) |
|---|---|---|---|
| `free` / `pro` | 10 | 40h | — |
| `max_5x` | 50 | 140h | 15h |
| `max_20x` | 200 | 240h | 24h |
| `team_standard` | 13 | 50h | — |
| `team_premium` | 63 | 250h | 19h |

Limits use the **conservative (`min`) side** of Anthropic's published ranges. Edit `config/limits.json` to adjust when Anthropic publishes precise numbers.

```sh
export CC_CODEXBAR_HOST=127.0.0.1   # codexbar daemon host (bare host, no scheme)
export CC_CODEXBAR_PORT=8080        # codexbar daemon port; invalid values fall back to 8080
```

Set `CC_STATUSLINE_DEBUG=1` to log which path is active and the last codexbar/OAuth error (if any) to `~/.cache/cc-statusline/debug.log`.

## How quota tracking works

### codexbar path (preferred)

When a [CodexBar](https://github.com/steipete/CodexBar) `codexbar serve` daemon is reachable at `http://{CC_CODEXBAR_HOST}:{CC_CODEXBAR_PORT}/usage?provider=claude`, Line 3 renders its 5h and weekly percentages plus a weekly **Pace** icon ( on track /  won't last to reset) — the only source that carries Pace. cc-statusline is a pure client: it never spawns, health-checks, or restarts the daemon, and a 200ms timeout keeps a hung daemon from lagging renders. Any failure falls through to OAuth. See [ADR-0005](docs/adr/0005-codexbar-as-preferred-quota-source.md).

### OAuth path

When `~/.claude/.credentials.json` is present and its access token is unexpired, Line 3 fetches `GET https://api.anthropic.com/api/oauth/usage` and renders Anthropic's authoritative `utilization` for `five_hour`, `seven_day_sonnet` (Sonnet weekly), and `seven_day` (all-model weekly). The 5h segment uses the filled hourglass icon (`󰺉`) to mark the authoritative path.

- **Read-only credentials.** cc-statusline never refreshes tokens, never writes back to `~/.claude/.credentials.json`, and never touches macOS Keychain or Linux libsecret. The refresh-token rotation risk is too steep for a statusline renderer to own.
- **Rate-limit aware.** Anthropic caps `/api/oauth/usage` at roughly 5 requests per access token. A 5-minute cache plus 15-minute cooldown on `HTTP 429` keep render-time fetches near zero.
- **Honest User-Agent.** Requests advertise `cc-statusline/<version> (+repo-url)`; the project does not impersonate Claude Code.

### JSONL fallback path

If neither codexbar nor OAuth is available, Line 3 reads `~/.claude/projects/**/*.jsonl` directly — the original behaviour.

- **5h block** — counts external user prompts (excluding slash-command outputs) inside a rolling 5-hour window anchored to your first prompt in that window.
- **Weekly** — sums Sonnet/Opus model-hours (session wall-time × per-model response ratio) across a rolling 7-day window. Time remaining = next Monday 00:00 UTC.
- **Percentage** = `current / tier_min_limit × 100`. Conservative by design — you hit 100% before Anthropic's actual cap.

See [ADR-0001](docs/adr/0001-own-jsonl-parsing.md) for why we parse JSONL ourselves instead of using `ccusage`, [ADR-0002](docs/adr/0002-prompts-and-hours-not-tokens.md) for why we track prompts/hours rather than tokens, and [ADR-0004](docs/adr/0004-oauth-quota-source-read-only.md) for the OAuth integration's read-only trust boundary.

## Colours

[Catppuccin Macchiato](https://github.com/catppuccin/catppuccin) with semantic colour assignments:

| Segment | Background |
|---|---|
| VIM NORMAL | blue |
| VIM INSERT | green |
| VIM VISUAL | mauve |
| CWD | blue |
| Git (clean) | mauve |
| Git (dirty) | peach |
| Metrics | teal |
| Model | sapphire |
| Context / 5h / Weekly | green → yellow → peach → red by `%` |

Thresholds: `< 50%` green · `< 75%` yellow · `< 90%` peach · `≥ 90%` red.

## Development

```sh
uv run pytest          # run all 111 tests
uv run pytest -v       # verbose
```

Requires [uv](https://docs.astral.sh/uv/).

## License

MIT
