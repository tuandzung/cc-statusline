# Query codexbar's local daemon as the preferred quota source, ahead of our own OAuth read

[ADR-0004](./0004-oauth-quota-source-read-only.md) established our own read of `~/.claude/.credentials.json` plus `GET /api/oauth/usage` as the preferred Line 3 source, with JSONL as fallback. We're inserting a third source ahead of both: [steipete/CodexBar](https://github.com/steipete/CodexBar)'s `codexbar serve` local daemon, queried at `GET http://127.0.0.1:8080/usage?provider=claude`.

codexbar reads the same Anthropic OAuth API we do, so it doesn't give us new numbers. What it gives us is **Pace** — a weekly-only ahead/behind-schedule indicator — for free, without us implementing the projection math ourselves.

## Considered options

- **Compute Pace ourselves** from `utilization` + `resets_at`, no new dependency. Rejected: we want codexbar's own computation, not a reimplementation.
- **Own-OAuth first, codexbar as enrichment only.** Rejected in favor of codexbar-first: simpler priority chain, and codexbar also covers users whose credentials live only in Keychain/libsecret (which ADR-0004 explicitly punts to JSONL).
- **One-shot `codexbar cost --json` subprocess per cache-miss**, matching how we already shell out for JSONL parsing. Rejected in favor of querying the daemon over HTTP — no per-call process spawn.

## Design

- **Pure client, no lifecycle ownership.** cc-statusline never spawns, health-checks, or restarts `codexbar serve`. It's assumed to already be running (e.g. via the CodexBar.app menu bar app, or the user's own service unit). Any failure to connect — not listening, timeout, malformed JSON, Claude provider not configured in codexbar — is treated as "source unavailable" and falls through to our own OAuth read, exactly as if codexbar didn't exist.
- **200ms timeout.** Loopback HTTP is normally low-single-digit ms; 200ms is generous headroom while still failing fast enough that a hung daemon never produces a visibly laggy render.
- **No backoff state.** Unlike Anthropic's 429 cooldown in ADR-0004, an unreachable localhost port fails near-instantly, so there's nothing to protect against by adding a cooldown. We just retry on the next cache-refresh, governed by the existing TTL.
- **Cached like `oauth_snapshot.json`.** Same fresh/stale/refetch state machine from ADR-0004, in a sibling file `codexbar_snapshot.json`.
- **Auto-detected, on by default.** No env var gate — a failed probe is indistinguishable from codexbar not being installed at all, so there's no user-visible cost to trying.
- **`willLastToReset`, not `stage`.** codexbar's pace payload includes a `stage` string (`"ahead"`, `"slightlyBehind"`, ...) whose full value set isn't documented anywhere we could find. We use the boolean `willLastToReset` instead — it can't silently drift into an unrecognized value the way an open-ended enum can.
- **Weekly only, icon only.** 5h-window pace is too noisy over a few hours to be worth showing. The icon is appended to the existing weekly segment; no new segment, no ETA text — Powerline segments are short colored chunks, not sentences.
- **Pace has no Line-3-shape guarantee across sources.** Own-OAuth and JSONL renders look exactly as they did before this change. Only codexbar-served renders show the Pace icon. This is a deliberate departure from ADR-0004's "Line 3 segments remain shape-stable" principle, scoped narrowly to this one bonus indicator.

## Consequences

- We take on a second third-party, unversioned, undocumented JSON schema (codexbar's `/usage` response) alongside Anthropic's own private `/api/oauth/usage` shape. If codexbar changes its response shape, the Pace icon silently stops appearing (fails safe into the own-OAuth fallback) rather than misreporting — because any parse failure is treated as "unavailable," not "zero."
- Users who install and run `codexbar serve` get quota data from a third-party binary we don't control, ahead of our own implementation, purely to inherit its Pace computation. If that computation is ever found to be wrong, we have no way to fix it ourselves short of dropping this source.
- The weekly segment's meaning is now source-dependent: with codexbar, it carries a trend icon; without it, it doesn't. A user switching between machines with/without codexbar installed will see Line 3 change shape.
