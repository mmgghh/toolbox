"""Tests for folding short entries and building suggested timesheet blocks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from pytoolbox.core.activity_blocks import fold_short_entries, suggest_blocks

T0 = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Rec:
    project: str
    start_dt: datetime
    end_dt: Optional[datetime]
    duration_hours: float
    app: str = "App"
    category: str = "editor"
    detail: str = ""
    ext: str = ""


def rec(project, start_min, end_min, app="App", detail=""):
    start = T0 + timedelta(minutes=start_min)
    end = T0 + timedelta(minutes=end_min)
    return Rec(project, start, end, (end - start).total_seconds() / 3600, app=app, detail=detail)


def test_fold_joins_short_switch_into_previous_and_rejoins_same_key():
    records = [rec("a", 0, 10), rec("b", 10, 10.05), rec("a", 10.05, 20)]
    folded = fold_short_entries(records, 10, T0)
    assert len(folded) == 1
    assert folded[0].project == "a"
    assert round(folded[0].duration_hours * 60, 2) == 20


def test_fold_keeps_short_entry_after_a_gap():
    records = [rec("a", 0, 10), rec("b", 15, 15.05)]
    assert len(fold_short_entries(records, 10, T0)) == 2


def test_fold_disabled_with_zero():
    records = [rec("a", 0, 10), rec("b", 10, 10.05)]
    assert len(fold_short_entries(records, 0, T0)) == 2


def test_suggest_joins_breaks_and_absorbs_detours():
    records = [
        rec("toolbox", 0, 30, app="PyCharm"),
        rec("ChatGPT", 30, 33, app="Chrome"),  # short detour
        rec("toolbox", 33, 60, app="Terminal"),
        rec("toolbox", 65, 90, app="PyCharm"),  # after a 5-minute break
        rec("samt", 120, 180, app="PyCharm"),
    ]
    blocks = suggest_blocks(records, T0, merge_gap_seconds=600, min_block_seconds=900)
    assert [(b.project, b.seconds / 60) for b in blocks] == [("toolbox", 90), ("samt", 60)]
    assert blocks[0].top_apps()[0] == "PyCharm"


def test_suggest_drops_unattributed_and_short_blocks():
    records = [rec("", 0, 60), rec("toolbox", 100, 105)]
    assert suggest_blocks(records, T0, 600, 900) == []


def test_suggest_keeps_long_other_project_between():
    records = [rec("a", 0, 30), rec("b", 30, 60), rec("a", 60, 90)]
    blocks = suggest_blocks(records, T0, 600, 900)
    assert [b.project for b in blocks] == ["a", "b", "a"]
