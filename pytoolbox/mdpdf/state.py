"""Document-wide settings the renderers read while a conversion is running.

These are module-level rather than parameters on purpose: the block renderers
are a dozen small functions that would otherwise each grow a parameter for one
rarely-changed knob. Keeping them in their own module — rather than in
whichever module happens to write them — means every reader sees the same
value no matter which module it lives in, and reads stay late-bound, so a
rebinding by ``convert`` or the CLI takes effect everywhere.

Always read through the module (``state.BODY_SIZE``), never
``from ... import BODY_SIZE``: an imported name is a snapshot and would miss
the rebinding this module exists to propagate.
"""

from __future__ import annotations

from pathlib import Path

#: Body text size in points. ``convert`` rebinds this for --font-size and
#: restores it afterwards, so it is the default rather than a constant.
BODY_SIZE = 10

#: Set by the CLI: when true, nothing reaches out to the network (no remote
#: images, no mermaid.ink). Sensible default for offline/metered devices.
offline = False

#: Extra faces named by ``--fallback-font``, registered after the built-in ones.
extra_fallback_fonts: tuple[Path, ...] = ()

#: str.translate table built by ``PDF.__init__`` once the loaded faces (and
#: hence the set of drawable code points) are known.
glyph_translation: dict[int, str] = {}
