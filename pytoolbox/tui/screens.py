"""Screens for the full-screen toolbox wizard: browse commands, then build
and run one.
"""

from __future__ import annotations

import shlex

import click
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from pytoolbox.tui.fields import (
    ChoiceField,
    CountField,
    FlagField,
    MultiField,
    TextField,
    build_field,
    render_tokens,
)


class BrowseScreen(Screen):
    """Lists one group's subcommands, live-filtered by a search box."""

    BINDINGS = [
        ("slash", "focus_search", "Search"),
        ("escape", "back", "Back"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, group: click.Group, ctx: click.Context, path: list) -> None:
        super().__init__()
        self.group = group
        self.ctx = ctx
        self.path = path
        self._names = sorted(n for n in group.list_commands(ctx) if not _is_hidden(group, ctx, n))

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search commands...", id="search")
        yield OptionList(*self._options(self._names), id="commands")
        yield Footer()

    def on_mount(self) -> None:
        self.title = " › ".join(self.path)
        if not self._names:
            self.notify(f"`{' '.join(self.path)}` has no subcommands.", severity="warning")

    def _options(self, names: list):
        for name in names:
            cmd = self.group.get_command(self.ctx, name)
            summary = cmd.get_short_help_str(limit=70).strip() if cmd else ""
            yield Option(f"{name}  {summary}", id=name)

    def _help(self, name: str) -> str:
        cmd = self.group.get_command(self.ctx, name)
        return cmd.get_short_help_str(limit=70).strip() if cmd else ""

    @on(Input.Changed, "#search")
    def _filter(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        matches = [n for n in self._names if query in n.lower() or query in self._help(n).lower()]
        option_list = self.query_one("#commands", OptionList)
        option_list.clear_options()
        option_list.add_options(self._options(matches))

    @on(OptionList.OptionSelected, "#commands")
    def _choose(self, event: OptionList.OptionSelected) -> None:
        name = event.option.id
        command = self.group.get_command(self.ctx, name)
        if command is None:
            return
        if isinstance(command, click.Group):
            sub_ctx = click.Context(command, info_name=command.name, parent=self.ctx)
            self.app.push_screen(BrowseScreen(command, sub_ctx, [*self.path, command.name or name]))
            return
        self.app.push_screen(FormScreen([*self.path], command))

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_back(self) -> None:
        if len(self.path) > 1:
            self.app.pop_screen()
        else:
            self.app.exit()


def _is_hidden(group: click.Group, ctx: click.Context, name: str) -> bool:
    cmd = group.get_command(ctx, name)
    return bool(cmd and cmd.hidden)


class MultiInput(Widget):
    """A repeatable-value control: an entry box plus removable value rows."""

    class Changed(Message):
        pass

    def __init__(self, spec: MultiField) -> None:
        super().__init__()
        self.spec = spec
        self.values: list = []

    def compose(self) -> ComposeResult:
        yield Label(self.spec.label)
        yield Input(placeholder="value, Enter to add", id="entry")
        yield Vertical(id="values")

    @on(Input.Submitted, "#entry")
    async def _add(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        self.values.append(value)
        entry = self.query_one("#entry", Input)
        entry.value = ""
        await self._render_values()
        self.post_message(self.Changed())

    @on(Button.Pressed)
    async def _remove(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("remove-"):
            return
        index = int(button_id.removeprefix("remove-"))
        del self.values[index]
        await self._render_values()
        self.post_message(self.Changed())

    async def _render_values(self) -> None:
        container = self.query_one("#values", Vertical)
        await container.remove_children()
        for i, value in enumerate(self.values):
            await container.mount(Horizontal(Label(value), Button("x", id=f"remove-{i}")))


class FormScreen(Screen):
    """One widget per parameter of `command`, a live command-line preview, and run/back."""

    BINDINGS = [
        ("ctrl+r", "run", "Run"),
        ("escape", "back", "Back"),
    ]

    def __init__(self, prefix: list, command: click.Command) -> None:
        super().__init__()
        self.prefix = prefix
        self.command = command
        self.entries: list = []  # (FieldSpec, widget)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="fields"):
            for param in self.command.params:
                if not getattr(param, "expose_value", True):
                    continue
                spec = build_field(param)
                if spec is None:
                    continue
                widget = _widget_for(spec)
                self.entries.append((spec, widget))
                yield widget
        yield Static(id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self.title = " ".join([*self.prefix, self.command.name or ""])
        self._refresh_preview()

    def _current_argv(self) -> list:
        tokens: list = []
        for spec, widget in self.entries:
            tokens += render_tokens(spec, _value_of(spec, widget))
        # self.prefix[0] is always the literal "toolbox" root element (seeded
        # once in ToolboxApp.on_mount, only ever extended -- never replaced);
        # drop it here so the built argv doesn't include it twice when passed
        # to root.main(args=...), which expects args *after* the prog name.
        return [*self.prefix[1:], self.command.name, *tokens]

    def _refresh_preview(self) -> None:
        self.query_one("#preview", Static).update("$ toolbox " + shlex.join(self._current_argv()))

    @on(Input.Changed)
    @on(Select.Changed)
    @on(Switch.Changed)
    @on(MultiInput.Changed)
    def _on_change(self) -> None:
        self._refresh_preview()

    def action_run(self) -> None:
        argv = self._current_argv()
        code = self.app.run_leaf(argv)
        self.app.notify(f"exit {code}", severity="error" if code else "information", title="toolbox")

    def action_back(self) -> None:
        self.app.pop_screen()


def _widget_for(spec):
    if isinstance(spec, TextField):
        return Input(value=spec.default, placeholder=spec.label, password=spec.password)
    if isinstance(spec, ChoiceField):
        options = [(choice, choice) for choice in spec.choices]
        return Select(options, value=spec.default if spec.default is not None else Select.NULL, allow_blank=spec.default is None)
    if isinstance(spec, FlagField):
        return Switch(value=spec.default)
    if isinstance(spec, CountField):
        return Input(value=str(spec.default), placeholder=spec.label, restrict=r"[0-9]*")
    if isinstance(spec, MultiField):
        return MultiInput(spec)
    raise TypeError(f"No widget for {spec!r}")


def _value_of(spec, widget):
    if isinstance(spec, CountField):
        return int(widget.value) if widget.value else 0
    if isinstance(spec, ChoiceField):
        # Select's "nothing chosen" sentinel is Select.NULL, not None --
        # normalize it so render_tokens' `value is None` check works.
        return None if widget.value is Select.NULL else widget.value
    if isinstance(spec, MultiField):
        return list(widget.values)
    return widget.value
