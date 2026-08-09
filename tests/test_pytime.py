"""Tests for the pytime tracking commands."""

from __future__ import annotations

import json

import pytest

from pytoolbox.pytime import time_cli


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Use a throwaway database for every test."""
    path = tmp_path / "pytime.db"
    monkeypatch.setenv("PYTIME_DB", str(path))
    return path


def test_status_with_no_entries(runner, db):
    result = runner.invoke(time_cli, ["status"])
    assert result.exit_code == 0
    assert "Nothing is being timed" in result.output


def test_start_then_status_then_end(runner, db):
    started = runner.invoke(time_cli, ["start", "-p", "demo", "write docs"])
    assert started.exit_code == 0, started.output
    assert "Task: write docs" in started.output

    status = runner.invoke(time_cli, ["status"])
    assert status.exit_code == 0
    assert "write docs" in status.output

    ended = runner.invoke(time_cli, ["end"])
    assert ended.exit_code == 0, ended.output
    assert "End:" in ended.output

    assert "Nothing is being timed" in runner.invoke(time_cli, ["status"]).output


def test_status_json(runner, db):
    runner.invoke(time_cli, ["start", "-p", "demo", "task"])
    result = runner.invoke(time_cli, ["status", "--json"])
    payload = json.loads(result.output)
    assert payload["running"] is True
    assert payload["entries"][0]["task"] == "task"


def test_start_closes_the_previous_entry(runner, db):
    runner.invoke(time_cli, ["start", "first"])
    runner.invoke(time_cli, ["start", "second"])
    result = runner.invoke(time_cli, ["status", "--json"])
    payload = json.loads(result.output)
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["task"] == "second"


def test_end_without_a_running_entry_fails(runner, db):
    result = runner.invoke(time_cli, ["end"])
    assert result.exit_code != 0
    assert "no unfinished entry" in result.stderr.lower()


def test_resume_reuses_project_and_task(runner, db):
    runner.invoke(time_cli, ["start", "-p", "demo", "original"])
    runner.invoke(time_cli, ["end"])
    result = runner.invoke(time_cli, ["resume"])
    assert result.exit_code == 0, result.output
    assert "Task: original" in result.output
    assert "Project: demo" in result.output


def test_resume_without_history_fails(runner, db):
    result = runner.invoke(time_cli, ["resume"])
    assert result.exit_code != 0
    assert "no entry to resume" in result.stderr.lower()


def test_add_with_duration(runner, db):
    result = runner.invoke(
        time_cli,
        ["add", "-p", "demo", "-c", "g", "--duration", "2 hours", "deep work", "2026-04-24 09:00"],
    )
    assert result.exit_code == 0, result.output
    assert "Duration (hours): 2" in result.output


def test_add_with_explicit_end(runner, db):
    result = runner.invoke(
        time_cli,
        [
            "add",
            "-c", "g",
            "--end", "2026-04-24 10:15",
            "review",
            "2026-04-24 09:00",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Duration (hours): 1.25" in result.output


def test_add_rejects_end_before_start(runner, db):
    result = runner.invoke(
        time_cli,
        ["add", "-c", "g", "--end", "2026-04-24 08:00", "oops", "2026-04-24 09:00"],
    )
    assert result.exit_code != 0
    assert "cannot be before" in result.stderr.lower()


def test_add_requires_end_or_duration(runner, db):
    result = runner.invoke(time_cli, ["add", "-c", "g", "task", "2026-04-24 09:00"])
    assert result.exit_code != 0


def _seed(runner):
    runner.invoke(
        time_cli,
        ["add", "-p", "alpha", "-c", "g", "--duration", "2 hours", "task one", "2026-04-24 09:00"],
    )
    runner.invoke(
        time_cli,
        ["add", "-p", "beta", "-c", "g", "--duration", "1 hour", "task two", "2026-04-25 09:00"],
    )


def test_report_table_includes_total(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["report"])
    assert result.exit_code == 0, result.output
    assert "Total: 3 hours (03:00) across 2 entries" in result.output


def test_report_no_total_flag(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["report", "--no-total"])
    assert "Total:" not in result.output


def test_report_json(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["report", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 2


def test_report_group_by_project(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["report", "-g", "project"])
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "beta" in result.output


def test_report_group_by_day_requires_month_and_year(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["report", "-g", "day"])
    assert result.exit_code != 0
    assert "requires" in result.stderr.lower()


def test_report_project_filter(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["report", "-p", "alpha", "--format", "json"])
    rows = json.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["project"] == "alpha"


def test_projects_and_tasks_listings(runner, db):
    _seed(runner)
    projects = runner.invoke(time_cli, ["projects", "--json"])
    assert {row["project"] for row in json.loads(projects.output)} == {"alpha", "beta"}
    tasks = runner.invoke(time_cli, ["tasks", "-p", "alpha", "--json"])
    assert [row["task"] for row in json.loads(tasks.output)] == ["task one"]


def test_edit_by_id(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["edit", "--id", "1", "--task", "renamed"])
    assert result.exit_code == 0, result.output
    assert "Task: renamed" in result.output


def test_edit_requires_a_selector(runner, db):
    result = runner.invoke(time_cli, ["edit", "--task", "x"])
    assert result.exit_code != 0


def test_delete_with_confirmation_flag(runner, db):
    _seed(runner)
    result = runner.invoke(time_cli, ["delete", "--id", "1", "--yes"])
    assert result.exit_code == 0, result.output
    remaining = json.loads(runner.invoke(time_cli, ["report", "--format", "json"]).output)
    assert len(remaining) == 1


def test_delete_without_matches(runner, db):
    result = runner.invoke(time_cli, ["delete", "--id", "999", "--yes"])
    assert result.exit_code == 0
    assert "no records matched" in result.output.lower()


def test_db_option_overrides_env(runner, tmp_path, db):
    other = tmp_path / "other.db"
    runner.invoke(time_cli, ["--db", str(other), "start", "elsewhere"])
    assert other.exists()
    assert "Nothing is being timed" in runner.invoke(time_cli, ["status"]).output
