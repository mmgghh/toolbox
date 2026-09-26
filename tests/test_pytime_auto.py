"""Tests for the ``pytime auto`` command group and its storage layer."""

from __future__ import annotations

import json
import time

import pytest

from pytoolbox.core.activity_rules import Classified
from pytoolbox.pytime import (
    advance_activity,
    close_after_gap,
    close_stale_activity_entries,
    connect,
    resolve_db_path,
    time_cli,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "pytime.db"
    monkeypatch.setenv("PYTIME_DB", str(path))
    return path


def _classified(**overrides):
    defaults = {"category": "editor", "app": "VS Code", "project": "toolbox", "detail": "pytime.py", "ext": "py"}
    defaults.update(overrides)
    return Classified(**defaults)


def test_advance_activity_opens_and_keeps_running_entry(db):
    conn = connect(resolve_db_path(None))
    state = advance_activity(conn, None, _classified(), 1000.0)
    assert state is not None
    entry_id, key = state
    row = conn.execute("SELECT * FROM activity_entries WHERE id = ?", (entry_id,)).fetchone()
    assert row["end_ts"] is None
    assert row["project"] == "toolbox"

    # Same classification again: no new row, entry stays open.
    state2 = advance_activity(conn, state, _classified(), 1005.0)
    assert state2 == state
    assert conn.execute("SELECT COUNT(*) AS n FROM activity_entries").fetchone()["n"] == 1
    conn.close()


def test_advance_activity_closes_and_opens_on_change(db):
    conn = connect(resolve_db_path(None))
    state = advance_activity(conn, None, _classified(detail="a.py"), 1000.0)
    state = advance_activity(conn, state, _classified(detail="b.py"), 1010.0)
    rows = conn.execute("SELECT * FROM activity_entries ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["end_ts"] == 1010.0
    assert rows[1]["end_ts"] is None
    assert rows[1]["detail"] == "b.py"
    conn.close()
    assert state is not None


def test_advance_activity_closes_on_idle():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE activity_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, app TEXT, "
        "project TEXT, detail TEXT, ext TEXT, start_ts REAL, end_ts REAL, last_seen_ts REAL)"
    )
    state = advance_activity(conn, None, _classified(), 1000.0)
    state = advance_activity(conn, state, None, 1050.0)
    assert state is None
    row = conn.execute("SELECT * FROM activity_entries").fetchone()
    assert row["end_ts"] == 1050.0


def _seed(db):
    conn = connect(resolve_db_path(None))
    state = advance_activity(conn, None, Classified("editor", "VS Code", "toolbox", "a.py", "py"), 1_700_000_000.0)
    state = advance_activity(
        conn, state, Classified("editor", "VS Code", "toolbox", "b.md", "md"), 1_700_000_600.0
    )
    state = advance_activity(
        conn, state, Classified("browser", "Chrome", "ChatGPT", "ChatGPT", ""), 1_700_001_200.0
    )
    advance_activity(conn, state, None, 1_700_001_800.0)
    conn.close()


def test_auto_status_reports_nothing_when_idle(runner, db):
    result = runner.invoke(time_cli, ["auto", "status"])
    assert result.exit_code == 0
    assert "Nothing is being auto-tracked" in result.output


def test_auto_status_reports_running_entry(db, runner):
    conn = connect(resolve_db_path(None))
    advance_activity(conn, None, _classified(), time.time() - 30)
    conn.close()
    result = runner.invoke(time_cli, ["auto", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["running"] is True
    assert payload["project"] == "toolbox"
    assert payload["detail"] == "pytime.py"


def test_auto_report_ungrouped(runner, db):
    _seed(db)
    result = runner.invoke(time_cli, ["auto", "report", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert len(rows) == 3
    assert {row["project"] for row in rows} == {"toolbox", "ChatGPT"}


def test_auto_report_group_by_ext(runner, db):
    _seed(db)
    result = runner.invoke(time_cli, ["auto", "report", "-g", "ext", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    exts = {row["ext"] for row in rows}
    assert exts == {"py", "md", ""}


def test_auto_report_filters_by_category(runner, db):
    _seed(db)
    result = runner.invoke(time_cli, ["auto", "report", "--category", "browser", "--format", "json"])
    rows = json.loads(result.stdout)
    assert len(rows) == 1
    assert rows[0]["app"] == "Chrome"


def test_auto_report_search_and_no_project(runner, db):
    _seed(db)
    found = json.loads(runner.invoke(time_cli, ["auto", "report", "-q", "b.m", "--format", "json"]).stdout)
    assert [row["detail"] for row in found] == ["b.md"]
    result = runner.invoke(time_cli, ["auto", "report", "--no-project"])
    assert "No records found" in result.output


def test_auto_delete_by_filter(runner, db):
    _seed(db)
    result = runner.invoke(time_cli, ["auto", "delete", "--app", "Chrome", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Deleted 1" in result.output
    rows = json.loads(runner.invoke(time_cli, ["auto", "report", "--format", "json"]).stdout)
    assert {row["app"] for row in rows} == {"VS Code"}


def test_auto_delete_requires_filter_or_all(runner, db):
    _seed(db)
    refused = runner.invoke(time_cli, ["auto", "delete", "--yes"])
    assert refused.exit_code != 0
    assert "--all" in refused.stderr
    done = runner.invoke(time_cli, ["auto", "delete", "--all", "--yes"])
    assert "Deleted 3" in done.output


def test_auto_delete_leaves_manual_entries(runner, db):
    runner.invoke(time_cli, ["start", "keep me"])
    _seed(db)
    runner.invoke(time_cli, ["auto", "delete", "--all", "--yes"])
    payload = json.loads(runner.invoke(time_cli, ["status", "--json"]).stdout)
    assert payload["entries"][0]["task"] == "keep me"


def test_auto_report_no_records(runner, db):
    result = runner.invoke(time_cli, ["auto", "report"])
    assert result.exit_code == 0
    assert "No records found" in result.output


def test_auto_rules_shows_backend_and_override_path(runner, db):
    result = runner.invoke(time_cli, ["auto", "rules"])
    assert result.exit_code == 0, result.output
    assert "Window backend:" in result.output
    assert "Rules override file:" in result.output


def test_auto_probe_prints_raw_and_classified(runner, db, monkeypatch):
    import pytoolbox.pytime as pt
    from pytoolbox.core.activewindow import WindowInfo

    monkeypatch.setattr(pt, "detect_backend", lambda: "gnome")
    monkeypatch.setattr(
        pt, "get_active_window", lambda backend=None: WindowInfo("toolbox – pytime.py", "jetbrains-pycharm-ce")
    )
    result = runner.invoke(time_cli, ["auto", "probe", "--delay", "0"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["raw"]["wm_class"] == "jetbrains-pycharm-ce"
    assert payload["classified"]["project"] == "toolbox"
    assert payload["classified"]["ext"] == "py"


def _seed_today(db, spans):
    """spans: (project, app, start_minutes_after_9am, end_minutes)."""
    from datetime import datetime

    base = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    conn = connect(resolve_db_path(None))
    for project, app, start, end in spans:
        conn.execute(
            "INSERT INTO activity_entries (category, app, project, detail, ext, start_ts, end_ts) "
            "VALUES ('editor', ?, ?, '', '', ?, ?)",
            (app, project, base + start * 60, base + end * 60),
        )
    conn.commit()
    conn.close()


def test_auto_report_min_seconds_folds_quick_switches(runner, db):
    _seed(db)
    conn = connect(resolve_db_path(None))
    conn.execute(
        "INSERT INTO activity_entries (category, app, project, detail, ext, start_ts, end_ts) "
        "VALUES ('other', 'Files', '', 'x', '', ?, ?)",
        (1_700_001_800.0, 1_700_001_803.0),
    )
    conn.commit()
    conn.close()
    folded = json.loads(runner.invoke(time_cli, ["auto", "report", "--format", "json"]).stdout)
    raw = json.loads(runner.invoke(time_cli, ["auto", "report", "--format", "json", "--min-seconds", "0"]).stdout)
    assert len(raw) == 4
    assert len(folded) == 3


def test_auto_today_summarizes_projects_and_apps(runner, db):
    _seed_today(db, [("toolbox", "PyCharm", 0, 60), ("Claude", "Claude", 60, 90)])
    payload = json.loads(runner.invoke(time_cli, ["auto", "today", "--json"]).stdout)
    assert round(payload["total_hours"], 2) == 1.5
    assert payload["projects"][0]["project"] == "toolbox"
    assert payload["projects"][0]["share"] == "67%"
    table = runner.invoke(time_cli, ["auto", "today"])
    assert "Auto-tracked today: 01:30" in table.output


def test_auto_suggest_then_apply_creates_manual_entries_once(runner, db):
    _seed_today(db, [("toolbox", "PyCharm", 0, 30), ("ChatGPT", "Chrome", 30, 33), ("toolbox", "Terminal", 33, 60)])
    preview = runner.invoke(time_cli, ["auto", "suggest"])
    assert "toolbox" in preview.output and "01:00" in preview.output
    assert "ChatGPT" not in preview.output  # absorbed detour

    applied = runner.invoke(time_cli, ["auto", "suggest", "--apply", "--yes"])
    assert "Added 1 manual entry" in applied.output
    rows = json.loads(runner.invoke(time_cli, ["report", "--format", "json"]).stdout)
    assert [(r["project"], r["task"], r["duration_hours"]) for r in rows] == [("toolbox", "auto-tracked", "1")]

    again = runner.invoke(time_cli, ["auto", "suggest", "--apply", "--yes"])
    assert "skip: overlaps entry 1" in again.output
    assert "Nothing new to add" in again.output


def test_auto_shell_init_prints_hook(runner):
    result = runner.invoke(time_cli, ["auto", "shell-init", "bash"])
    assert result.exit_code == 0
    assert "__pytime_preexec" in result.output
    assert "CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1" in result.output
    assert "add-zsh-hook" in runner.invoke(time_cli, ["auto", "shell-init", "zsh"]).output


def test_watch_pauses_while_screen_locked(runner, db, monkeypatch):
    import pytoolbox.pytime as pt
    from pytoolbox.core.activewindow import WindowInfo

    ticks = {"n": 0}

    def locked():
        ticks["n"] += 1
        if ticks["n"] >= 4:
            import os
            import signal

            os.kill(os.getpid(), signal.SIGINT)
        return ticks["n"] == 2

    monkeypatch.setattr(pt, "detect_backend", lambda: "gnome")
    monkeypatch.setattr(pt, "get_idle_seconds", lambda: None)
    monkeypatch.setattr(pt, "is_screen_locked", locked)
    monkeypatch.setattr(pt, "get_active_window", lambda backend=None: WindowInfo("a.py — toolbox", "code"))
    result = runner.invoke(time_cli, ["auto", "watch", "--interval", "0.1"])
    assert result.exit_code == 0, result.output
    assert "(paused: screen locked)" in result.stderr
    rows = json.loads(runner.invoke(time_cli, ["auto", "report", "--format", "json", "--min-seconds", "0"]).stdout)
    assert len(rows) == 2  # tracking stopped at the lock and resumed after it


def test_watch_rejects_broken_rules_file(runner, db, monkeypatch, tmp_path):
    import pytoolbox.pytime as pt
    from pytoolbox.core import activity_rules

    bad = tmp_path / "rules.json"
    bad.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(activity_rules, "default_rules_path", lambda: bad)
    monkeypatch.setattr(pt, "detect_backend", lambda: "gnome")
    result = runner.invoke(time_cli, ["auto", "watch"])
    assert result.exit_code != 0
    assert "Could not read" in result.stderr


def test_manual_and_auto_entries_do_not_interfere(runner, db):
    runner.invoke(time_cli, ["start", "-p", "demo", "write docs"])
    conn = connect(resolve_db_path(None))
    advance_activity(conn, None, _classified(), time.time() - 30)
    conn.close()

    manual_status = runner.invoke(time_cli, ["status", "--json"])
    manual_payload = json.loads(manual_status.stdout)
    assert manual_payload["entries"][0]["task"] == "write docs"

    auto_status = runner.invoke(time_cli, ["auto", "status", "--json"])
    auto_payload = json.loads(auto_status.stdout)
    assert auto_payload["project"] == "toolbox"


# --- suspend gaps and entries left open by a dead watcher ------------------


def test_close_after_gap_closes_at_previous_tick(db):
    conn = connect(resolve_db_path(None))
    state = advance_activity(conn, None, _classified(), 1000.0)
    assert close_after_gap(conn, state, 1010.0, 1020.0, max_gap=30.0) == state
    assert close_after_gap(conn, state, 1010.0, 4610.0, max_gap=30.0) is None
    row = conn.execute("SELECT end_ts FROM activity_entries").fetchone()
    assert row["end_ts"] == 1010.0
    conn.close()


def test_close_stale_activity_entries_uses_last_heartbeat(db):
    conn = connect(resolve_db_path(None))
    now = 100_000.0
    stale = advance_activity(conn, None, _classified(detail="stale.py"), now - 5000)
    conn.execute("UPDATE activity_entries SET last_seen_ts = ? WHERE id = ?", (now - 4000, stale[0]))
    fresh = advance_activity(conn, None, _classified(detail="fresh.py"), now - 5000)
    conn.execute("UPDATE activity_entries SET last_seen_ts = ? WHERE id = ?", (now - 60, fresh[0]))
    legacy = advance_activity(conn, None, _classified(detail="legacy.py"), now - 5000)
    conn.execute("UPDATE activity_entries SET last_seen_ts = NULL WHERE id = ?", (legacy[0],))
    conn.commit()

    assert close_stale_activity_entries(conn, now) == 2
    ends = {r["detail"]: r["end_ts"] for r in conn.execute("SELECT detail, end_ts FROM activity_entries")}
    assert ends == {"stale.py": now - 4000, "fresh.py": None, "legacy.py": now - 5000}
    conn.close()


def test_schema_migration_adds_last_seen_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute(
        "CREATE TABLE activity_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, "
        "app TEXT NOT NULL, project TEXT, detail TEXT, ext TEXT, start_ts REAL NOT NULL, end_ts REAL)"
    )
    old.execute("INSERT INTO activity_entries (category, app, start_ts) VALUES ('editor', 'VS Code', 1.0)")
    old.commit()
    old.close()

    conn = connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(activity_entries)")}
    assert "last_seen_ts" in columns
    assert conn.execute("SELECT COUNT(*) FROM activity_entries").fetchone()[0] == 1
    conn.close()


def test_auto_status_ignores_entry_from_dead_watcher(runner, db):
    conn = connect(resolve_db_path(None))
    advance_activity(conn, None, _classified(), time.time() - 7200)  # no heartbeat for 2h
    conn.close()
    result = runner.invoke(time_cli, ["auto", "status", "--json"])
    assert json.loads(result.stdout) == {"running": False}
    rows = json.loads(runner.invoke(time_cli, ["auto", "report", "--format", "json", "--min-seconds", "0"]).stdout)
    assert len(rows) == 1
    assert rows[0]["end_epoch"] == rows[0]["start_epoch"]  # closed at its only heartbeat


class _FakeClock:
    """Stands in for the ``time`` module inside ``watch``: each sleep advances 1s."""

    def __init__(self, start: float) -> None:
        self.now = start
        self.suspend_for = 0.0  # added once, at the next sleep

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds + self.suspend_for
        self.suspend_for = 0.0


def _run_watch(runner, monkeypatch, clock, on_tick, args=("--interval", "5")):
    import pytoolbox.pytime as pt
    from pytoolbox.core.activewindow import WindowInfo

    ticks = {"n": 0}

    def locked():
        ticks["n"] += 1
        on_tick(ticks["n"])
        return False

    monkeypatch.setattr(pt, "time_module", clock)
    monkeypatch.setattr(pt, "detect_backend", lambda: "gnome")
    monkeypatch.setattr(pt, "get_idle_seconds", lambda: None)
    monkeypatch.setattr(pt, "is_screen_locked", locked)
    monkeypatch.setattr(pt, "get_active_window", lambda backend=None: WindowInfo("a.py — toolbox", "code"))
    return runner.invoke(time_cli, ["auto", "watch", *args])


def _stop():
    import os
    import signal

    os.kill(os.getpid(), signal.SIGINT)


def _entries(db):
    conn = connect(resolve_db_path(None))
    rows = [dict(r) for r in conn.execute("SELECT * FROM activity_entries ORDER BY id")]
    conn.close()
    return rows


def test_watch_does_not_count_suspend(runner, db, monkeypatch):
    start = 1_800_000_000.0
    clock = _FakeClock(start)

    def on_tick(n):
        if n == 2:
            clock.suspend_for = 3600  # suspended for an hour after tick 2
        if n == 5:
            _stop()

    result = _run_watch(runner, monkeypatch, clock, on_tick)
    assert result.exit_code == 0, result.output
    assert "clock jumped" in result.stderr
    first, second = _entries(db)
    # Closed at the last tick before the suspend, not after it.
    assert first["end_ts"] - first["start_ts"] == pytest.approx(5.0)
    assert second["start_ts"] >= start + 3600
    assert second["end_ts"] - second["start_ts"] < 60


def test_watch_heartbeats_open_entry(runner, db, monkeypatch):
    clock = _FakeClock(1_800_000_000.0)
    seen = []

    def on_tick(n):
        if n > 1:
            seen.append(_entries(db)[0]["last_seen_ts"])
        if n == 30:
            _stop()

    result = _run_watch(runner, monkeypatch, clock, on_tick)
    assert result.exit_code == 0, result.output
    # A heartbeat roughly every minute (rounded up to the next 5s tick).
    beats = sorted(set(seen))
    assert len(beats) == 3
    assert all(60.0 <= b - a <= 66.0 for a, b in zip(beats, beats[1:]))


def test_watch_closes_entries_left_open_by_earlier_watcher(runner, db, monkeypatch):
    conn = connect(resolve_db_path(None))
    advance_activity(conn, None, _classified(detail="old.py"), 1_799_999_000.0)
    conn.close()
    clock = _FakeClock(1_800_000_000.0)
    result = _run_watch(runner, monkeypatch, clock, lambda n: n == 2 and _stop())
    assert result.exit_code == 0, result.output
    assert "left open by an earlier watcher" in result.stderr
    old = _entries(db)[0]
    assert old["end_ts"] == old["start_ts"]


def test_watch_rejects_interval_beyond_heartbeat_window(runner, db, monkeypatch):
    import pytoolbox.pytime as pt

    monkeypatch.setattr(pt, "detect_backend", lambda: "gnome")
    result = runner.invoke(time_cli, ["auto", "watch", "--interval", "600"])
    assert result.exit_code != 0
    assert "--interval" in result.output
