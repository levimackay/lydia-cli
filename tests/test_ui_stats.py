"""Tests for format_stats, the dim one-liner shown after a chat reply."""

from lydia.cli.ui import format_stats


def test_format_stats_returns_none_with_no_data() -> None:
    assert format_stats({}) is None


def test_format_stats_normal_reply() -> None:
    line = format_stats({"eval_count": 412, "total_duration": 9_300_000_000, "done_reason": "stop"})
    assert line == "412 tokens · 9.3s · 44 tok/s"


def test_format_stats_notes_truncation_by_token_limit() -> None:
    line = format_stats({"eval_count": 412, "total_duration": 9_300_000_000, "done_reason": "length"})
    assert line == "412 tokens · 9.3s · 44 tok/s · cut off by token limit"


def test_format_stats_notes_truncation_even_without_perf_counters() -> None:
    # A provider that reports done_reason but not eval_count/total_duration
    # should still surface the truncation, not silently say nothing.
    assert format_stats({"done_reason": "length"}) == "reply cut off by the token limit"
