"""Hermetic tests for the OAuth quota path.

Covers credentials parsing, response normalisation, the fetch state machine
(fresh / stale / over-stale / 429-cooldown / token-expired / reset-passed),
and the segment builders. No live network; ``urllib.request.urlopen`` is
monkey-patched throughout.
"""

import json
import sys
import time
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import statusline  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _iso(ts: float) -> str:
    """Return an ISO-8601 / Z-suffixed string for a unix timestamp."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usage_body(
    five_hour: Optional[float] = 45.2,
    seven_day: Optional[float] = 12.8,
    seven_day_sonnet: Optional[float] = 5.1,
    extra_usage_enabled: bool = False,
    five_hour_reset_in_s: float = 3 * 3600,
    seven_day_reset_in_s: float = 5 * 24 * 3600,
    now: Optional[float] = None,
) -> dict:
    """Shape-match the onWatch fixture for /api/oauth/usage."""
    now = now if now is not None else time.time()
    body: dict = {}
    if five_hour is not None:
        body["five_hour"] = {
            "utilization": five_hour,
            "resets_at":   _iso(now + five_hour_reset_in_s),
            "is_enabled":  True,
        }
    if seven_day is not None:
        body["seven_day"] = {
            "utilization": seven_day,
            "resets_at":   _iso(now + seven_day_reset_in_s),
            "is_enabled":  True,
        }
    if seven_day_sonnet is not None:
        body["seven_day_sonnet"] = {
            "utilization": seven_day_sonnet,
            "resets_at":   _iso(now + seven_day_reset_in_s),
            "is_enabled":  True,
        }
    body["extra_usage"] = {
        "utilization": 0,
        "resets_at":   None,
        "is_enabled":  extra_usage_enabled,
    }
    return body


@pytest.fixture
def fake_cache(tmp_path, monkeypatch):
    """Redirect cache + creds + debug-log paths into a tmp dir."""
    cache  = tmp_path / "cache"
    creds  = tmp_path / "credentials.json"
    debug  = tmp_path / "debug.log"
    monkeypatch.setattr(statusline, "_CACHE_DIR",       cache)
    monkeypatch.setattr(statusline, "_OAUTH_SNAPSHOT",  cache / "oauth_snapshot.json")
    monkeypatch.setattr(statusline, "_OAUTH_STATE",     cache / "oauth_state.json")
    monkeypatch.setattr(statusline, "_OAUTH_CREDS_PATH", creds)
    monkeypatch.setattr(statusline, "_DEBUG_LOG",       debug)
    return {"cache": cache, "creds": creds, "debug": debug}


def _write_creds(path: Path, access: str = "tok_abc", expires_at_ms: Optional[int] = None) -> None:
    if expires_at_ms is None:
        expires_at_ms = int((time.time() + 3600) * 1000)
    path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken":  access,
            "refreshToken": "rt_xyz",
            "expiresAt":    expires_at_ms,
            "scopes":       ["user:inference"],
            "subscriptionType": "max_20x",
        }
    }))


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
        # Assert headers are right
        assert req.headers["Authorization"].startswith("Bearer ")
        assert req.headers["Anthropic-beta"] == "oauth-2025-04-20"
        assert req.headers["User-agent"].startswith("cc-statusline/")
        return _FakeHTTPResponse(payload)
    return _opener


def _mock_urlopen_http_error(code: int):
    def _opener(req, timeout=None):
        raise urllib.error.HTTPError(
            url="https://x", code=code, msg="err", hdrs=None, fp=None  # type: ignore[arg-type]
        )
    return _opener


def _mock_urlopen_network_error():
    def _opener(req, timeout=None):
        raise OSError("connection refused")
    return _opener


# ── Credentials parser ───────────────────────────────────────────────────────


class TestReadOAuthCredentials:
    def test_returns_none_when_file_missing(self, fake_cache):
        assert statusline._read_oauth_credentials() is None

    def test_parses_well_formed_creds(self, fake_cache):
        _write_creds(fake_cache["creds"], access="tok_42")
        creds = statusline._read_oauth_credentials()
        assert creds is not None
        assert creds["access"] == "tok_42"
        assert creds["expires_at"] is not None
        assert creds["expires_at"] > time.time()

    def test_missing_access_token_returns_none(self, fake_cache):
        fake_cache["creds"].write_text(json.dumps({"claudeAiOauth": {"refreshToken": "x"}}))
        assert statusline._read_oauth_credentials() is None

    def test_bad_json_returns_none(self, fake_cache):
        fake_cache["creds"].write_text("not-json")
        assert statusline._read_oauth_credentials() is None

    def test_missing_oauth_section_returns_none(self, fake_cache):
        fake_cache["creds"].write_text(json.dumps({"other": {}}))
        assert statusline._read_oauth_credentials() is None

    def test_omitted_expires_at_returns_none_expiry(self, fake_cache):
        fake_cache["creds"].write_text(json.dumps({
            "claudeAiOauth": {"accessToken": "tok"}
        }))
        creds = statusline._read_oauth_credentials()
        assert creds is not None
        assert creds["expires_at"] is None


# ── Response normalisation ───────────────────────────────────────────────────


class TestNormalizeOAuthResponse:
    def test_picks_known_axes(self):
        body = _usage_body()
        snap = statusline._normalize_oauth_response(body, now=1_000_000.0)
        assert snap is not None
        assert set(snap["axes"]) == {"five_hour", "seven_day", "seven_day_sonnet"}
        assert snap["fetched_at"] == 1_000_000.0

    def test_drops_disabled_axes(self):
        body = _usage_body(extra_usage_enabled=False)
        snap = statusline._normalize_oauth_response(body, now=time.time())
        assert "extra_usage" not in (snap or {}).get("axes", {})

    def test_drops_null_utilization(self):
        body = _usage_body()
        body["five_hour"]["utilization"] = None
        snap = statusline._normalize_oauth_response(body, now=time.time())
        assert snap is not None
        assert "five_hour" not in snap["axes"]

    def test_filters_unknown_keys(self):
        body = _usage_body()
        body["seven_day_omelette"] = {
            "utilization": 99.0,
            "resets_at":   _iso(time.time() + 3600),
            "is_enabled":  True,
        }
        snap = statusline._normalize_oauth_response(body, now=time.time())
        assert snap is not None
        assert "seven_day_omelette" not in snap["axes"]

    def test_empty_axes_returns_none(self):
        snap = statusline._normalize_oauth_response({"extra_usage": {"is_enabled": False}}, now=0)
        assert snap is None

    def test_parses_resets_at_to_unix(self):
        now = time.time()
        body = _usage_body(now=now, five_hour_reset_in_s=1800)
        snap = statusline._normalize_oauth_response(body, now=now)
        assert snap is not None
        resets = snap["axes"]["five_hour"]["resets_at"]
        assert resets is not None
        assert abs(resets - (now + 1800)) < 5


# ── Fetcher status mapping ───────────────────────────────────────────────────


class TestFetchOAuthUsage:
    def test_ok(self, monkeypatch):
        body = _usage_body()
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_ok(body))
        status, parsed = statusline._fetch_oauth_usage("tok")
        assert status == "ok"
        assert parsed == body

    def test_429_maps_to_rate_limited(self, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_http_error(429))
        status, parsed = statusline._fetch_oauth_usage("tok")
        assert status == "rate_limited"
        assert parsed is None

    def test_401_maps_to_auth_error(self, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_http_error(401))
        assert statusline._fetch_oauth_usage("tok")[0] == "auth_error"

    def test_403_maps_to_auth_error(self, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_http_error(403))
        assert statusline._fetch_oauth_usage("tok")[0] == "auth_error"

    def test_500_maps_to_server_error(self, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_http_error(500))
        assert statusline._fetch_oauth_usage("tok")[0] == "server_error"

    def test_network_error_maps_to_network_error(self, monkeypatch):
        monkeypatch.setattr(statusline.urllib.request, "urlopen", _mock_urlopen_network_error())
        assert statusline._fetch_oauth_usage("tok")[0] == "network_error"


# ── State machine ────────────────────────────────────────────────────────────


class TestGetOAuthStats:
    def test_no_creds_returns_none(self, fake_cache):
        assert statusline._get_oauth_stats() is None

    def test_fresh_fetch_caches_snapshot(self, fake_cache, monkeypatch):
        _write_creds(fake_cache["creds"])
        monkeypatch.setattr(statusline.urllib.request, "urlopen",
                            _mock_urlopen_ok(_usage_body()))
        snap = statusline._get_oauth_stats()
        assert snap is not None
        assert "five_hour" in snap["axes"]
        # Cache files written
        assert (fake_cache["cache"] / "oauth_snapshot.json").exists()

    def test_reuses_cache_within_ttl(self, fake_cache, monkeypatch):
        _write_creds(fake_cache["creds"])
        calls = {"n": 0}

        def opener(req, timeout=None):
            calls["n"] += 1
            return _FakeHTTPResponse(json.dumps(_usage_body()).encode())

        monkeypatch.setattr(statusline.urllib.request, "urlopen", opener)
        statusline._get_oauth_stats()
        statusline._get_oauth_stats()
        assert calls["n"] == 1  # second call served from cache

    def test_refetches_after_ttl(self, fake_cache, monkeypatch):
        _write_creds(fake_cache["creds"])
        # Plant a stale snapshot
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        stale_fetched = time.time() - (statusline._OAUTH_CACHE_TTL + 60)
        (fake_cache["cache"] / "oauth_snapshot.json").write_text(json.dumps({
            "fetched_at": stale_fetched,
            "axes": {"five_hour": {"utilization": 10.0, "resets_at": time.time() + 3600}},
        }))
        monkeypatch.setattr(statusline.urllib.request, "urlopen",
                            _mock_urlopen_ok(_usage_body(five_hour=77.7)))
        snap = statusline._get_oauth_stats()
        assert snap is not None
        assert abs(snap["axes"]["five_hour"]["utilization"] - 77.7) < 0.01

    def test_429_marks_cooldown_then_returns_stale_snapshot(self, fake_cache, monkeypatch):
        _write_creds(fake_cache["creds"])
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        stale_fetched = time.time() - (statusline._OAUTH_CACHE_TTL + 60)
        (fake_cache["cache"] / "oauth_snapshot.json").write_text(json.dumps({
            "fetched_at": stale_fetched,
            "axes": {"five_hour": {"utilization": 30.0, "resets_at": time.time() + 3600}},
        }))
        monkeypatch.setattr(statusline.urllib.request, "urlopen",
                            _mock_urlopen_http_error(429))
        snap = statusline._get_oauth_stats()
        # snapshot is older than TTL but younger than 30 min — return it
        assert snap is not None
        # cooldown persisted
        state = json.loads((fake_cache["cache"] / "oauth_state.json").read_text())
        assert state["blocked_until"] > time.time()

    def test_over_stale_snapshot_falls_back(self, fake_cache, monkeypatch):
        _write_creds(fake_cache["creds"])
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        very_old = time.time() - (statusline._OAUTH_STALE_WINDOW + 60)
        (fake_cache["cache"] / "oauth_snapshot.json").write_text(json.dumps({
            "fetched_at": very_old,
            "axes": {"five_hour": {"utilization": 30.0, "resets_at": time.time() + 3600}},
        }))
        monkeypatch.setattr(statusline.urllib.request, "urlopen",
                            _mock_urlopen_http_error(429))
        assert statusline._get_oauth_stats() is None

    def test_expired_token_skips_fetch_but_returns_fresh_snapshot(self, fake_cache, monkeypatch):
        # Token already expired
        _write_creds(fake_cache["creds"], expires_at_ms=int((time.time() - 600) * 1000))
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        (fake_cache["cache"] / "oauth_snapshot.json").write_text(json.dumps({
            "fetched_at": time.time() - 60,
            "axes": {"five_hour": {"utilization": 42.0, "resets_at": time.time() + 3600}},
        }))
        opened = {"n": 0}

        def opener(req, timeout=None):
            opened["n"] += 1
            return _FakeHTTPResponse(json.dumps(_usage_body()).encode())

        monkeypatch.setattr(statusline.urllib.request, "urlopen", opener)
        snap = statusline._get_oauth_stats()
        assert snap is not None
        assert opened["n"] == 0  # never tried the network

    def test_reset_passed_forces_refetch_through_cooldown(self, fake_cache, monkeypatch):
        _write_creds(fake_cache["creds"])
        fake_cache["cache"].mkdir(parents=True, exist_ok=True)
        # Snapshot is recent but five_hour reset is in the past
        (fake_cache["cache"] / "oauth_snapshot.json").write_text(json.dumps({
            "fetched_at": time.time() - 30,
            "axes": {"five_hour": {"utilization": 95.0, "resets_at": time.time() - 5}},
        }))
        # cooldown active
        (fake_cache["cache"] / "oauth_state.json").write_text(
            json.dumps({"blocked_until": time.time() + 600})
        )
        called = {"n": 0}

        def opener(req, timeout=None):
            called["n"] += 1
            return _FakeHTTPResponse(json.dumps(_usage_body(five_hour=3.0)).encode())

        monkeypatch.setattr(statusline.urllib.request, "urlopen", opener)
        snap = statusline._get_oauth_stats()
        assert called["n"] == 1
        assert snap is not None
        assert snap["axes"]["five_hour"]["utilization"] < 10


# ── Segment builders ─────────────────────────────────────────────────────────


class TestOAuthAxisPct:
    def test_percentage_scale(self):
        snap = {"axes": {"five_hour": {"utilization": 42.7}}}
        assert statusline._oauth_axis_pct(snap, "five_hour") == 43

    def test_fraction_scale_is_rescaled(self):
        snap = {"axes": {"five_hour": {"utilization": 0.42}}}
        assert statusline._oauth_axis_pct(snap, "five_hour") == 42

    def test_clamped_to_100(self):
        snap = {"axes": {"five_hour": {"utilization": 150.0}}}
        assert statusline._oauth_axis_pct(snap, "five_hour") == 100

    def test_missing_axis_returns_none(self):
        assert statusline._oauth_axis_pct({"axes": {}}, "five_hour") is None


class TestSegmentBuildersOAuth:
    def test_block5h_uses_live_icon(self):
        snap = {"axes": {"five_hour": {"utilization": 50.0, "resets_at": time.time() + 600}}}
        seg = statusline._seg_block5h_oauth(snap)
        assert statusline.ICON_BLOCK_LIVE in seg.content
        assert "50%" in seg.content

    def test_weekly_shows_S_and_A(self):
        snap = {"axes": {
            "seven_day":        {"utilization": 30.0, "resets_at": time.time() + 86400},
            "seven_day_sonnet": {"utilization": 15.0, "resets_at": time.time() + 86400},
        }}
        seg = statusline._seg_weekly_oauth(snap)
        assert "15% S" in seg.content
        assert "30% A" in seg.content

    def test_weekly_anchors_remain_on_seven_day(self):
        # seven_day resets in 3 days, sonnet in 1 day — should pick seven_day.
        now = time.time()
        snap = {"axes": {
            "seven_day":        {"utilization": 10.0, "resets_at": now + 3 * 86400},
            "seven_day_sonnet": {"utilization": 5.0,  "resets_at": now + 86400},
        }}
        seg = statusline._seg_weekly_oauth(snap)
        # _fmt_duration of (3d − ε) renders as "2d 23h"; sonnet would be "23h …".
        assert "2d" in seg.content
