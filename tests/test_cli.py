"""Tests for the ``toolbox`` umbrella command and cross-cutting CLI behaviour."""

from __future__ import annotations

import importlib

import pytest
from click.shell_completion import ShellComplete
from click.testing import CliRunner

from pytoolbox import __version__
from pytoolbox.cli import SHELLS, SUBCOMMANDS, console_scripts, toolbox

#: Every console script declared in pyproject.toml.
ENTRY_POINTS = [
    ("pytoolbox.pyfm", "file_management"),
    ("pytoolbox.pystr", "str_cli"),
    ("pytoolbox.pyjdate", "jdate_cli"),
    ("pytoolbox.pytime", "time_cli"),
    ("pytoolbox.pyssh", "ssh_management"),
    ("pytoolbox.pynet", "net_cli"),
    ("pytoolbox.pymd2pdf", "pymd2pdf_cli"),
    ("pytoolbox.pymd2html", "md2html_cli"),
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


@pytest.mark.parametrize("name", sorted(SUBCOMMANDS))
def test_umbrella_usage_line_names_the_command_you_typed(runner, name):
    """`toolbox fm --help` must not say `Usage: toolbox file-management`.

    The usage line is the thing users copy; it has to be a command that runs.
    """
    result = runner.invoke(toolbox, [name, "--help"])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"Usage: toolbox {name} ")


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


# --- shell completion ------------------------------------------------------


def test_console_scripts_match_the_installed_entry_points():
    from importlib.metadata import distribution

    installed = {
        entry.name
        for entry in distribution("pytoolbox").entry_points
        if entry.group == "console_scripts"
    }
    assert set(console_scripts()) == installed


@pytest.mark.parametrize("shell", SHELLS)
def test_completion_emits_a_script_for_every_console_script(runner, shell):
    result = runner.invoke(toolbox, ["completion", shell])
    assert result.exit_code == 0, result.output
    for prog_name in console_scripts():
        assert f"_{prog_name.upper()}_COMPLETE" in result.stdout


def test_completion_detects_the_shell_from_the_environment(runner, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    result = runner.invoke(toolbox, ["completion"])
    assert result.exit_code == 0, result.output
    assert "completion for zsh" in result.stdout


def test_completion_asks_for_a_shell_when_it_cannot_tell(runner, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/nushell")
    result = runner.invoke(toolbox, ["completion"])
    assert result.exit_code != 0
    assert "Pass one explicitly" in result.stderr


def test_completion_rejects_an_unsupported_shell(runner):
    result = runner.invoke(toolbox, ["completion", "csh"])
    assert result.exit_code != 0


def _complete(args, incomplete=""):
    """Return the completion candidates Click offers for ``toolbox <args>``."""
    completer = ShellComplete(toolbox, {}, "toolbox", "_TOOLBOX_COMPLETE")
    return [item.value for item in completer.get_completions(args, incomplete)]


def test_completion_lists_subcommands_and_their_subcommands():
    top_level = _complete([])
    for name in SUBCOMMANDS:
        assert name in top_level
    assert "completion" in top_level
    assert "ping" in _complete(["net"])


def test_completion_survives_a_missing_optional_dependency(monkeypatch):
    """A broken module must not take completion down for the whole umbrella."""
    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "pytoolbox.pymd2pdf":
            raise ImportError("No module named 'fpdf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("pytoolbox.cli.importlib.import_module", fake_import)

    top_level = _complete([])
    for name in SUBCOMMANDS:
        assert name in top_level, "completion dropped commands because one import failed"


def test_missing_optional_dependency_fails_only_when_invoked(runner, monkeypatch):
    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "pytoolbox.pymd2pdf":
            raise ImportError("No module named 'fpdf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("pytoolbox.cli.importlib.import_module", fake_import)

    listed = runner.invoke(toolbox, ["--help"])
    assert listed.exit_code == 0, listed.output
    assert "md2pdf" in listed.stdout

    result = runner.invoke(toolbox, ["md2pdf", "notes.md"])
    assert result.exit_code != 0
    assert "pip install 'pytoolbox[all]'" in result.stderr


def test_pdf2md_is_registered():
    from pytoolbox.cli import SUBCOMMANDS, console_scripts

    assert "pdf2md" in SUBCOMMANDS
    assert "pypdf2md" in console_scripts()
