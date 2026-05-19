# Own the JSONL transcript parsing instead of shelling out to `ccusage`

Claude Code writes every assistant turn to `~/.claude/projects/<project>/<session>.jsonl` with full message metadata and token usage. `ccusage` is the de-facto CLI that aggregates these files, exposes `blocks` and `weekly` subcommands, and has community testing. We considered depending on it and rejected that path.

We parse the JSONL files ourselves in pure Python stdlib. Reasons:

- **Zero runtime deps.** The plugin needs to run under `python3 ${CLAUDE_PLUGIN_ROOT}/statusline.py` without requiring `npx ccusage` (Node + ccusage install) or `bun x ccusage`. A Claude Code statusline that doesn't degrade gracefully when its sidecar is missing is a footgun for new users.
- **No subprocess latency.** Even a warm `bun x ccusage blocks --json` is 100–300 ms; a `npx` cold-start is 1–2 s. The statusline runs on every assistant message (debounced 300 ms) and on `refreshInterval` ticks. We can't pay that cost per render. Owning the parser lets us cache per-file by mtime and finish in single-digit ms.
- **Different limit model.** We track [[prompts and model-hours]], not tokens (see [ADR-0002](./0002-prompts-and-hours-not-tokens.md)). `ccusage`'s `blocks` and `weekly` outputs are token/cost-centric and don't give us what we need without re-aggregation anyway.

## Consequences

We take on Claude Code's JSONL schema as a load-bearing private API. If Anthropic changes message shapes (e.g. how `userType` is encoded, how `isMeta` works, how `message.model` is named), our line 3 silently misreports until we fix the parser. Tests under `tests/` should include fixture JSONL snippets from known Claude Code versions so drift is detected on CI.
