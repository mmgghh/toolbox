"""Interactive menu: browse every ``toolbox`` command and build its arguments.

Walks the Click command tree generically, from the ``toolbox`` group down
through however many nested groups to a leaf command, and prompts for that
leaf's own arguments and options using Click's own parameter metadata.
Nothing here is specific to any one command, so it keeps working as commands
gain or lose parameters without needing its own updates.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from typing import Optional

import click

from pytoolbox.core import console

#: Words that end the menu or step back a level, recognised at every prompt.
_QUIT_WORDS = {"q", "quit", "exit"}
_BACK_WORDS = {"b", "back"}


class _Quit(Exception):
    """Raised from anywhere in the menu to unwind straight back to run_menu()."""


def run_menu(root: click.Group) -> None:
    """Run the interactive menu loop rooted at ``root`` (the ``toolbox`` group)."""
    console.echo(console.style("toolbox menu", fg="cyan", bold=True))
    console.echo("Pick a number or a name at each step. 'b' back, 'q' quit.\n")
    try:
        _browse(root)
    except _Quit:
        pass
    console.echo("")


def _browse(root: click.Group) -> None:
    root_ctx = click.Context(root, info_name="toolbox")
    #: (group, its context, its name as reached from the root)
    stack: list[tuple[click.Group, click.Context, str]] = [(root, root_ctx, "toolbox")]

    while stack:
        group, ctx, _ = stack[-1]
        path = " ".join(name for _, _, name in stack)
        names = sorted(n for n in group.list_commands(ctx) if not _is_hidden(group, ctx, n))
        if not names:
            console.warn(f"`{path}` has no subcommands.")
            stack.pop()
            continue

        console.echo(console.style(path, fg="cyan", bold=True))
        for i, name in enumerate(names, 1):
            cmd = group.get_command(ctx, name)
            summary = cmd.get_short_help_str(limit=70).strip() if cmd else ""
            console.echo(f"  {i:>2}) {name:<12} {summary}")

        answer = _ask_name("Command", names, allow_back=len(stack) > 1)
        if answer == "__back__":
            stack.pop()
            console.echo("")
            continue

        try:
            command = group.get_command(ctx, answer)
        except click.ClickException as exc:
            console.error(str(exc))
            continue
        if command is None:
            console.error(f"No such command {answer!r}.")
            continue

        if isinstance(command, click.Group):
            sub_ctx = click.Context(command, info_name=command.name, parent=ctx)
            stack.append((command, sub_ctx, command.name or answer))
            console.echo("")
            continue

        console.echo("")
        _run_leaf(root, [name for _, _, name in stack[1:]], command)
        console.echo("")


def _is_hidden(group: click.Group, ctx: click.Context, name: str) -> bool:
    cmd = group.get_command(ctx, name)
    return bool(cmd and cmd.hidden)


def _run_leaf(root: click.Group, prefix: list[str], command: click.Command) -> None:
    while True:
        argv_tail = _build_argv(command)
        full_argv = [*prefix, command.name, *argv_tail]
        console.echo("")
        console.echo(console.style("$ toolbox " + shlex.join(full_argv), fg="green"))
        action = _confirm_run()
        if action == "run":
            _invoke(root, full_argv)
            return
        if action == "edit":
            console.echo("")
            continue
        return  # back to the command list


def _build_argv(command: click.Command) -> list[str]:
    console.echo(console.style(command.name, fg="cyan", bold=True))
    if command.help:
        console.echo(command.help.strip().splitlines()[0])
    tokens: list[str] = []
    for param in command.params:
        if not getattr(param, "expose_value", True):
            continue
        if isinstance(param, click.Argument):
            tokens.extend(_ask_argument(param))
        elif isinstance(param, click.Option):
            tokens.extend(_ask_option(param))
    return tokens


def _ask_argument(param: click.Argument) -> list[str]:
    label = param.human_readable_name
    choices = getattr(param.type, "choices", None)

    if param.nargs == -1:
        console.echo(f"{label} -- one or more, blank line to stop:")
        values: list[str] = []
        while True:
            raw = _read(f"  {label}[{len(values) + 1}]")
            if not raw:
                if values or not param.required:
                    return values
                console.error(f"{label} needs at least one value.")
                continue
            values.append(raw)

    default_str = None if param.default in (None, ()) else str(param.default)

    if choices:
        value = _choose_one(label, list(choices), default=default_str)
        return [value] if value and value != default_str else []

    raw = _read(label, required=param.required, default=default_str)
    return [raw] if raw and raw != default_str else []


def _ask_option(param: click.Option) -> list[str]:
    flag = _preferred_opt(param.opts)
    label = param.help.strip().splitlines()[0] if param.help else param.human_readable_name

    if param.is_flag:
        default_on = bool(param.default)
        if param.secondary_opts:
            answer = _ask_yes_no(f"{flag}? ({label})", default=default_on)
            if answer == default_on:
                return []
            return [flag] if answer else [_preferred_opt(param.secondary_opts)]
        if default_on:
            # No `--no-...` counterpart -- nothing this wizard can turn off.
            return []
        return [flag] if _ask_yes_no(f"{flag}? ({label})", default=False) else []

    if param.count:
        raw = _read(f"{flag} count ({label})", default="0")
        try:
            n = max(0, int(raw)) if raw else 0
        except ValueError:
            n = 0
        return [flag] * n

    if param.multiple:
        console.echo(f"{flag} ({label}) -- one or more, blank line to stop:")
        values = []
        while True:
            raw = _read(f"  value {len(values) + 1}")
            if not raw:
                break
            values.append(raw)
        tokens: list[str] = []
        for value in values:
            tokens += [flag, value]
        return tokens

    choices = getattr(param.type, "choices", None)
    if choices:
        default_str = None if param.default is None else str(param.default)
        value = _choose_one(f"{flag} ({label})", list(choices), default=default_str)
        if value is None or value == default_str:
            return []
        return [flag, value]

    if param.required:
        raw = _read(f"{flag} ({label})", required=True, hide=bool(getattr(param, "hide_input", False)))
        return [flag, raw]

    default_str = None if param.default is None else str(param.default)
    raw = _read(f"{flag} ({label})", default=default_str, hide=bool(getattr(param, "hide_input", False)))
    if not raw or raw == default_str:
        return []
    return [flag, raw]


def _preferred_opt(opts: Sequence[str]) -> str:
    long_opts = [o for o in opts if o.startswith("--")]
    return long_opts[0] if long_opts else opts[0]


def _ask_name(prompt_text: str, names: Sequence[str], *, allow_back: bool) -> str:
    hint = "b=back, " if allow_back else ""
    while True:
        raw = click.prompt(f"{prompt_text} ({hint}q=quit)", default="", show_default=False).strip()
        if not raw:
            continue
        low = raw.lower()
        if low in _QUIT_WORDS:
            raise _Quit()
        if allow_back and low in _BACK_WORDS:
            return "__back__"
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(names):
                return names[index - 1]
            console.error(f"No option {index}.")
            continue
        return raw


def _confirm_run() -> str:
    while True:
        raw = click.prompt("Run this? [Y/n/e=edit/b=back/q=quit]", default="y", show_default=False)
        raw = raw.strip().lower()
        if raw in ("", "y", "yes"):
            return "run"
        if raw in ("e", "edit"):
            return "edit"
        if raw in ("b", *_BACK_WORDS):
            return "back"
        if raw in _QUIT_WORDS:
            raise _Quit()
        console.error("Please answer y, e, b or q.")


def _ask_yes_no(label: str, *, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = click.prompt(f"  {label} [{hint}]", default="", show_default=False).strip().lower()
        if raw in _QUIT_WORDS:
            raise _Quit()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        console.error("Please answer y or n.")


def _choose_one(label: str, choices: list[str], *, default: Optional[str]) -> Optional[str]:
    console.echo(f"{label}:")
    for i, choice in enumerate(choices, 1):
        marker = " (default)" if choice == default else ""
        console.echo(f"  {i:>2}) {choice}{marker}")
    while True:
        raw = click.prompt("  choice", default="", show_default=False).strip()
        if raw.lower() in _QUIT_WORDS:
            raise _Quit()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        if raw in choices:
            return raw
        console.error("Pick one of the numbers or names above.")


def _read(label: str, *, required: bool = False, default: Optional[str] = None, hide: bool = False) -> str:
    hint = f" [{default}]" if default else (" [required]" if required else " [Enter to skip]")
    while True:
        raw = click.prompt(f"  {label}{hint}", default="", show_default=False, hide_input=hide)
        raw = raw.strip()
        if raw.lower() in _QUIT_WORDS:
            raise _Quit()
        if not raw:
            if required:
                console.error("This is required.")
                continue
            return default or ""
        return raw


def _invoke(root: click.Group, argv: list[str]) -> None:
    console.echo("")
    try:
        root.main(args=argv, prog_name="toolbox", standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
    except click.Abort:
        console.warn("aborted")
    except SystemExit:
        pass
