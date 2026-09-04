"""The full-screen `toolbox tui` app: browse commands, build arguments, run them."""

from __future__ import annotations

import shlex
from pathlib import Path

import click
from textual.app import App

from pytoolbox.tui.screens import BrowseScreen


class ToolboxApp(App):
    """Full-screen browse/build/run wizard over a Click command tree."""

    CSS_PATH = Path(__file__).parent / "app.tcss"
    TITLE = "toolbox"

    def __init__(self, root: click.Group) -> None:
        super().__init__()
        self.root_group = root

    def on_mount(self) -> None:
        root_ctx = click.Context(self.root_group, info_name="toolbox")
        self.push_screen(BrowseScreen(self.root_group, root_ctx, ["toolbox"]))

    def run_leaf(self, argv: list) -> int:
        """Suspend the TUI, run `argv` for real, and return its exit code."""
        with self.suspend():
            print(f"$ toolbox {shlex.join(argv)}")
            code = _invoke(self.root_group, argv)
            input("\nPress Enter to return to toolbox tui...")
        return code


def _invoke(root: click.Group, argv: list) -> int:
    try:
        code = root.main(args=argv, prog_name="toolbox", standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        print("Aborted!")
        return 1
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    return code if isinstance(code, int) else 0


def run_tui(root: click.Group) -> None:
    """Entry point: run the full-screen app rooted at `root` (the `toolbox` group)."""
    ToolboxApp(root).run()
