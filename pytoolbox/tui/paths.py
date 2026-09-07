"""Filesystem path autocomplete for TUI text inputs.

Batch mode gets shell tab-completion for free once ``toolbox completion`` is
sourced; the TUI has no shell underneath it, so text fields backed by a
``click.Path``/``click.File`` type get this instead -- an inline ghost-text
suggestion (Textual's built-in ``Suggester``) completed with the Right-arrow
or End key.
"""

from __future__ import annotations

import os
from typing import Optional

from textual.suggester import Suggester


class PathSuggester(Suggester):
    """Suggests the first filesystem entry matching what's typed so far."""

    def __init__(self, *, dirs_only: bool = False) -> None:
        # Filenames are case-sensitive on the filesystems this runs against,
        # and directory listings change between keystrokes, so caching a
        # casefolded value would go stale or match the wrong entry.
        super().__init__(use_cache=False, case_sensitive=True)
        self.dirs_only = dirs_only

    async def get_suggestion(self, value: str) -> Optional[str]:
        if not value or value.isspace():
            return None
        if "/" in value:
            head, _, prefix = value.rpartition("/")
            dir_part = head or "/"  # "/foo" -> head "" means the root directory
            shown_prefix = f"{head}/"
        else:
            dir_part, prefix, shown_prefix = ".", value, ""
        try:
            names = sorted(os.listdir(os.path.expanduser(dir_part)))
        except OSError:
            return None
        show_hidden = prefix.startswith(".")
        for name in names:
            if not name.startswith(prefix):
                continue
            if name.startswith(".") and not show_hidden:
                continue
            is_dir = os.path.isdir(os.path.join(os.path.expanduser(dir_part), name))
            if self.dirs_only and not is_dir:
                continue
            suggestion = f"{shown_prefix}{name}"
            return f"{suggestion}/" if is_dir else suggestion
        return None
