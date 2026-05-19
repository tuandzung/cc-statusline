# Distribute as a Claude Code plugin, not a loose script

Community Claude Code statuslines (CCometixLine, ccline, cc-statusline-templates) are mostly bare scripts: clone the file, copy into `~/.claude/`, manually edit `settings.json`. We instead ship a full Claude Code plugin (`.claude-plugin/marketplace.json`, `.claude-plugin/plugin.json`) with an installer skill at `skills/install-statusline/SKILL.md`. The precedent is `glm-statusline-cc`, which uses the same shape.

Reasoning:

- **`${CLAUDE_PLUGIN_ROOT}` resolution.** When installed as a plugin, the `statusLine.command` field in `~/.claude/settings.json` can reference `python3 ${CLAUDE_PLUGIN_ROOT}/statusline.py`. Claude Code expands this to the install path. Without plugin packaging the user has to hardcode an absolute path that breaks when the repo moves.
- **Slash-command install.** `/install-statusline` (the [[installer skill]]) reads `~/.claude/settings.json`, merges the `statusLine` block, and writes it back. The user never edits JSON by hand and never copies files around.
- **Marketplace discoverability.** `/plugin marketplace add <repo>` then `/plugin install cc-statusline` is one path forward for distribution. Loose scripts don't get that.

## Consequences

The plugin is **not** runnable as a plain `python3 statusline.py` from a checkout without manual `CLAUDE_PLUGIN_ROOT` and `CC_PLAN_TIER` setup — fine for the supported install path but worth documenting in README for users who want to run-and-modify. Tests must mock `CLAUDE_PLUGIN_ROOT` and the stdin JSON shape so they don't depend on the plugin install being live.
