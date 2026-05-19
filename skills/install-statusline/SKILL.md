---
name: install-statusline
description: Install cc-statusline into Claude Code settings
allowed-tools:
  - Read
  - Write
  - Bash
---

# install-statusline

Configure cc-statusline in Claude Code settings.

## Instructions

Read `~/.claude/settings.json`. If missing, create it with `{}`.

Merge the following into the existing JSON (preserve all existing keys):

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ${CLAUDE_PLUGIN_ROOT}/statusline.py",
    "refreshInterval": 30,
    "hideVimModeIndicator": true
  }
}
```

If running as a plugin (`CLAUDE_PLUGIN_ROOT` is set), use the template above verbatim.

If running standalone (no plugin), replace `${CLAUDE_PLUGIN_ROOT}` with the absolute path to the directory containing `statusline.py`. Ask the user for the path if unsure.

Write the merged result back to `~/.claude/settings.json`.

Then ask the user to set their plan tier by adding this to their shell profile:

```sh
export CC_PLAN_TIER=pro  # options: free, pro, max_5x, max_20x, team_standard, team_premium
```

Confirm to the user what was written and remind them to reload their shell or source their profile.
