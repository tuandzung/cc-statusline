# Read-only OAuth quota source, with JSONL fallback intact

cc-statusline historically computes Line 3 (`5h block %`, `weekly Sonnet/Opus %`) from local JSONL transcripts via a [model-hour heuristic](./0002-prompts-and-hours-not-tokens.md) and a [tier table](../../config/limits.json). Anthropic exposes the same information authoritatively at `GET https://api.anthropic.com/api/oauth/usage` for Claude Code's OAuth client (response shape: `five_hour`, `seven_day`, `seven_day_sonnet`, `monthly_limit`, `extra_usage`, each with `utilization` + `resets_at` + `is_enabled`). The endpoint is undocumented and OAuth-gated (`anthropic-beta: oauth-2025-04-20`), and rate-limited at approximately 5 requests per access token.

When the user has Claude Code OAuth credentials present (`~/.claude/.credentials.json` → `claudeAiOauth`), we prefer the authoritative endpoint; otherwise we keep the JSONL path. This ADR records four coupled choices.

## Read-only credentials

We **read** `accessToken` from the credentials file. We never read or use `refreshToken`. We never refresh, never write back, never touch macOS Keychain or Linux libsecret.

The onWatch project ([commit 0ab1009](https://github.com/onllm-dev/onWatch/commit/0ab1009)) bypasses the 5-req rate limit by refreshing the OAuth token on every 429 and persisting the rotated pair back to the credential store. That is appropriate for a long-running monitoring daemon. For a statusline plugin invoked on every Claude Code render, the failure mode is severe: refresh tokens are one-time-use (rotation); a single bug between "refresh succeeded" and "new pair persisted" leaves the user unable to log into Claude Code until they re-auth. We are not willing to own that risk in a script whose only output is a coloured terminal line.

Read-only caps us at ~5 fetches per access-token lifetime. We compensate with a 5-minute cache TTL and a 15-minute backoff after any 429 — most renders consume zero requests.

## File-only credential discovery

We only read `~/.claude/.credentials.json`. We do **not** invoke `security find-generic-password` (macOS Keychain) or `secret-tool` (Linux libsecret). Reasons: keychain access from a statusline render risks surfacing an OS unlock prompt, requires the binary to be installed, and adds subprocess latency on every cache miss. Users whose Claude Code wrote credentials only to Keychain or libsecret remain on the JSONL fallback path — they lose nothing, they just don't gain the OAuth path.

An env-var escape hatch (`CC_STATUSLINE_OAUTH_TOKEN`) can be added later if user demand justifies it. Not in v1.

## Honest User-Agent

We send `User-Agent: cc-statusline/<version> (+<repo-url>)`. We do **not** impersonate `claude-code/2.1.69` as onWatch does. We are a third party using credentials provisioned for a first-party client; lying about that conflicts with the rest of this project's posture. If Anthropic later distinguishes traffic by UA and blocks ours, the JSONL fallback ensures no regression — Line 3 returns to today's behaviour without code changes.

## Layout and graceful degradation

Line 3 segments remain shape-stable (5h + weekly). In OAuth mode the weekly segment becomes `S {pct}% A {pct}%` mapping `seven_day_sonnet` and `seven_day` (Anthropic does not expose Opus separately). The 5h segment icon swaps to a filled hourglass variant to mark **Authoritative %**; fallback uses the existing icon. `CC_STATUSLINE_DEBUG=1` writes path-taken plus last error to `~/.cache/cc-statusline/debug.log`.

State machine:
- Fresh cache (< 5 min): render snapshot.
- Stale cache (5–30 min) and `resets_at` still future: render snapshot, recompute "time remaining" from `resets_at`.
- Cache > 30 min, or `resets_at` already passed: refetch ignoring 429-cooldown once (cap reset just happened anyway), else fall back to JSONL.
- 429 hit: set `oauth_blocked_until = now + 15min`; while blocked and no fresh snapshot exists, render via JSONL.

Cache colocates with existing aggregate at `/tmp/cc-statusline-cache-{uid}/oauth_snapshot.json` and `oauth_state.json` (last-429 timestamp, last credential mtime).

## Tests

`tests/test_oauth.py`: hermetic. Monkey-patches `time.time()` and `urllib.request.urlopen`. Fixtures mirror the onWatch fixture shape (`utilization`, `resets_at`, `is_enabled`). Covers credentials parser, response parser, cache state transitions, 429 cooldown, stale-window resolution, JSONL fallback trigger on every failure mode. No live network call in CI.

## Consequences

- We take on a second load-bearing private API alongside the [JSONL schema](./0001-own-jsonl-parsing.md). If Anthropic changes the `/api/oauth/usage` response shape (renames `five_hour`, alters `utilization` scale, changes `resets_at` format), the OAuth path silently misreports until we update the parser. Fixtures track the current shape but cannot detect drift on their own.
- This ADR partially supersedes ADR-0001's framing: JSONL is no longer the *sole* authoritative source; it is the fallback. The reasoning in ADR-0001 still applies for users without OAuth credentials.
- We accept that Anthropic could block our UA at any time. Fallback intact means worst case = today.
- We do **not** support Max-20x Opus dedicated metering in OAuth mode (Anthropic does not expose it). Users who need that figure must rely on the JSONL fallback or wait for Anthropic to publish an `opus`-specific key.
- The `CC_PLAN_TIER` env var and `config/limits.json` remain meaningful only on the fallback path. They are not deprecated; they are scoped.
- Changing this ADR's "read-only" stance later (e.g. adding refresh-and-writeback for unlimited polling) is a trust-boundary change. It would require an explicit follow-up ADR and a migration plan for users whose tokens get rotated mid-flight.
