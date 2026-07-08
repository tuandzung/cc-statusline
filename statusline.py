#!/usr/bin/env python3
"""cc-statusline — 3-line Powerline statusline for Claude Code.

Line 1: [VIM mode]  [CWD]  [Git branch/ahead/behind/dirty]  [Session metrics]
Line 2: [Model]  [Context %]
Line 3: [5h block %]  [Weekly %]

Line 3 has three data sources, in priority order:
  1. codexbar path — third-party `codexbar serve` local daemon
                   (https://github.com/steipete/CodexBar), queried at
                   http://127.0.0.1:8080/usage?provider=claude. Pure client:
                   never spawned or supervised by us. Only source with Pace.
  2. OAuth path  — Anthropic /api/oauth/usage (authoritative utilization).
                   Enabled when ~/.claude/.credentials.json contains a
                   claudeAiOauth.accessToken. Read-only; never refreshes.
  3. JSONL path  — local model-hour heuristic over ~/.claude/projects/*.jsonl,
                   denominated against CC_PLAN_TIER. Used when (1) and (2) are
                   unavailable.

Config:
  CC_PLAN_TIER         free|pro|max_5x|max_20x|team_standard|team_premium (default: pro)
  CC_STATUSLINE_DEBUG  set to "1" to log OAuth path decisions to
                       ~/.cache/cc-statusline/debug.log

Requires: Python 3.10+, Nerd Font v3 patched terminal font, 24-bit colour terminal.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

__version__ = "1.2.0"

# ── Catppuccin Macchiato palette (24-bit RGB) ─────────────────────────────────
_P: dict[str, tuple[int, int, int]] = {
    "rosewater": (244, 219, 214),
    "flamingo": (240, 198, 198),
    "pink": (245, 189, 230),
    "mauve": (198, 160, 246),
    "red": (237, 135, 150),
    "maroon": (238, 153, 160),
    "peach": (245, 169, 127),
    "yellow": (238, 212, 159),
    "green": (166, 218, 149),
    "teal": (139, 213, 202),
    "sky": (145, 215, 227),
    "sapphire": (125, 196, 228),
    "blue": (138, 173, 244),
    "lavender": (183, 189, 248),
    "text": (202, 211, 245),
    "subtext1": (184, 192, 224),
    "overlay2": (147, 154, 183),
    "overlay1": (128, 135, 162),
    "surface2": (91, 96, 120),
    "surface1": (73, 77, 100),
    "surface0": (54, 58, 78),
    "base": (36, 39, 58),
    "mantle": (30, 32, 48),
    "crust": (24, 25, 38),
}

# ── Nerd Font v3 icons ────────────────────────────────────────────────────────
ICON_VIM = ""  # nf-dev-vim
ICON_FOLDER = ""  # nf-cod-folder
ICON_BRANCH = ""  # nf-dev-git_branch
ICON_AHEAD = ""  # nf-fa-arrow_up
ICON_BEHIND = ""  # nf-fa-arrow_down
ICON_DIRTY = ""  # nf-fa-exclamation_circle
ICON_TIMER = "\U000f051b"  # nf-md-timer_outline  (U+F051B)
ICON_ADD = ""  # nf-oct-diff_added
ICON_DEL = ""  # nf-oct-diff_removed
ICON_MODEL = "✱"  # nf-fa-robot
ICON_BRAIN = "󰧑"  # nf-md-brain          (U+F068B)
ICON_BLOCK = ""  # nf-md-hourglass_empty (U+F03BC) — JSONL fallback
ICON_BLOCK_LIVE = ""  # nf-md-hourglass_full  (U+F0E89) — OAuth authoritative
ICON_WEEKLY = ""  # nf-fa-calendar
ICON_PACE_OK = "\uf058"  # nf-fa-check_circle — codexbar: will last to reset
ICON_PACE_BEHIND = "\uf071"  # nf-fa-exclamation_triangle — codexbar: won't last

# Powerline right-filled separator (U+E0B0)
_CHEV = ""

# VIM mode → display letter and bg palette name
_VIM_LETTER: dict[str, str] = {
    "NORMAL": "N",
    "INSERT": "I",
    "VISUAL": "V",
    "VISUAL LINE": "V",
}
_VIM_COLOR: dict[str, str] = {
    "NORMAL": "blue",
    "INSERT": "green",
    "VISUAL": "mauve",
    "VISUAL LINE": "lavender",
}

# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _fg(name: str) -> str:
    r, g, b = _P[name]
    return f"\x1b[38;2;{r};{g};{b}m"

def _bg(name: str) -> str:
    r, g, b = _P[name]
    return f"\x1b[48;2;{r};{g};{b}m"

_RESET = "\x1b[0m"

def _pct_color(pct: float) -> str:
    if pct >= 90:
        return "red"
    if pct >= 75:
        return "peach"
    if pct >= 50:
        return "yellow"
    return "green"

# ── Powerline renderer ────────────────────────────────────────────────────────

class Segment:
    """A single coloured Powerline chunk."""

    __slots__ = ("content", "bg", "fg")

    def __init__(self, content: str, bg: str, fg: str = "crust") -> None:
        self.content = content  # icon + text, no surrounding spaces
        self.bg = bg
        self.fg = fg

def render_line(segments: list[Segment]) -> str:
    """Join Segments with Powerline chevrons into a single ANSI string."""
    if not segments:
        return ""
    parts: list[str] = []
    for i, seg in enumerate(segments):
        if i > 0:
            # Chevron: fg = prev segment's bg colour, bg = this segment's bg colour
            r, g, b = _P[segments[i - 1].bg]
            parts.append(f"{_bg(seg.bg)}\x1b[38;2;{r};{g};{b}m{_CHEV}")
        parts.append(f"{_bg(seg.bg)}{_fg(seg.fg)} {seg.content} ")
    # Final chevron: bg = terminal default (reset), fg = last segment's bg colour
    r, g, b = _P[segments[-1].bg]
    parts.append(f"{_RESET}\x1b[38;2;{r};{g};{b}m{_CHEV}{_RESET}")
    return "".join(parts)

# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    """Convert seconds to human-readable string: '4d 6h', '2h 14m', '47m', '12s'."""
    if seconds <= 0:
        return "0s"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if d:
        return f"{d}d {h}h" if h else f"{d}d"
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    if m:
        return f"{m}m {s}s" if s else f"{m}m"
    return f"{s}s"

def _fmt_cwd(path: str) -> str:
    """Shorten a filesystem path: replace $HOME with ~, collapse mid if >40 chars."""
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home) :]
    if len(path) <= 40:
        return path
    parts = path.split("/")
    head = parts[0]  # "~" or ""
    tail = "/".join(parts[-2:])
    return f"{head}/…/{tail}"

# ── Git ───────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Optional[str] = None) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=2, cwd=cwd)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None

def _git_info(cwd: str) -> Optional[dict]:
    """Return {branch, ahead, behind, dirty} or None if not a git repo."""
    out = _run(
        ["git", "status", "--porcelain", "-b", "--untracked-files=no"],
        cwd,
    )
    if out is None:
        return None
    lines = out.splitlines()
    if not lines or not lines[0].startswith("## "):
        return None

    header = lines[0][3:]  # strip "## "
    ahead = behind = 0

    ab = re.search(r"\[(?:ahead (\d+))?(?:,\s*)?(?:behind (\d+))?\]", header)
    if ab:
        ahead = int(ab.group(1) or 0)
        behind = int(ab.group(2) or 0)
    else:
        only_behind = re.search(r"\[behind (\d+)\]", header)
        if only_behind:
            behind = int(only_behind.group(1))

    branch = re.split(r"\.\.\.|\s+\[", header)[0].strip()
    if branch.startswith("No commits yet on "):
        branch = branch[len("No commits yet on ") :]
    elif branch in ("HEAD (no branch)", "HEAD"):
        sha = _run(["git", "rev-parse", "--short", "HEAD"], cwd)
        branch = f"➤ {sha}" if sha else "detached"

    dirty = any(ln.strip() for ln in lines[1:])
    return {"branch": branch, "ahead": ahead, "behind": behind, "dirty": dirty}

# ── JSONL usage tracker ───────────────────────────────────────────────────────

_CACHE_DIR = Path(f"/tmp/cc-statusline-cache-{os.getuid()}")
_AGG_CACHE = _CACHE_DIR / "aggregate.json"
_SES_DIR = _CACHE_DIR / "sessions"
_AGG_TTL = 30  # seconds between full re-scans

# ── Cache IO substrate ────────────────────────────────────────────────────────
# Single owner of read/write/swallow for every JSON file under _CACHE_DIR.
# TTL / mtime / stale-window semantics stay caller-side; this layer only
# handles parent-mkdir, JSON parse, atomic write, and silent failure.

def _cache_load(path: Path) -> Optional[dict]:
    if not path.is_relative_to(_CACHE_DIR):
        _dlog(f"cache: refusing load outside _CACHE_DIR: {path}")
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        _dlog(f"cache: load failed {path}: {e}")
        return None

def _cache_save(path: Path, data: dict) -> None:
    if not path.is_relative_to(_CACHE_DIR):
        _dlog(f"cache: refusing save outside _CACHE_DIR: {path}")
        return
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except Exception as e:
        _dlog(f"cache: save failed {path}: {e}")
        try:
            tmp.unlink()
        except Exception:
            pass

def _session_cache_path(jsonl: Path) -> Path:
    h = hashlib.md5(str(jsonl).encode(), usedforsecurity=False).hexdigest()
    return _SES_DIR / f"{h}.json"

def _parse_ts(ts: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def _is_command_msg(content) -> bool:
    text = content if isinstance(content, str) else ""
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text += item.get("text", "")
    return "<command-name>" in text or "<local-command-stdout>" in text

def _parse_jsonl(path: Path) -> Optional[dict]:
    """Parse one JSONL session file. Returns dict or None on failure."""
    timestamps: list[float] = []
    prompts = sonnet_r = opus_r = total_r = 0
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                ts = msg.get("timestamp")
                if ts:
                    t = _parse_ts(ts)
                    if t:
                        timestamps.append(t)

                mtype = msg.get("type")
                if mtype == "user":
                    m = msg.get("message", {})
                    if (
                        m.get("role") == "user"
                        and not msg.get("isMeta", False)
                        and msg.get("userType") == "external"
                    ):
                        content = m.get("content", "")
                        if content and not _is_command_msg(content):
                            prompts += 1
                elif mtype == "assistant":
                    model_s = (msg.get("message", {}).get("model") or "").lower()
                    if "opus" in model_s:
                        opus_r += 1
                        total_r += 1
                    elif "sonnet" in model_s or "haiku" in model_s:
                        sonnet_r += 1
                        total_r += 1
    except Exception:
        return None

    if not timestamps:
        return None

    return {
        "start_ts": min(timestamps),
        "end_ts": max(timestamps),
        "prompts": prompts,
        "sonnet_r": sonnet_r,
        "opus_r": opus_r,
        "total_r": max(total_r, 1),
    }

def _cached_session(jsonl: Path, mtime: float) -> Optional[dict]:
    d = _cache_load(_session_cache_path(jsonl))
    if d and d.get("mtime") == mtime:
        return d
    return None

def _save_session(jsonl: Path, mtime: float, info: dict) -> None:
    _cache_save(_session_cache_path(jsonl), {**info, "mtime": mtime})

def _load_sessions() -> list[dict]:
    projects = Path.home() / ".claude" / "projects"
    if not projects.exists():
        return []
    sessions: list[dict] = []
    for jsonl in projects.rglob("*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        info = _cached_session(jsonl, mtime)
        if info is None:
            info = _parse_jsonl(jsonl)
            if info:
                _save_session(jsonl, mtime, info)
        if info:
            sessions.append(info)
    return sessions

def _compute_stats(sessions: list[dict], now: float) -> dict:
    window_5h = now - 5 * 3600
    window_7d = now - 7 * 24 * 3600

    # 5h block: prorate prompts by overlap fraction inside the 5h window
    prompt_5h = 0
    cycle_start = now  # will track earliest session start in the 5h window
    for s in sessions:
        if s["end_ts"] < window_5h:
            continue
        ov_start = max(s["start_ts"], window_5h)
        ov_end = min(s["end_ts"], now)
        if ov_end <= ov_start:
            continue
        dur = max(s["end_ts"] - s["start_ts"], 1)
        prompt_5h += round(s["prompts"] * (ov_end - ov_start) / dur)
        cycle_start = min(cycle_start, max(s["start_ts"], window_5h))

    if cycle_start == now:
        cycle_start = window_5h
    time_remaining_5h = max(0.0, (cycle_start + 5 * 3600) - now)

    # Weekly: model-hours (session wall-time × per-model response ratio)
    sonnet_hours = opus_hours = 0.0
    for s in sessions:
        if s["end_ts"] < window_7d:
            continue
        ov_start = max(s["start_ts"], window_7d)
        ov_end = min(s["end_ts"], now)
        dur_h = max(0.0, ov_end - ov_start) / 3600
        sonnet_hours += dur_h * s["sonnet_r"] / s["total_r"]
        opus_hours += dur_h * s["opus_r"] / s["total_r"]

    # Next Monday 00:00 UTC = weekly reset point
    dt_now = datetime.fromtimestamp(now, tz=timezone.utc)
    days_until_mon = (7 - dt_now.weekday()) % 7 or 7
    next_mon = (dt_now + timedelta(days=days_until_mon)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    weekly_reset_remaining = next_mon.timestamp() - now

    return {
        "prompt_5h": prompt_5h,
        "time_remaining_5h": time_remaining_5h,
        "sonnet_hours": sonnet_hours,
        "opus_hours": opus_hours,
        "weekly_reset_remaining": weekly_reset_remaining,
    }

def _get_usage_stats() -> Optional[dict]:
    """Return aggregate stats from cache or freshly computed."""
    now = time.time()
    agg = _cache_load(_AGG_CACHE)
    if agg and now - agg.get("computed_at", 0) < _AGG_TTL:
        return agg

    try:
        sessions = _load_sessions()
        stats = _compute_stats(sessions, now)
        stats["computed_at"] = now
        _cache_save(_AGG_CACHE, stats)
        return stats
    except Exception:
        return None

# ── OAuth quota path ──────────────────────────────────────────────────────────
#
# Reads Claude Code's own OAuth access token from ~/.claude/.credentials.json
# and calls Anthropic's authoritative /api/oauth/usage endpoint. Strictly
# read-only: never refreshes, never writes back, never touches keychains.
# Falls back to the JSONL path on any failure (missing creds, expired token,
# 401/403/429, network error, parse error).
#
# See docs/adr/0004-oauth-quota-source-read-only.md for the design rationale.

_OAUTH_URL = "https://api.anthropic.com/api/oauth/usage"
_OAUTH_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
_OAUTH_SNAPSHOT = _CACHE_DIR / "oauth_snapshot.json"
_OAUTH_STATE = _CACHE_DIR / "oauth_state.json"
_OAUTH_CACHE_TTL = 300  # 5 min — render stale snapshot before refetching
_OAUTH_STALE_WINDOW = 1800  # 30 min — beyond this, fall back to JSONL
_OAUTH_429_COOLDOWN = 900  # 15 min — back off after rate limit
_OAUTH_TIMEOUT = 1.5  # seconds — cap render-path latency
_OAUTH_AXES = ("five_hour", "seven_day", "seven_day_sonnet")
_USER_AGENT = (
    f"cc-statusline/{__version__} (+https://github.com/tuandzung/cc-statusline)"
)

_DEBUG_ENABLED = os.environ.get("CC_STATUSLINE_DEBUG") == "1"
_DEBUG_LOG = Path.home() / ".cache" / "cc-statusline" / "debug.log"

def _dlog(msg: str) -> None:
    if not _DEBUG_ENABLED:
        return
    try:
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass

def _read_oauth_credentials() -> Optional[dict]:
    """Return {access, expires_at} from ~/.claude/.credentials.json or None.

    expires_at is unix seconds; may be None if the file omits it.
    """
    if not _OAUTH_CREDS_PATH.exists():
        return None
    try:
        data = json.loads(_OAUTH_CREDS_PATH.read_text())
    except Exception:
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict):
        return None
    access = oauth.get("accessToken")
    if not isinstance(access, str) or not access:
        return None
    exp_ms = oauth.get("expiresAt")
    expires_at: Optional[float] = None
    if isinstance(exp_ms, (int, float)) and exp_ms > 0:
        expires_at = float(exp_ms) / 1000.0
    return {"access": access, "expires_at": expires_at}

def _fetch_oauth_usage(access_token: str) -> tuple[str, Optional[dict]]:
    """Call /api/oauth/usage. Return (status, body).

    status ∈ {"ok", "rate_limited", "auth_error", "server_error", "network_error"}.
    body is the parsed JSON on "ok", else None.
    """
    req = urllib.request.Request(
        _OAUTH_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_OAUTH_TIMEOUT) as resp:
            raw = resp.read(65536)
        body = json.loads(raw)
        if not isinstance(body, dict):
            return "network_error", None
        return "ok", body
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "rate_limited", None
        if e.code in (401, 403):
            return "auth_error", None
        if 500 <= e.code < 600:
            return "server_error", None
        return "network_error", None
    except Exception:
        return "network_error", None

def _parse_resets_at(value) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def _normalize_oauth_response(body: dict, now: float) -> Optional[dict]:
    """Pick the axes we render, drop disabled/null/unknown ones."""
    axes: dict[str, dict] = {}
    for key in _OAUTH_AXES:
        entry = body.get(key)
        if not isinstance(entry, dict):
            continue
        if entry.get("is_enabled") is False:
            continue
        util = entry.get("utilization")
        if not isinstance(util, (int, float)):
            continue
        axes[key] = {
            "utilization": float(util),
            "resets_at": _parse_resets_at(entry.get("resets_at")),
        }
    if not axes:
        return None
    return {"fetched_at": now, "axes": axes}

def _snapshot_reset_passed(snapshot: dict, now: float) -> bool:
    for ax in snapshot.get("axes", {}).values():
        ra = ax.get("resets_at")
        if isinstance(ra, (int, float)) and ra < now:
            return True
    return False

def _get_oauth_stats() -> Optional[dict]:
    """Return a fresh-or-stale-within-window OAuth snapshot, or None to fall back.

    State machine:
      - No creds              → None
      - Token clearly expired → reuse snapshot if young, else None
      - Cache < 5 min old     → reuse snapshot
      - 30 min ≥ age ≥ 5 min  → refetch (unless 429-cooldown); reuse on failure if not over-stale
      - Age > 30 min          → refetch; fall back if refetch fails
      - Any quota's resets_at already passed → force refetch (cooldown bypassed once)
      - 429                   → mark cooldown 15 min, reuse snapshot if fresh-enough
    """
    now = time.time()

    creds = _read_oauth_credentials()
    if not creds:
        _dlog("oauth: no credentials, falling back")
        return None

    snapshot = _cache_load(_OAUTH_SNAPSHOT)
    state = _cache_load(_OAUTH_STATE) or {}
    blocked_until = state.get("blocked_until", 0)

    snap_age = float("inf")
    if snapshot and isinstance(snapshot.get("fetched_at"), (int, float)):
        snap_age = now - snapshot["fetched_at"]

    reset_passed = bool(snapshot) and _snapshot_reset_passed(snapshot, now)

    # If access token already expired, don't bother fetching — would 401.
    token_expired = creds["expires_at"] is not None and creds["expires_at"] < now
    if token_expired:
        _dlog("oauth: token expired, skipping fetch")
        if snapshot and snap_age < _OAUTH_STALE_WINDOW and not reset_passed:
            return snapshot
        return None

    must_fetch = snap_age >= _OAUTH_CACHE_TTL or reset_passed
    cooldown_active = now < blocked_until
    fetch_allowed = must_fetch and (not cooldown_active or reset_passed)

    if fetch_allowed:
        status, body = _fetch_oauth_usage(creds["access"])
        if status == "ok" and body is not None:
            new_snap = _normalize_oauth_response(body, now)
            if new_snap:
                _cache_save(_OAUTH_SNAPSHOT, new_snap)
                _cache_save(_OAUTH_STATE, {"blocked_until": 0})
                _dlog(f"oauth: fetched ok ({list(new_snap['axes'])})")
                return new_snap
            _dlog("oauth: response normalized to empty, falling back")
        elif status == "rate_limited":
            _cache_save(_OAUTH_STATE, {"blocked_until": now + _OAUTH_429_COOLDOWN})
            _dlog("oauth: 429 received, cooldown 15m")
        else:
            _dlog(f"oauth: fetch status={status}")

    # Fall back to stale snapshot if within window and reset hasn't passed.
    if snapshot and snap_age < _OAUTH_STALE_WINDOW and not reset_passed:
        return snapshot
    return None

# ── codexbar quota path ─────────────────────────────────────────────────────
#
# Queries the third-party `codexbar serve` daemon (steipete/CodexBar) for the
# same Anthropic quota data as the OAuth path, plus a weekly Pace indicator
# codexbar computes itself. We are a pure client: we never spawn, health-check,
# or restart the daemon. Any failure — unreachable, timeout, malformed JSON,
# Claude provider not configured in codexbar — falls back to the OAuth path.
#
# See docs/adr/0005-codexbar-as-preferred-quota-source.md for the rationale.

_CODEXBAR_URL = "http://127.0.0.1:8080/usage?provider=claude"
_CODEXBAR_SNAPSHOT = _CACHE_DIR / "codexbar_snapshot.json"
_CODEXBAR_CACHE_TTL = 300  # 5 min — mirrors _OAUTH_CACHE_TTL
_CODEXBAR_STALE_WINDOW = 1800  # 30 min — mirrors _OAUTH_STALE_WINDOW
_CODEXBAR_TIMEOUT = 0.2  # 200ms — a hung local daemon must never lag a render

def _fetch_codexbar_usage() -> Optional[dict]:
    """GET codexbar's /usage endpoint for the claude provider. None on any failure."""
    req = urllib.request.Request(
        _CODEXBAR_URL, headers={"Accept": "application/json"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=_CODEXBAR_TIMEOUT) as resp:
            raw = resp.read(65536)
        body = json.loads(raw)
        return body if isinstance(body, dict) else None
    except Exception as e:
        _dlog(f"codexbar: fetch failed: {e}")
        return None

def _normalize_codexbar_response(body: dict, now: float) -> Optional[dict]:
    """Defensive parse of codexbar's /usage response.

    This is an unversioned third-party JSON shape we don't control. Any
    missing or mistyped field is treated as absent rather than raised, so
    schema drift fails safe into the OAuth fallback instead of misreporting.
    """
    payload = body
    claude = payload.get("claude")
    if isinstance(claude, dict):
        payload = claude

    def _axis(section) -> tuple[Optional[int], Optional[float]]:
        if not isinstance(section, dict):
            return None, None
        pct = section.get("usedPercent")
        pct = min(100, max(0, round(pct))) if isinstance(pct, (int, float)) else None
        return pct, _parse_resets_at(section.get("resetsAt"))

    five_pct, five_resets = _axis(payload.get("primary"))
    week_pct, week_resets = _axis(payload.get("secondary"))
    if five_pct is None and week_pct is None:
        return None

    pace = payload.get("pace")
    week_pace = pace.get("secondary") if isinstance(pace, dict) else None
    will_last = week_pace.get("willLastToReset") if isinstance(week_pace, dict) else None
    will_last = will_last if isinstance(will_last, bool) else None

    return {
        "fetched_at": now,
        "five_hour": {"pct": five_pct, "resets_at": five_resets},
        "weekly": {"pct": week_pct, "resets_at": week_resets, "will_last_to_reset": will_last},
    }

def _codexbar_reset_passed(snapshot: dict, now: float) -> bool:
    for key in ("five_hour", "weekly"):
        ra = snapshot.get(key, {}).get("resets_at")
        if isinstance(ra, (int, float)) and ra < now:
            return True
    return False

def _get_codexbar_stats() -> Optional[dict]:
    """Return a fresh-or-stale-within-window codexbar snapshot, or None to fall back.

    No backoff state beyond the cache TTL: a refused loopback connection
    fails near-instantly, so there's nothing to protect against by adding
    one, unlike the 429 cooldown on the OAuth path.
    """
    now = time.time()
    snapshot = _cache_load(_CODEXBAR_SNAPSHOT)
    snap_age = float("inf")
    if snapshot and isinstance(snapshot.get("fetched_at"), (int, float)):
        snap_age = now - snapshot["fetched_at"]
    reset_passed = bool(snapshot) and _codexbar_reset_passed(snapshot, now)

    if snap_age < _CODEXBAR_CACHE_TTL and not reset_passed:
        return snapshot

    body = _fetch_codexbar_usage()
    if body is not None:
        new_snap = _normalize_codexbar_response(body, now)
        if new_snap:
            _cache_save(_CODEXBAR_SNAPSHOT, new_snap)
            _dlog("codexbar: fetched ok")
            return new_snap
        _dlog("codexbar: response normalized to empty, falling back")
    else:
        _dlog("codexbar: unreachable")

    if snapshot and snap_age < _CODEXBAR_STALE_WINDOW and not reset_passed:
        return snapshot
    return None

# ── Tier limits ───────────────────────────────────────────────────────────────

_EMBEDDED_LIMITS: dict[str, dict] = {
    "free": {
        "5h_cycle": {"min": 10, "max": 40},
        "weekly_sonnet": {"min": 40, "max": 80},
    },
    "pro": {
        "5h_cycle": {"min": 10, "max": 40},
        "weekly_sonnet": {"min": 40, "max": 80},
    },
    "max_5x": {
        "5h_cycle": {"min": 50, "max": 200},
        "weekly_sonnet": {"min": 140, "max": 280},
        "weekly_opus": {"min": 15, "max": 35},
    },
    "max_20x": {
        "5h_cycle": {"min": 200, "max": 800},
        "weekly_sonnet": {"min": 240, "max": 480},
        "weekly_opus": {"min": 24, "max": 40},
    },
    "team_standard": {
        "5h_cycle": {"min": 13, "max": 50},
        "weekly_sonnet": {"min": 50, "max": 100},
    },
    "team_premium": {
        "5h_cycle": {"min": 63, "max": 250},
        "weekly_sonnet": {"min": 250, "max": 500},
        "weekly_opus": {"min": 19, "max": 44},
    },
}

def _tier_limits() -> dict:
    tier = os.environ.get("CC_PLAN_TIER", "pro").lower().strip()
    # Try loading from config/limits.json relative to plugin root or script dir
    script_dir = Path(__file__).parent
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", str(script_dir)))
    for base in (plugin_root, script_dir):
        p = base / "config" / "limits.json"
        if p.exists():
            try:
                return json.loads(p.read_text()).get(tier, _EMBEDDED_LIMITS["pro"])
            except Exception:
                break
    return _EMBEDDED_LIMITS.get(tier, _EMBEDDED_LIMITS["pro"])

# ── Segment builders ──────────────────────────────────────────────────────────

def _seg_vim(data: dict) -> Optional[Segment]:
    vim = data.get("vim")
    if not vim:
        return None
    mode = vim.get("mode", "NORMAL")
    letter = _VIM_LETTER.get(mode, mode[:1])
    color = _VIM_COLOR.get(mode, "blue")
    return Segment(f"{ICON_VIM} {letter}", color)

def _seg_cwd(data: dict) -> Segment:
    path = (data.get("workspace") or {}).get("current_dir") or os.getcwd()
    return Segment(f"{ICON_FOLDER} {_fmt_cwd(path)}", "blue")

def _seg_git(data: dict) -> Optional[Segment]:
    cwd = (data.get("workspace") or {}).get("current_dir") or os.getcwd()
    info = _git_info(cwd)
    if not info:
        return None
    text = info["branch"]
    if info["ahead"]:
        text += f" {ICON_AHEAD}{info['ahead']}"
    if info["behind"]:
        text += f" {ICON_BEHIND}{info['behind']}"
    if info["dirty"]:
        text += f" {ICON_DIRTY}"
    bg_color = "peach" if info["dirty"] else "mauve"
    return Segment(f"{ICON_BRANCH} {text}", bg_color)

def _seg_metrics(data: dict) -> Segment:
    cost = data.get("cost") or {}
    dur_ms = int(cost.get("total_duration_ms") or 0)
    added = cost.get("total_lines_added") or 0
    removed = cost.get("total_lines_removed") or 0
    dur_str = _fmt_duration(dur_ms / 1000)
    return Segment(
        f"{ICON_TIMER} {dur_str}  {ICON_ADD} {added}  {ICON_DEL} {removed}",
        "teal",
    )

def _seg_model(data: dict) -> Segment:
    name = (data.get("model") or {}).get("display_name") or "—"
    return Segment(f"{ICON_MODEL} {name}", "sapphire")

def _seg_context(data: dict) -> Segment:
    ctx = data.get("context_window") or {}
    pct = ctx.get("used_percentage")
    if pct is None:
        return Segment(f"{ICON_BRAIN} —", "surface2")
    pct_i = int(pct)
    total_in = ctx.get("total_input_tokens") or 0
    size = ctx.get("context_window_size") or 200_000
    total_k = round(total_in / 1000)
    size_k = round(size / 1000)
    return Segment(
        f"{ICON_BRAIN} {pct_i}% ({total_k}k/{size_k}k)",
        _pct_color(pct_i),
    )

def _seg_block5h(stats: Optional[dict], tier: dict) -> Segment:
    if not stats:
        return Segment(f"{ICON_BLOCK} —", "surface2")
    prompts = stats.get("prompt_5h", 0)
    limit = (tier.get("5h_cycle") or {}).get("min") or 10
    pct = min(100, round(prompts / limit * 100))
    remain = stats.get("time_remaining_5h", 0)
    return Segment(
        f"{ICON_BLOCK} {pct}% ({_fmt_duration(remain)})",
        _pct_color(pct),
    )

def _seg_weekly(stats: Optional[dict], tier: dict) -> Segment:
    if not stats:
        return Segment(f"{ICON_WEEKLY} —", "surface2")
    s_h = stats.get("sonnet_hours", 0.0)
    o_h = stats.get("opus_hours", 0.0)
    remain = stats.get("weekly_reset_remaining", 0)
    s_lim = (tier.get("weekly_sonnet") or {}).get("min") or 40
    o_cfg = tier.get("weekly_opus")
    s_pct = min(100, round(s_h / s_lim * 100))
    text = f"{ICON_WEEKLY} {s_pct}% S"
    if o_cfg:
        o_lim = o_cfg.get("min") or 1
        o_pct = min(100, round(o_h / o_lim * 100))
        text += f"  ⚡ {o_pct}% O"
    text += f" ({_fmt_duration(remain)})"
    return Segment(text, _pct_color(s_pct))

def _oauth_axis_pct(snapshot: dict, key: str) -> Optional[int]:
    """Return integer % for a given axis, or None if absent.

    Anthropic's `utilization` field is a 0..100 percentage in observed responses;
    a value <= 1.0 is treated as a 0..1 fraction defensively.
    """
    ax = snapshot.get("axes", {}).get(key)
    if not ax:
        return None
    util = ax.get("utilization")
    if not isinstance(util, (int, float)):
        return None
    if util <= 1.0:
        util *= 100
    return min(100, max(0, round(util)))

def _seg_block5h_oauth(snapshot: dict) -> Segment:
    pct = _oauth_axis_pct(snapshot, "five_hour")
    if pct is None:
        return Segment(f"{ICON_BLOCK_LIVE} —", "surface2")
    resets = snapshot.get("axes", {}).get("five_hour", {}).get("resets_at")
    remain = max(0.0, resets - time.time()) if isinstance(resets, (int, float)) else 0
    return Segment(
        f"{ICON_BLOCK_LIVE} {pct}% ({_fmt_duration(remain)})",
        _pct_color(pct),
    )

def _seg_weekly_oauth(snapshot: dict) -> Segment:
    s_pct = _oauth_axis_pct(snapshot, "seven_day_sonnet")
    a_pct = _oauth_axis_pct(snapshot, "seven_day")
    if s_pct is None and a_pct is None:
        return Segment(f"{ICON_WEEKLY} —", "surface2")

    # Anchor "time remaining" to seven_day (all-model), falling back to sonnet.
    axes = snapshot.get("axes", {})
    resets = axes.get("seven_day", {}).get("resets_at") or axes.get(
        "seven_day_sonnet", {}
    ).get("resets_at")
    remain = max(0.0, resets - time.time()) if isinstance(resets, (int, float)) else 0

    parts = []
    if s_pct is not None:
        parts.append(f"{s_pct}% S")
    if a_pct is not None:
        parts.append(f"{a_pct}% A")
    text = f"{ICON_WEEKLY} {'  '.join(parts)} ({_fmt_duration(remain)})"

    worst = max(p for p in (s_pct, a_pct) if p is not None)
    return Segment(text, _pct_color(worst))

def _seg_block5h_codexbar(snapshot: dict) -> Segment:
    ax = snapshot.get("five_hour") or {}
    pct = ax.get("pct")
    if pct is None:
        return Segment(f"{ICON_BLOCK_LIVE} —", "surface2")
    resets = ax.get("resets_at")
    remain = max(0.0, resets - time.time()) if isinstance(resets, (int, float)) else 0
    return Segment(
        f"{ICON_BLOCK_LIVE} {pct}% ({_fmt_duration(remain)})",
        _pct_color(pct),
    )

def _seg_weekly_codexbar(snapshot: dict) -> Segment:
    """Weekly segment for the codexbar path. Only source that carries Pace:
    an icon driven by willLastToReset, appended after the percentage — never
    the undocumented `stage` string (see ADR-0005)."""
    ax = snapshot.get("weekly") or {}
    pct = ax.get("pct")
    if pct is None:
        return Segment(f"{ICON_WEEKLY} —", "surface2")
    resets = ax.get("resets_at")
    remain = max(0.0, resets - time.time()) if isinstance(resets, (int, float)) else 0

    will_last = ax.get("will_last_to_reset")
    pace_icon = ""
    if will_last is True:
        pace_icon = f" {ICON_PACE_OK}"
    elif will_last is False:
        pace_icon = f" {ICON_PACE_BEHIND}"

    text = f"{ICON_WEEKLY} {pct}%{pace_icon} ({_fmt_duration(remain)})"
    return Segment(text, _pct_color(pct))

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    data: dict = {}
    try:
        raw = sys.stdin.buffer.read()
        if raw.strip():
            data = json.loads(raw)
    except Exception:
        pass

    # Line 1: vim (optional) → cwd → git (optional) → metrics
    segs1: list[Segment] = []
    vim = _seg_vim(data)
    if vim:
        segs1.append(vim)
    segs1.append(_seg_cwd(data))
    git = _seg_git(data)
    if git:
        segs1.append(git)
    segs1.append(_seg_metrics(data))

    # Line 2: model → context
    segs2: list[Segment] = [_seg_model(data), _seg_context(data)]

    # Line 3: prefer the codexbar daemon (adds Pace), then Anthropic-authoritative
    # OAuth, else fall back to the JSONL-derived model-hour heuristic.
    codexbar_snap = _get_codexbar_stats()
    oauth_snap = None if codexbar_snap else _get_oauth_stats()
    if codexbar_snap:
        segs3: list[Segment] = [
            _seg_block5h_codexbar(codexbar_snap),
            _seg_weekly_codexbar(codexbar_snap),
        ]
    elif oauth_snap:
        segs3 = [
            _seg_block5h_oauth(oauth_snap),
            _seg_weekly_oauth(oauth_snap),
        ]
    else:
        tier = _tier_limits()
        stats = _get_usage_stats()
        segs3 = [_seg_block5h(stats, tier), _seg_weekly(stats, tier)]

    print(render_line(segs1))
    print(render_line(segs2))
    print(render_line(segs3))

if __name__ == "__main__":
    main()
