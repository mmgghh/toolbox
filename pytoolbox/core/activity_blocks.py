"""Reshape auto-tracked entries for reading: fold flicker, build timesheet blocks.

Works on any record with ``category``, ``app``, ``project``, ``detail``,
``ext``, ``start_dt``, ``end_dt`` (``None`` while running) and
``duration_hours`` -- in practice ``pytime.ActivityRecord``. Nothing here
touches the database; the raw entries always stay as recorded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

#: Two entries closer than this are treated as back to back (the watcher
#: closes one and opens the next at the same instant, give or take rounding).
_CONTIGUOUS_SECONDS = 1.0


def _end(record, now: datetime) -> datetime:
    return record.end_dt or now


def _key(record) -> tuple:
    return (record.category, record.app, record.project, record.detail, record.ext)


def _extend(record, other, now: datetime):
    return replace(
        record,
        end_dt=other.end_dt,
        duration_hours=record.duration_hours + other.duration_hours,
    )


def fold_short_entries(records: Sequence, min_seconds: float, now: datetime) -> list:
    """Fold entries shorter than ``min_seconds`` into the entry right before them.

    A quick alt-tab through three windows otherwise shows up as three rows of
    a few seconds each. Only back-to-back entries are folded, so a short entry
    after an idle gap stays as it is; consecutive entries that end up with
    the same classification are joined into one row. Totals are unchanged.
    """
    if min_seconds <= 0:
        return list(records)
    folded: list = []
    for record in records:
        previous = folded[-1] if folded else None
        contiguous = (
            previous is not None
            and abs((record.start_dt - _end(previous, now)).total_seconds()) <= _CONTIGUOUS_SECONDS
        )
        if contiguous and (record.duration_hours * 3600 < min_seconds or _key(previous) == _key(record)):
            folded[-1] = _extend(previous, record, now)
            continue
        folded.append(record)
    return folded


@dataclass
class Block:
    """A stretch of time proposed as one manual entry for ``project``."""

    project: str
    start: datetime
    end: datetime
    app_seconds: dict = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def top_apps(self, limit: int = 3) -> list[str]:
        ranked = sorted(self.app_seconds.items(), key=lambda item: item[1], reverse=True)
        return [app for app, _ in ranked[:limit]]


def _absorb(block: Block, other: Block) -> None:
    block.end = max(block.end, other.end)
    for app, seconds in other.app_seconds.items():
        block.app_seconds[app] = block.app_seconds.get(app, 0.0) + seconds


def suggest_blocks(
    records: Sequence,
    now: datetime,
    merge_gap_seconds: float = 600,
    min_block_seconds: float = 900,
) -> list[Block]:
    """Turn auto entries into timesheet-style blocks, one project each.

    1. Consecutive entries of the same project less than ``merge_gap_seconds``
       apart join into one block (short idle breaks don't split work).
    2. A brief detour -- the whole gap between two blocks of the same project
       is at most ``merge_gap_seconds`` -- is absorbed into that project, the
       way a timesheet wouldn't log a two-minute look at chat separately.
    3. Unattributed time and blocks shorter than ``min_block_seconds`` are
       dropped.
    """
    blocks: list[Block] = []
    for record in records:
        segment = Block(
            project=record.project or "",
            start=record.start_dt,
            end=_end(record, now),
            app_seconds={record.app: record.duration_hours * 3600},
        )
        if (
            blocks
            and blocks[-1].project == segment.project
            and (segment.start - blocks[-1].end).total_seconds() <= merge_gap_seconds
        ):
            _absorb(blocks[-1], segment)
        else:
            blocks.append(segment)

    changed = True
    while changed:
        changed = False
        for index in range(1, len(blocks) - 1):
            before, after = blocks[index - 1], blocks[index + 1]
            if (
                before.project
                and before.project == after.project
                and (after.start - before.end).total_seconds() <= merge_gap_seconds
            ):
                _absorb(before, after)
                del blocks[index : index + 2]
                changed = True
                break

    return [block for block in blocks if block.project and block.seconds >= min_block_seconds]
