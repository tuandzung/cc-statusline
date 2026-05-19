"""Tests for Powerline rendering logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from statusline import Segment, render_line, _CHEV, _RESET, _P


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\x1b\[[^m]*m", "", s)


def test_empty_line_returns_empty():
    assert render_line([]) == ""


def test_single_segment_contains_content():
    seg = Segment("hello world", "blue")
    result = render_line([seg])
    assert "hello world" in _strip_ansi(result)


def test_single_segment_ends_with_chevron_and_reset():
    seg = Segment("X", "blue")
    result = render_line([seg])
    assert _CHEV in result
    assert result.endswith(_RESET)


def test_two_segments_one_chevron_between():
    s1 = Segment("A", "blue")
    s2 = Segment("B", "mauve")
    result = render_line([s1, s2])
    plain = _strip_ansi(result)
    # Both labels present
    assert "A" in plain
    assert "B" in plain
    # Two chevrons: one between segments, one at the end
    assert result.count(_CHEV) == 2


def test_three_segments_two_inter_chevrons():
    segs = [Segment("A", "blue"), Segment("B", "mauve"), Segment("C", "teal")]
    result = render_line(segs)
    assert result.count(_CHEV) == 3


def test_bg_colour_appears_in_output():
    r, g, b = _P["sapphire"]
    seg = Segment("model", "sapphire")
    result = render_line([seg])
    assert f"48;2;{r};{g};{b}" in result


def test_fg_colour_defaults_to_crust():
    r, g, b = _P["crust"]
    seg = Segment("X", "blue")  # default fg = crust
    result = render_line([seg])
    assert f"38;2;{r};{g};{b}" in result


def test_final_chevron_uses_last_segment_bg_as_fg():
    """The final chevron is painted in the last segment's bg colour as fg."""
    seg = Segment("last", "mauve")
    result = render_line([seg])
    r, g, b = _P["mauve"]
    # The mauve fg escape and the final chevron must both appear in the output
    mauve_fg = f"38;2;{r};{g};{b}"
    assert mauve_fg in result
    # The mauve fg code must appear before the last chevron
    idx_fg   = result.rfind(mauve_fg)
    idx_chev = result.rfind(_CHEV)
    assert idx_fg < idx_chev
