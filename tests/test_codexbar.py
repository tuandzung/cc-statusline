"""Hermetic tests for the codexbar quota path.

Covers response normalisation (defensive against the undocumented third-party
schema), the fetch state machine (fresh / stale / over-stale / unreachable /
reset-passed), and the segment builders — including the willLastToReset →
Pace-icon mapping. No live network; ``urllib.request.urlopen`` is
monkey-patched throughout.

See docs/adr/0005-codexbar-as-preferred-quota-source.md for the design.
"""

import json
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import statusline  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usage_body(
    five_pct: Optional[float] = 28,
    week_pct: Optional[float] = 59,
    will_last: Optional[bool] = True,
    five_reset_in_s: float = 3 * 3600,
    week_reset_in_s: float = 5 * 24 * 3600,
    now: Optional[float] = None,
) -> dict:
    """Shape-match codexbar's /usage?provider=claude response."""
    now = now if now is not None else time.time()
    body: dict = {}
    if five_pct is not None:
        body["primary"] = {
            "usedPercent": five_pct,
            "windowMinutes": 300,
            "resetsAt": _iso(now + five_reset_in_s),
        }
    if week_pct is not None:
        body["secondary"] = {
            "usedPercent": week_pct,
            "windowMinutes": 10080,
            "resetsAt": _iso(now + week_reset_in_s),
        }
    if will_last is not None:
        body["pace"] = {"secondary": {"willLastToReset": will_last}}
    return body


@pytest.fixture
def fake_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    debug = tmp_path / "debug.log"
    monkeypatch.setattr(statusline, "_CACHE_DIR", cache)
    monkeypatch.setattr(statusline, "_CODEXBAR_SNAPSHOT", cache / "codexbar_snapshot.json")
    monkeypatch.setattr(statusline, "_DEBUG_LOG", debug)
    return {"cache": cache, "debug": debug}


class _FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._buf = BytesIO(payload)

    def read(self, *_args, **_kwargs) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _mock_urlopen_ok(body: dict):
    payload = json.dumps(body).encode("utf-8")

    def _opener(req, timeout=None):
        assert req.full_url == statusline._CODEXBAR_URL
        return _FakeHTTPResponse(payload)
    return _opener


def _mock_urlopen_unreachable():
    def _opener(req, timeout=None):
        raise OSError("connection refused")
    return _opener


# ── Fetcher ───────────────────────────────────────────────────────────────────


class TestFetchCodexbarUsage:
    def test_ok(self, monkeypatch):
        body = _usage_body()
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_ok(body))
        assert statusline._fetch_codexbar_usage() == body

    def test_unreachable_returns_none(self, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_unreachable())
        assert statusline._fetch_codexbar_usage() is None

    def test_non_dict_body_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            statusline.urllib.request, "urlopen", _mock_urlopen_raw(b"[1, 2, 3]")
        )
        assert statusline._fetch_codexbar_usage() is None

    def test_malformed_json_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            statusline.urllib.request, "urlopen", _mock_urlopen_raw(b"not-json")
        )
        assert statusline._fetch_codexbar_usage() is None

    def test_array_wrapped_body_unwraps_claude_provider(self, monkeypatch):
        """Live codexbar wraps the response in a list of per-provider objects."""
        wrapped = [{"provider": "claude", "usage": {"primary": {"usedPercent": 12}}}]
        monkeypatch.setattr(
            statusline.urllib.request,
            "urlopen",
            _mock_urlopen_raw(json.dumps(wrapped).encode()),
        )
        assert statusline._fetch_codexbar_usage() == wrapped[0]

    def test_array_with_no_matching_provider_returns_none(self, monkeypatch):
        wrapped = [{"provider": "codex", "usage": {}}]
        monkeypatch.setattr(
            statusline.urllib.request,
            "urlopen",
            _mock_urlopen_raw(json.dumps(wrapped).encode()),
        )
        assert statusline._fetch_codexbar_usage() is None


def _mock_urlopen_raw(payload: bytes):
    def _opener(req, timeout=None):
        return _FakeHTTPResponse(payload)
    return _opener


# ── Response normalisation ───────────────────────────────────────────────────


class TestNormalizeCodexbarResponse:
    def test_picks_both_axes_and_pace(self):
        body = _usage_body()
        snap = statusline._normalize_codexbar_response(body, now=1_000_000.0)
        assert snap is not None
        assert snap["five_hour"]["pct"] == 28
        assert snap["weekly"]["pct"] == 59
        assert snap["weekly"]["will_last_to_reset"] is True
        assert snap["fetched_at"] == 1_000_000.0

    def test_missing_primary_still_yields_weekly(self):
        body = _usage_body(five_pct=None)
        snap = statusline._normalize_codexbar_response(body, now=time.time())
        assert snap is not None
        assert snap["five_hour"]["pct"] is None
        assert snap["weekly"]["pct"] == 59

    def test_both_axes_missing_returns_none(self):
        snap = statusline._normalize_codexbar_response({}, now=time.time())
        assert snap is None

    def test_unwraps_usage_key(self):
        """Live codexbar nests primary/secondary under 'usage', pace stays top-level."""
        body = {
            "provider": "claude",
            "usage": {
                "primary": {"usedPercent": 12, "resetsAt": _iso(time.time() + 3600)},
                "secondary": {"usedPercent": 2, "resetsAt": _iso(time.time() + 86400)},
            },
            "pace": {"secondary": {"willLastToReset": True}},
        }
        snap = statusline._normalize_codexbar_response(body, now=time.time())
        assert snap is not None
        assert snap["five_hour"]["pct"] == 12
        assert snap["weekly"]["pct"] == 2
        assert snap["weekly"]["will_last_to_reset"] is True

    def test_unwraps_claude_provider_key(self):
        body = {"claude": _usage_body()}
        snap = statusline._normalize_codexbar_response(body, now=time.time())
        assert snap is not None
        assert snap["weekly"]["pct"] == 59

    def test_missing_pace_yields_none_will_last(self):
        body = _usage_body(will_last=None)
        snap = statusline._normalize_codexbar_response(body, now=time.time())
        assert snap is not None
        assert snap["weekly"]["will_last_to_reset"] is None

    def test_non_bool_will_last_to_reset_is_dropped(self):
        body = _usage_body()
        body["pace"]["secondary"]["willLastToReset"] = "yes"
        snap = statusline._normalize_codexbar_response(body, now=time.time())
        assert snap is not None
        assert snap["weekly"]["will_last_to_reset"] is None

    def test_out_of_range_percent_clamped(self):
        body = _usage_body(five_pct=150, week_pct=-10)
        snap = statusline._normalize_codexbar_response(body, now=time.time())
        assert snap is not None
        assert snap["five_hour"]["pct"] == 100
        assert snap["weekly"]["pct"] == 0

    def test_malformed_axis_type_treated_as_absent(self):
        body = {"primary": "not-a-dict", "secondary": _usage_body()["secondary"]}
        snap = statusline._normalize_codexbar_response(body, now=time.time())
        assert snap is not None
        assert snap["five_hour"]["pct"] is None
        assert snap["weekly"]["pct"] == 59

    def test_parses_resets_at_to_unix(self):
        now = time.time()
        body = _usage_body(now=now, week_reset_in_s=1800)
        snap = statusline._normalize_codexbar_response(body, now=now)
        assert snap is not None
        resets = snap["weekly"]["resets_at"]
        assert resets is not None
        assert abs(resets - (now + 1800)) < 5


# ── State machine ────────────────────────────────────────────────────────────


class TestGetCodexbarStats:
    def test_fresh_fetch_caches_snapshot(self, fake_cache, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_ok(_usage_body()))
        snap = statusline._get_codexbar_stats()
        assert snap is not None
        assert snap["weekly"]["pct"] == 59
        assert (fake_cache["cache"] / "codexbar_snapshot.json").exists()

    def test_reuses_cache_within_ttl(self, fake_cache, monkeypatch):
        calls = {"n": 0}

        def opener(req, timeout=None):
            calls["n"] += 1
            return _FakeHTTPResponse(json.dumps(_usage_body()).encode())

        monkeypatch.setattr(statusline.urllib.request, "urlopen", opener)
        statusline._get_codexbar_stats()
        statusline._get_codexbar_stats()
        assert calls["n"] == 1

    def test_refetches_after_ttl(self, fake_cache, monkeypatch):
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        stale_fetched = time.time() - (statusline._CODEXBAR_CACHE_TTL + 60)
        (fake_cache["cache"] / "codexbar_snapshot.json").write_text(json.dumps({
            "fetched_at": stale_fetched,
            "five_hour": {"pct": 10, "resets_at": time.time() + 3600},
            "weekly": {"pct": 20, "resets_at": time.time() + 86400, "will_last_to_reset": True},
        }))
        monkeypatch.setattr(
            statusline.urllib.request, "urlopen", _mock_urlopen_ok(_usage_body(week_pct=77))
        )
        snap = statusline._get_codexbar_stats()
        assert snap is not None
        assert snap["weekly"]["pct"] == 77

    def test_unreachable_falls_back_to_stale_within_window(self, fake_cache, monkeypatch):
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        stale_fetched = time.time() - (statusline._CODEXBAR_CACHE_TTL + 60)
        (fake_cache["cache"] / "codexbar_snapshot.json").write_text(json.dumps({
            "fetched_at": stale_fetched,
            "five_hour": {"pct": 10, "resets_at": time.time() + 3600},
            "weekly": {"pct": 20, "resets_at": time.time() + 86400, "will_last_to_reset": True},
        }))
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_unreachable())
        snap = statusline._get_codexbar_stats()
        assert snap is not None
        assert snap["weekly"]["pct"] == 20

    def test_unreachable_and_no_cache_returns_none(self, fake_cache, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_unreachable())
        assert statusline._get_codexbar_stats() is None

    def test_over_stale_and_unreachable_falls_back_to_none(self, fake_cache, monkeypatch):
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        very_old = time.time() - (statusline._CODEXBAR_STALE_WINDOW + 60)
        (fake_cache["cache"] / "codexbar_snapshot.json").write_text(json.dumps({
            "fetched_at": very_old,
            "five_hour": {"pct": 10, "resets_at": time.time() + 3600},
            "weekly": {"pct": 20, "resets_at": time.time() + 86400, "will_last_to_reset": True},
        }))
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_unreachable())
        assert statusline._get_codexbar_stats() is None

    def test_reset_passed_forces_refetch(self, fake_cache, monkeypatch):
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        (fake_cache["cache"] / "codexbar_snapshot.json").write_text(json.dumps({
            "fetched_at": time.time() - 30,
            "five_hour": {"pct": 95, "resets_at": time.time() - 5},
            "weekly": {"pct": 50, "resets_at": time.time() + 86400, "will_last_to_reset": True},
        }))
        called = {"n": 0}

        def opener(req, timeout=None):
            called["n"] += 1
            return _FakeHTTPResponse(json.dumps(_usage_body(five_pct=3)).encode())

        monkeypatch.setattr(statusline.urllib.request, "urlopen", opener)
        snap = statusline._get_codexbar_stats()
        assert called["n"] == 1
        assert snap is not None
        assert snap["five_hour"]["pct"] == 3


# ── Segment builders ─────────────────────────────────────────────────────────


class TestSegmentBuildersCodexbar:
    def test_block5h_uses_live_icon(self):
        snap = {"five_hour": {"pct": 50, "resets_at": time.time() + 600}}
        seg = statusline._seg_block5h_codexbar(snap)
        assert statusline.ICON_BLOCK_LIVE in seg.content
        assert "50%" in seg.content

    def test_block5h_missing_pct_renders_dash(self):
        seg = statusline._seg_block5h_codexbar({})
        assert "—" in seg.content

    def test_weekly_will_last_shows_ok_icon(self):
        snap = {"weekly": {"pct": 40, "resets_at": time.time() + 86400, "will_last_to_reset": True}}
        seg = statusline._seg_weekly_codexbar(snap)
        assert statusline.ICON_PACE_OK in seg.content
        assert statusline.ICON_PACE_BEHIND not in seg.content
        assert "40%" in seg.content

    def test_weekly_wont_last_shows_behind_icon(self):
        snap = {"weekly": {"pct": 90, "resets_at": time.time() + 86400, "will_last_to_reset": False}}
        seg = statusline._seg_weekly_codexbar(snap)
        assert statusline.ICON_PACE_BEHIND in seg.content
        assert statusline.ICON_PACE_OK not in seg.content

    def test_weekly_unknown_pace_shows_no_icon(self):
        snap = {"weekly": {"pct": 40, "resets_at": time.time() + 86400, "will_last_to_reset": None}}
        seg = statusline._seg_weekly_codexbar(snap)
        assert statusline.ICON_PACE_OK not in seg.content
        assert statusline.ICON_PACE_BEHIND not in seg.content

    def test_weekly_missing_pct_renders_dash(self):
        seg = statusline._seg_weekly_codexbar({})
        assert "—" in seg.content
