"""Tests for the shared building blocks in ``pytoolbox.core``."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone

import click
import pytest

from pytoolbox.core import console, fs, intervals, paths, tables

# ── intervals ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 y", intervals.IntervalDelta(years=1)),
        ("2 mon", intervals.IntervalDelta(months=2)),
        ("3 days", intervals.IntervalDelta(days=3)),
        ("1 week", intervals.IntervalDelta(days=7)),
        ("-3.5 hours", intervals.IntervalDelta(seconds=-12600)),
        ("90 minutes", intervals.IntervalDelta(seconds=5400)),
        ("04:30", intervals.IntervalDelta(seconds=16200)),
        ("2 days 04:30", intervals.IntervalDelta(days=2, seconds=16200)),
        ("1y2mon10d", intervals.IntervalDelta(years=1, months=2, days=10)),
    ],
)
def test_parse_pg_interval(text, expected):
    assert intervals.parse_pg_interval(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "banana", "3 fortnights", "1.5 years"])
def test_parse_pg_interval_rejects_bad_input(text):
    with pytest.raises(click.ClickException):
        intervals.parse_pg_interval(text)


def test_shift_months_clamps_day():
    # Jan 31 + 1 month has to land on the last day of February, not overflow.
    assert intervals.shift_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert intervals.shift_months(datetime(2024, 1, 31), 1) == datetime(2024, 2, 29)


def test_shift_months_crosses_year_boundaries():
    assert intervals.shift_months(datetime(2026, 11, 15), 3) == datetime(2027, 2, 15)
    assert intervals.shift_months(datetime(2026, 2, 15), -3) == datetime(2025, 11, 15)


def test_apply_interval_direction():
    base = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    delta = intervals.parse_pg_interval("1 mon 2 days 3 hours")
    assert intervals.apply_interval(base, delta) == datetime(2026, 4, 17, 15, 0, tzinfo=timezone.utc)
    assert intervals.apply_interval(base, delta, direction=-1) == datetime(
        2026, 2, 13, 9, 0, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (59, "59s"), (60, "1m"), (3661, "1h 1m 1s"), (90000, "1d 1h"), (-120, "-2m")],
)
def test_format_duration(seconds, expected):
    assert intervals.format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(3.0, "3"), (2.5, "2.5"), (0.000045, "0.000045"), (-1.0, "-1")]
)
def test_format_total_value(value, expected):
    assert intervals.format_total_value(value) == expected


def test_format_hours_minutes():
    assert intervals.format_hours_minutes(2.5) == "02:30"
    assert intervals.format_hours_minutes(-1.25) == "-01:15"


def test_gregorian_days_in_month():
    assert intervals.gregorian_days_in_month(2026, 2) == 28
    assert intervals.gregorian_days_in_month(2024, 2) == 29
    assert intervals.gregorian_days_in_month(2026, 12) == 31


# ── fs ──────────────────────────────────────────────────────────────

def test_human_bytes():
    assert fs.human_bytes(512) == "512 B"
    assert fs.human_bytes(2048) == "2.0 KB"
    assert fs.human_bytes(5 * 1024 ** 3) == "5.0 GB"


def test_normalize_extensions():
    assert fs.normalize_extensions(["py", ".TXT,md", "  "]) == {".py", ".txt", ".md"}


def test_unique_path(tmp_path):
    target = tmp_path / "file.txt"
    assert fs.unique_path(target) == target
    target.write_text("x", encoding="utf-8")
    assert fs.unique_path(target).name == "file(1).txt"


def test_iter_files_depth(tree):
    top_level = list(fs.iter_files(tree, depth=0))
    assert top_level == []  # every file lives one level down
    everything = sorted(p.name for p in fs.iter_files(tree))
    assert everything == ["notes.md", "one.txt", "three.txt", "two.txt"]


def test_iter_files_filters(tree):
    only_md = [p.name for p in fs.iter_files(tree, extensions={".md"})]
    assert only_md == ["notes.md"]
    excluded = sorted(p.name for p in fs.iter_files(tree, exclude_dir=["*/b"]))
    assert excluded == ["one.txt", "two.txt"]


def test_iter_files_accepts_a_single_file(tree):
    target = tree / "a" / "one.txt"
    assert list(fs.iter_files(target)) == [target]


def test_iter_files_skips_hidden_by_default(tmp_path):
    (tmp_path / ".secret").write_text("x", encoding="utf-8")
    (tmp_path / "plain").write_text("x", encoding="utf-8")
    assert [p.name for p in fs.iter_files(tmp_path)] == ["plain"]
    assert sorted(p.name for p in fs.iter_files(tmp_path, include_hidden=True)) == [".secret", "plain"]


def test_is_probably_text(tmp_path):
    text_file = tmp_path / "a.txt"
    text_file.write_text("hello", encoding="utf-8")
    binary_file = tmp_path / "b.bin"
    binary_file.write_bytes(b"\x00\x01\x02")
    assert fs.is_probably_text(text_file)
    assert not fs.is_probably_text(binary_file)


def test_file_hash_matches_for_identical_content(tree):
    assert fs.file_hash(tree / "a" / "one.txt") == fs.file_hash(tree / "b" / "three.txt")
    assert fs.file_hash(tree / "a" / "one.txt") != fs.file_hash(tree / "a" / "two.txt")


def test_get_size_of_directory(tree):
    assert fs.get_size(tree) == len("alpha") + len("beta") + len("alpha") + len("# title")


# ── paths ───────────────────────────────────────────────────────────

def test_pytoolbox_home_overrides_every_directory(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "home"))
    assert paths.config_dir() == tmp_path / "home" / "config"
    assert paths.data_dir() == tmp_path / "home" / "data"
    assert paths.cache_dir() == tmp_path / "home" / "cache"
    assert paths.runtime_dir() == tmp_path / "home" / "run"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_write_private_file_is_owner_only(tmp_path):
    target = paths.write_private_file(tmp_path / "secrets" / "pass", "hunter2")
    assert target.read_text(encoding="utf-8") == "hunter2"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_private_file_overwrites_atomically(tmp_path):
    """A second write must not leave a truncated file if it were interrupted --
    it lands in a temp file and is swapped into place with os.replace."""
    target = tmp_path / "pass"
    paths.write_private_file(target, "first")
    paths.write_private_file(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    assert list(tmp_path.iterdir()) == [target]  # no leftover temp file


def test_write_private_file_leaves_the_previous_content_on_failure(tmp_path, monkeypatch):
    """A crash mid-write must not truncate the file: the old content (or
    nothing, on a first write) survives, and the failed temp file is cleaned up."""
    target = tmp_path / "pass"
    paths.write_private_file(target, "first")

    class Boom(Exception):
        pass

    def fail_replace(src, dst):
        raise Boom("disk full")

    monkeypatch.setattr(paths.os, "replace", fail_replace)
    with pytest.raises(Boom):
        paths.write_private_file(target, "second")
    assert target.read_text(encoding="utf-8") == "first"
    assert list(tmp_path.iterdir()) == [target]  # no leftover temp file


def test_find_font_returns_none_for_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "font_dirs", lambda: [tmp_path])
    assert paths.find_font("DefinitelyNotAFont.ttf") is None


def test_find_font_searches_one_level_deep(monkeypatch, tmp_path):
    family = tmp_path / "family"
    family.mkdir()
    (family / "Some.ttf").write_bytes(b"")
    monkeypatch.setattr(paths, "font_dirs", lambda: [tmp_path])
    assert paths.find_font("Some.ttf") == family / "Some.ttf"


# ── tables ──────────────────────────────────────────────────────────

ROWS = [{"a": 1, "b": "x"}, {"a": 22, "b": "yy"}]
HEADERS = ["a", "b"]


def test_render_table_aligns_columns():
    lines = tables.render_table(ROWS, HEADERS).splitlines()
    assert lines[0] == "a  | b "
    assert lines[1] == "---+---"
    assert lines[2] == "1  | x "


def test_render_table_of_nothing_is_empty():
    assert tables.render_table([], HEADERS) == ""


def test_render_markdown():
    assert tables.render_markdown(ROWS, HEADERS).splitlines()[0] == "| a | b |"


def test_render_json_roundtrips():
    import json

    assert json.loads(tables.render_json(ROWS, HEADERS)) == [
        {"a": 1, "b": "x"},
        {"a": 22, "b": "yy"},
    ]


def test_emit_excel_without_output_is_rejected():
    with pytest.raises(click.ClickException):
        tables.emit(ROWS, HEADERS, "excel")


def test_emit_writes_csv(tmp_path):
    target = tmp_path / "out.csv"
    tables.emit(ROWS, HEADERS, "csv", target)
    assert target.read_text(encoding="utf-8").splitlines()[0] == "a,b"


# ── console ─────────────────────────────────────────────────────────

def test_plural():
    assert console.plural(1, "file") == "1 file"
    assert console.plural(2, "file") == "2 files"
    assert console.plural(2, "entry", "entries") == "2 entries"


def test_confirm_short_circuits_with_assume_yes():
    assert console.confirm("really?", assume_yes=True) is True


def test_color_disabled_by_no_color(monkeypatch):
    monkeypatch.setenv(console.NO_COLOR_ENV, "1")
    assert console.style("x", fg="red") == "x"
