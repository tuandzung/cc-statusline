# cc-statusline

3-line Powerline statusline for [Claude Code](https://code.claude.com) — Catppuccin Macchiato palette, Nerd Font v3 icons, plan quota tracking from local JSONL history.

![statusline preview](docs/preview.png)

```
 N  ~/Working/Personal/cc-statusline   main  +2  0s   +120   -45 
  Sonnet 4.6   22% (45k/200k) 
  26% (2h 14m)   3% S (5d 19h) 
```

## Features

- **Line 1** — VIM mode (when enabled) · CWD · git branch/ahead/behind/dirty · session duration + lines changed
- **Line 2** — model name · context window usage % with raw token counts
- **Line 3** — 5h prompt block % with time remaining · weekly Sonnet/Opus hours %
- **Zero runtime deps** — pure Python 3.10+ stdlib, no pip install required
- **Per-file mtime cache** — JSONL re-parsed only when changed; aggregate refreshed every 30s
- **6 plan tiers** — `free`, `pro`, `max_5x`, `max_20x`, `team_standard`, `team_premium`

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

Set your plan tier in your shell profile:

```sh
export CC_PLAN_TIER=pro   # default
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

## How quota tracking works

Line 3 reads `~/.claude/projects/**/*.jsonl` directly — no external CLI required.

- **5h block** — counts external user prompts (excluding slash-command outputs) inside a rolling 5-hour window anchored to your first prompt in that window.
- **Weekly** — sums Sonnet/Opus model-hours (session wall-time × per-model response ratio) across a rolling 7-day window. Time remaining = next Monday 00:00 UTC.
- **Percentage** = `current / tier_min_limit × 100`. Conservative by design — you hit 100% before Anthropic's actual cap.

See [ADR-0001](docs/adr/0001-own-jsonl-parsing.md) for why we parse JSONL ourselves instead of using `ccusage`, and [ADR-0002](docs/adr/0002-prompts-and-hours-not-tokens.md) for why we track prompts/hours rather than tokens.

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
uv run pytest          # run all 45 tests
uv run pytest -v       # verbose
```

Requires [uv](https://docs.astral.sh/uv/).

## License

MIT
