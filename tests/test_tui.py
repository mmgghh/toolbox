"""Tests for the full-screen ``toolbox tui`` wizard.

Uses Textual's headless run_test()/Pilot harness, driven via asyncio.run()
so no extra test-runner plugin (e.g. pytest-asyncio) is needed. Exercises a
small synthetic command tree, the same way test_core_menu.py exercises
menu.py -- so these stay correct regardless of how the real commands'
options evolve.
"""

from __future__ import annotations

import asyncio

import click
import pytest

pytest.importorskip("textual", reason="TUI is an optional extra")

from pytoolbox.tui.app import ToolboxApp, _invoke  # noqa: E402
from pytoolbox.tui.screens import BrowseScreen, FormScreen  # noqa: E402


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
@click.option("--loud", is_flag=True, help="Shout it.")
@click.option("--mode", type=click.Choice(["a", "b"]), default="a", show_default=True)
@click.option("-v", "--verbose", count=True)
@click.option("--label", default="x", show_default=True)
def greet(name, loud, mode, verbose, label) -> None:
    click.echo(f"hi {name} loud={loud} mode={mode} v={verbose} label={label}")


@fake_root.group()
def barren() -> None:
    """A group with no subcommands."""


def run(coro):
    return asyncio.run(coro)


async def _mount_root_browse(pilot) -> None:
    await pilot.pause()


def test_root_lists_top_level_commands():
    async def scenario():
        app = ToolboxApp(fake_root)
        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            screen = app.screen
            assert isinstance(screen, BrowseScreen)
            assert screen._names == ["barren", "solo", "tools"]

    run(scenario())


def test_search_filters_the_list():
    async def scenario():
        app = ToolboxApp(fake_root)
        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            await pilot.click("#search")
            for ch in "too":
                await pilot.press(ch)
            await pilot.pause()
            from textual.widgets import OptionList

            option_list = app.screen.query_one("#commands", OptionList)
            assert option_list.option_count == 1

    run(scenario())


def test_selecting_a_group_pushes_a_scoped_browse_screen():
    async def scenario():
        app = ToolboxApp(fake_root)
        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            from textual.widgets import OptionList

            option_list = app.screen.query_one("#commands", OptionList)
            index = app.screen._names.index("tools")
            option_list.highlighted = index
            option_list.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, BrowseScreen)
            assert app.screen.path == ["toolbox", "tools"]
            assert app.screen._names == ["collect", "greet"]

    run(scenario())


def test_empty_group_is_navigable_and_reported():
    async def scenario():
        app = ToolboxApp(fake_root)
        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            from textual.widgets import OptionList

            option_list = app.screen.query_one("#commands", OptionList)
            index = app.screen._names.index("barren")
            option_list.highlighted = index
            option_list.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, BrowseScreen)
            assert app.screen._names == []

    run(scenario())


async def _open_greet_form(pilot, app):
    from textual.widgets import OptionList

    option_list = app.screen.query_one("#commands", OptionList)
    index = app.screen._names.index("tools")
    option_list.highlighted = index
    option_list.focus()
    await pilot.press("enter")
    await pilot.pause()
    option_list = app.screen.query_one("#commands", OptionList)
    index = app.screen._names.index("greet")
    option_list.highlighted = index
    option_list.focus()
    await pilot.press("enter")
    await pilot.pause()


def test_form_screen_renders_one_widget_per_parameter_kind():
    async def scenario():
        app = ToolboxApp(fake_root)
        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            await _open_greet_form(pilot, app)

            from textual.widgets import Input, Select, Switch

            screen = app.screen
            assert isinstance(screen, FormScreen)
            kinds = [type(widget) for _, widget in screen.entries]
            # name (Input), --loud (Switch), --mode (Select), --verbose (Input), --label (Input)
            assert kinds == [Input, Switch, Select, Input, Input]

    run(scenario())


def test_form_preview_updates_as_fields_change():
    async def scenario():
        app = ToolboxApp(fake_root)
        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            await _open_greet_form(pilot, app)

            from textual.widgets import Input, Static

            screen = app.screen
            name_input = screen.entries[0][1]
            assert isinstance(name_input, Input)
            name_input.value = "bob"
            await pilot.pause()
            preview = screen.query_one("#preview", Static)
            assert preview.content == "$ toolbox tools greet bob"

    run(scenario())


def test_run_action_invokes_app_run_leaf_with_the_built_argv(monkeypatch):
    async def scenario():
        app = ToolboxApp(fake_root)
        seen = {}

        def fake_run_leaf(argv):
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(app, "run_leaf", fake_run_leaf)

        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            await _open_greet_form(pilot, app)

            screen = app.screen
            name_input = screen.entries[0][1]
            name_input.value = "bob"
            await pilot.pause()
            screen.action_run()
            await pilot.pause()

        assert seen["argv"] == ["tools", "greet", "bob"]

    run(scenario())


def test_invoke_runs_a_correct_argv_with_no_leading_toolbox_element(capsys):
    # Regression test for the argv-building bug: root.main(args=...) expects
    # args *after* the prog name, so a leading "toolbox" element would make
    # every real invocation fail with "Error: No such command 'toolbox'".
    code = _invoke(fake_root, ["tools", "greet", "bob"])
    capsys.readouterr()
    assert code == 0


@tools.command()
@click.argument("items", nargs=-1)
@click.option("--tag", multiple=True, help="Repeatable tag.")
def collect(items, tag) -> None:
    click.echo(f"items={list(items)} tag={list(tag)}")


async def _open_collect_form(pilot, app):
    from textual.widgets import OptionList

    option_list = app.screen.query_one("#commands", OptionList)
    index = app.screen._names.index("tools")
    option_list.highlighted = index
    option_list.focus()
    await pilot.press("enter")
    await pilot.pause()
    option_list = app.screen.query_one("#commands", OptionList)
    index = app.screen._names.index("collect")
    option_list.highlighted = index
    option_list.focus()
    await pilot.press("enter")
    await pilot.pause()


def test_multi_field_add_and_remove():
    async def scenario():
        app = ToolboxApp(fake_root)
        async with app.run_test() as pilot:
            await _mount_root_browse(pilot)
            await _open_collect_form(pilot, app)

            from pytoolbox.tui.screens import MultiInput

            screen = app.screen
            items_widget = screen.entries[0][1]
            tag_widget = screen.entries[1][1]
            assert isinstance(items_widget, MultiInput)
            assert isinstance(tag_widget, MultiInput)

            entry = items_widget.query_one("#entry")
            entry.value = "a"
            await pilot.pause()
            await entry.action_submit()  # posts Input.Submitted
            await pilot.pause()
            entry.value = "b"
            await pilot.pause()
            await entry.action_submit()
            await pilot.pause()

            assert items_widget.values == ["a", "b"]
            argv = screen._current_argv()
            assert argv == ["tools", "collect", "a", "b"]

            from textual.widgets import Button

            remove_button = items_widget.query_one("#remove-0", Button)
            await pilot.click(remove_button)
            await pilot.pause()

            assert items_widget.values == ["b"]
            argv = screen._current_argv()
            assert argv == ["tools", "collect", "b"]

    run(scenario())
