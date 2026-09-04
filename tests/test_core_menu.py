"""Tests for the interactive command-building menu (``toolbox menu``).

Exercises the generic Click-introspection wizard against a small synthetic
command tree (so these stay correct regardless of how the real commands'
options evolve), plus a couple of end-to-end checks against the real
``toolbox`` group.
"""

from __future__ import annotations

import click

from pytoolbox.cli import toolbox
from pytoolbox.core.menu import run_menu


@click.group()
def fake_root() -> None:
    """Fake root."""


@fake_root.command()
def solo() -> None:
    """A leaf directly under the root."""
    click.echo("solo ran")


@fake_root.group()
def tools() -> None:
    """A group of tools."""


@tools.command()
@click.argument("name")
@click.argument("items", nargs=-1)
@click.option("--loud", is_flag=True, help="Shout it.")
@click.option("--mode", type=click.Choice(["a", "b"]), default="a", show_default=True)
@click.option("--tag", multiple=True, help="Repeatable tag.")
@click.option("-v", "--verbose", count=True)
@click.option("--label", default="x", show_default=True)
def greet(name, items, loud, mode, tag, verbose, label) -> None:
    click.echo(f"hi {name} items={list(items)} loud={loud} mode={mode} tag={list(tag)} v={verbose} label={label}")


@fake_root.group()
def barren() -> None:
    """A group with no subcommands."""


@click.command()
def menu_cmd() -> None:
    run_menu(fake_root)


def _invoke(runner, lines):
    return runner.invoke(menu_cmd, input="\n".join(lines) + "\n")


def test_lists_top_level_commands_and_quits(runner):
    result = _invoke(runner, ["q"])
    assert result.exit_code == 0, result.output
    assert "solo" in result.stdout
    assert "tools" in result.stdout


def test_navigates_into_a_group_and_back(runner):
    result = _invoke(runner, ["tools", "b", "q"])
    assert result.exit_code == 0, result.output
    assert "greet" in result.stdout
    # The root listing is shown once before descending and once again after 'b'.
    assert result.stdout.count("solo") == 2


def test_number_selects_by_position(runner):
    # Sorted top-level names are barren, solo, tools -> solo is option 2.
    result = _invoke(runner, ["2", "y", "q"])
    assert result.exit_code == 0, result.output
    assert "solo ran" in result.stdout


def test_runs_a_leaf_with_no_parameters(runner):
    result = _invoke(runner, ["solo", "", "q"])
    assert result.exit_code == 0, result.output
    assert "$ toolbox solo" in result.stdout
    assert "solo ran" in result.stdout


def test_builds_arguments_and_every_option_kind(runner):
    result = _invoke(
        runner,
        [
            "tools",  # descend into the group
            "greet",  # pick the leaf
            "bob",  # name (required argument)
            "x",  # items[1]
            "",  # items[2] -> stop collecting
            "y",  # --loud
            "b",  # --mode (differs from default "a")
            "t1",  # --tag value 1
            "",  # --tag value 2 -> stop collecting
            "2",  # --verbose count
            "",  # --label -> skip, keep default
            "y",  # confirm run
            "q",  # quit back at the "tools" listing
        ],
    )
    assert result.exit_code == 0, result.output
    assert "$ toolbox tools greet bob x --loud --mode b --tag t1 --verbose --verbose" in result.stdout
    assert "hi bob items=['x'] loud=True mode=b tag=['t1'] v=2 label=x" in result.stdout


def test_accepting_every_default_omits_every_flag(runner):
    result = _invoke(
        runner,
        [
            "tools",
            "greet",
            "bob",  # name
            "",  # items -> none
            "",  # --loud -> No
            "",  # --mode -> default
            "",  # --tag -> none
            "",  # --verbose -> 0
            "",  # --label -> default
            "y",
            "q",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "$ toolbox tools greet bob" in result.stdout
    assert "hi bob items=[] loud=False mode=a tag=[] v=0 label=x" in result.stdout


def test_edit_rebuilds_the_command_before_running(runner):
    result = _invoke(
        runner,
        [
            "solo",
            "e",  # edit: rebuild (solo has no params, so this just re-confirms)
            "y",
            "q",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.count("solo ran") == 1


def test_back_at_confirm_returns_to_the_command_list_without_running(runner):
    result = _invoke(runner, ["solo", "b", "q"])
    assert result.exit_code == 0, result.output
    assert "solo ran" not in result.stdout


def test_quit_at_any_prompt_ends_the_menu(runner):
    result = _invoke(runner, ["tools", "greet", "q"])
    assert result.exit_code == 0, result.output


def test_empty_group_warns_and_steps_back(runner):
    result = _invoke(runner, ["barren", "q"])
    assert result.exit_code == 0, result.output
    assert "no subcommands" in result.stderr


def test_unknown_name_is_reported_and_reprompts(runner):
    result = _invoke(runner, ["nonsense", "solo", "", "q"])
    assert result.exit_code == 0, result.output
    assert "No such command" in result.stderr
    assert "solo ran" in result.stdout


# --- against the real toolbox group ----------------------------------------


def test_real_menu_runs_a_real_command(runner):
    # calc's params, in order: EXPRESSION (variadic), --caret, --base,
    # --precision (each a blank to accept its default), then confirm and quit.
    result = runner.invoke(toolbox, ["menu"], input="calc\n2+2\n\n\n\n\ny\nq\n")
    assert result.exit_code == 0, result.output
    assert "$ toolbox calc 2+2" in result.stdout
    assert "4" in result.stdout


def test_real_menu_top_level_lists_every_subcommand(runner):
    from pytoolbox.cli import SUBCOMMANDS

    result = runner.invoke(toolbox, ["menu"], input="q\n")
    assert result.exit_code == 0, result.output
    for name in SUBCOMMANDS:
        assert name in result.stdout
