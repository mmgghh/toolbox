"""Tests for the ``pytime auto`` command group and its storage layer."""

from __future__ import annotations

import json

import pytest

from pytoolbox.core.activity_rules import Classified
from pytoolbox.pytime import advance_activity, connect, resolve_db_path, time_cli


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
        "project TEXT, detail TEXT, ext TEXT, start_ts REAL, end_ts REAL)"
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
    advance_activity(conn, None, _classified(), 1_700_000_000.0)
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


def test_manual_and_auto_entries_do_not_interfere(runner, db):
    runner.invoke(time_cli, ["start", "-p", "demo", "write docs"])
    conn = connect(resolve_db_path(None))
    advance_activity(conn, None, _classified(), 1_700_000_000.0)
    conn.close()

    manual_status = runner.invoke(time_cli, ["status", "--json"])
    manual_payload = json.loads(manual_status.stdout)
    assert manual_payload["entries"][0]["task"] == "write docs"

    auto_status = runner.invoke(time_cli, ["auto", "status", "--json"])
    auto_payload = json.loads(auto_status.stdout)
    assert auto_payload["project"] == "toolbox"
