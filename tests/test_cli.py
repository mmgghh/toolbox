"""Tests for the ``toolbox`` umbrella command and cross-cutting CLI behaviour."""

from __future__ import annotations

import importlib

import pytest
from click.testing import CliRunner

from pytoolbox import __version__
from pytoolbox.cli import SUBCOMMANDS, toolbox

#: Every console script declared in pyproject.toml.
ENTRY_POINTS = [
    ("pytoolbox.pyfm", "file_management"),
    ("pytoolbox.pystr", "str_cli"),
    ("pytoolbox.pyjdate", "jdate_cli"),
    ("pytoolbox.pytime", "time_cli"),
    ("pytoolbox.pyssh", "ssh_management"),
    ("pytoolbox.pynet", "net_cli"),
    ("pytoolbox.pymd2pdf", "pymd2pdf_cli"),
    ("pytoolbox.cli", "toolbox"),
]


@pytest.mark.parametrize(("module_name", "attribute"), ENTRY_POINTS)
def test_every_entry_point_resolves(module_name, attribute):
    module = importlib.import_module(module_name)
    assert hasattr(module, attribute), f"{module_name}:{attribute} is missing"


@pytest.mark.parametrize(("module_name", "attribute"), ENTRY_POINTS)
def test_every_cli_has_help_and_version(module_name, attribute):
    command = getattr(importlib.import_module(module_name), attribute)
    runner = CliRunner()

    help_result = runner.invoke(command, ["--help"])
    assert help_result.exit_code == 0
    assert "Usage:" in help_result.output

    short_help = runner.invoke(command, ["-h"])
    assert short_help.exit_code == 0

    version_result = runner.invoke(command, ["--version"])
    assert version_result.exit_code == 0
    assert __version__ in version_result.output


def test_umbrella_lists_every_subcommand(runner):
    result = runner.invoke(toolbox, ["--help"])
    assert result.exit_code == 0
    for name in SUBCOMMANDS:
        assert name in result.output


@pytest.mark.parametrize("name", sorted(SUBCOMMANDS))
def test_umbrella_forwards_to_each_subcommand(runner, name):
    result = runner.invoke(toolbox, [name, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_umbrella_runs_a_real_subcommand(runner):
    result = runner.invoke(toolbox, ["jdate", "convert", "-g", "2026-01-04"])
    assert result.exit_code == 0, result.output
    assert "1404-10-14" in result.output


def test_umbrella_prefix_matching(runner):
    result = runner.invoke(toolbox, ["jd", "convert", "-g", "2026-01-04"])
    assert result.exit_code == 0, result.output


def test_doctor_reports_environment(runner):
    result = runner.invoke(toolbox, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "pytoolbox" in result.output
    assert "System tools" in result.output
    assert "Clipboard" in result.output


def test_where_lists_directories(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTOOLBOX_HOME", str(tmp_path / "home"))
    result = runner.invoke(toolbox, ["where"])
    assert result.exit_code == 0
    assert str(tmp_path / "home") in result.output


def test_unknown_subcommand_is_reported(runner):
    result = runner.invoke(toolbox, ["nonsense"])
    assert result.exit_code != 0
