"""Filesystem path autocomplete for TUI text inputs.

Batch mode gets shell tab-completion for free once ``toolbox completion`` is
sourced; the TUI has no shell underneath it, so text fields backed by a
``click.Path``/``click.File`` type get two ways to complete instead:

- Ghost-text: Textual's built-in ``Suggester``, showing the first matching
  entry, accepted with the Right-arrow or End key.
- Ctrl+Space: a dropdown listing *every* matching entry, for when the first
  match (alphabetically) isn't the one you want.

Both are driven by the same directory listing/filtering logic below.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from typing import Optional

from textual import events, on
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.suggester import Suggester
from textual.widgets import Input, OptionList

# How many entries the Ctrl+Space dropdown will show at most, so a huge
# directory can't make the listing (or the widget) unreasonably large.
MAX_DROPDOWN_MATCHES = 100


def _matching_entries(value: str, dirs_only: bool) -> Iterator[str]:
    """Yield every filesystem entry under `value`'s directory that matches
    what's been typed so far, sorted, in the same "shown" form the user
    typed (~-relative, trailing "/" for directories).
    """
    if not value or value.isspace():
        return
    if "/" in value:
        head, _, prefix = value.rpartition("/")
        dir_part = head or "/"  # "/foo" -> head "" means the root directory
        shown_prefix = f"{head}/"
    else:
        dir_part, prefix, shown_prefix = ".", value, ""
    try:
        names = sorted(os.listdir(os.path.expanduser(dir_part)))
    except OSError:
        return
    show_hidden = prefix.startswith(".")
    for name in names:
        if not name.startswith(prefix):
            continue
        if name.startswith(".") and not show_hidden:
            continue
        is_dir = os.path.isdir(os.path.join(os.path.expanduser(dir_part), name))
        if dirs_only and not is_dir:
            continue
        suggestion = f"{shown_prefix}{name}"
        yield f"{suggestion}/" if is_dir else suggestion


def list_path_matches(value: str, dirs_only: bool = False) -> list:
    """All filesystem entries matching `value`, for the Ctrl+Space dropdown."""
    return list(itertools.islice(_matching_entries(value, dirs_only), MAX_DROPDOWN_MATCHES))


class PathSuggester(Suggester):
    """Suggests the first filesystem entry matching what's typed so far."""

    def __init__(self, *, dirs_only: bool = False) -> None:
        # Filenames are case-sensitive on the filesystems this runs against,
        # and directory listings change between keystrokes, so caching a
        # casefolded value would go stale or match the wrong entry.
        super().__init__(use_cache=False, case_sensitive=True)
        self.dirs_only = dirs_only

    async def get_suggestion(self, value: str) -> Optional[str]:
        return next(_matching_entries(value, self.dirs_only), None)


class _PathOptionList(OptionList):
    """The Ctrl+Space dropdown -- hides itself as soon as it loses focus."""

    def _on_blur(self, event: events.Blur) -> None:
        super()._on_blur(event)
        self.display = False


class PathInput(Vertical):
    """A path `Input` plus a Ctrl+Space dropdown listing every matching entry.

    Ghost-text completion (Right/End) comes from `PathSuggester` as before;
    Ctrl+Space is for when more than one entry matches and the first one
    (alphabetically) isn't the one you want.
    """

    BINDINGS = [
        # A real Ctrl+Space keystroke arrives as the literal "ctrl+@" key
        # (that's what a terminal's NUL byte decodes to) -- Textual's
        # "ctrl+space" name is only ever synthesized in tests, never by a
        # real terminal, so it's listed here too only as a harmless belt.
        Binding("ctrl+@,ctrl+space", "show_options", "Path options", show=False),
        Binding("escape", "close_options", "Close options", show=False),
    ]

    def __init__(self, *, dirs_only: bool = False, **input_kwargs) -> None:
        super().__init__()
        self.dirs_only = dirs_only
        self._input_kwargs = input_kwargs

    def compose(self) -> ComposeResult:
        yield Input(suggester=PathSuggester(dirs_only=self.dirs_only), **self._input_kwargs)
        yield _PathOptionList()

    def on_mount(self) -> None:
        self.query_one(_PathOptionList).display = False

    @property
    def value(self) -> str:
        return self.query_one(Input).value

    @value.setter
    def value(self, new_value: str) -> None:
        self.query_one(Input).value = new_value

    def action_show_options(self) -> None:
        matches = list_path_matches(self.query_one(Input).value, self.dirs_only)
        if not matches:
            return
        options = self.query_one(_PathOptionList)
        options.clear_options()
        options.add_options(matches)
        options.highlighted = 0
        options.display = True
        options.focus()

    def action_close_options(self) -> None:
        options = self.query_one(_PathOptionList)
        if not options.display:
            # Nothing to close -- let Escape fall through to "back" as usual.
            raise SkipAction()
        options.display = False
        self.query_one(Input).focus()

    @on(OptionList.OptionSelected)
    def _pick(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        input_widget = self.query_one(Input)
        input_widget.value = str(event.option.prompt)
        input_widget.action_end()
        self.query_one(_PathOptionList).display = False
        input_widget.focus()
