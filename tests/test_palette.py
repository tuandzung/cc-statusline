"""Tests for palette helpers and percentage colour thresholds."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from statusline import _fg, _bg, _pct_color, _P, _fmt_duration, _fmt_cwd


def test_pct_color_green_below_50():
    assert _pct_color(0)  == "green"
    assert _pct_color(49) == "green"


def test_pct_color_yellow_50_to_74():
    assert _pct_color(50) == "yellow"
    assert _pct_color(74) == "yellow"


def test_pct_color_peach_75_to_89():
    assert _pct_color(75) == "peach"
    assert _pct_color(89) == "peach"


def test_pct_color_red_at_90():
    assert _pct_color(90)  == "red"
    assert _pct_color(100) == "red"


def test_fg_produces_38_escape():
    r, g, b = _P["blue"]
    assert _fg("blue") == f"\x1b[38;2;{r};{g};{b}m"


def test_bg_produces_48_escape():
    r, g, b = _P["mauve"]
    assert _bg("mauve") == f"\x1b[48;2;{r};{g};{b}m"


def test_palette_has_required_colours():
    required = {
        "crust", "blue", "mauve", "teal", "sapphire", "green",
        "yellow", "peach", "red", "surface2", "surface0",
    }
    assert required.issubset(_P.keys())


class TestFmtDuration:
    def test_zero(self):
        assert _fmt_duration(0) == "0s"

    def test_seconds(self):
        assert _fmt_duration(45) == "45s"

    def test_minutes(self):
        assert _fmt_duration(90) == "1m 30s"

    def test_hours(self):
        assert _fmt_duration(3600) == "1h"

    def test_hours_and_minutes(self):
        assert _fmt_duration(5 * 3600 + 14 * 60) == "5h 14m"

    def test_days(self):
        assert _fmt_duration(4 * 86400 + 6 * 3600) == "4d 6h"


class TestFmtCwd:
    def test_short_path_unchanged(self):
        assert _fmt_cwd("/tmp/foo") == "/tmp/foo"

    def test_home_replaced_with_tilde(self):
        import os
        home = os.path.expanduser("~")
        result = _fmt_cwd(home + "/foo/bar")
        assert result.startswith("~")
        assert "foo/bar" in result

    def test_long_path_truncated(self):
        long_path = "/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v"
        result = _fmt_cwd(long_path)
        assert len(result) <= 50   # must be shorter than raw
        assert "…" in result

    def test_40_char_path_not_truncated(self):
        path = "/home/user/" + "x" * 28   # total = 40
        result = _fmt_cwd(path)
        assert "…" not in result
