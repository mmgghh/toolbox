"""Best-effort Mermaid diagram rendering: local mmdc, then mermaid.ink, then give up.

Shared by ``pymd2html`` (an inline SVG) and ``mdpdf`` (a raster image embedded
in the PDF page) -- both need the same "try mmdc, then the network, warn once"
sequence, just for a different output format. Depends only on ``requests``, a
hard dependency of every pytoolbox command, so importing this module never
pulls in an optional extra a caller might not have installed.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests

from pytoolbox.core import console

#: mermaid-cli (`mmdc`), if installed, renders diagrams locally and offline.
#: Otherwise a diagram falls back to the mermaid.ink web API, and finally to
#: showing the raw source untouched.
HAS_MMDC = shutil.which("mmdc") is not None

MMDC_TIMEOUT_SECONDS = 30
INK_TIMEOUT_SECONDS = 15

#: mermaid.ink's path segment and mmdc's background colour, per output format.
_INK_PATH = {"svg": "svg", "png": "img"}
_MMDC_BACKGROUND = {"svg": "transparent", "png": "white"}

#: Whether the "no network" warning has already been printed this run, so a
#: document full of diagrams says it once rather than once per diagram.
_warned = False


def _mmdc(source: str, fmt: str) -> Optional[bytes]:
    """Render via a local mermaid-cli install. Returns file bytes or None."""
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "diagram.mmd"
        out_path = Path(tmp) / f"diagram.{fmt}"
        in_path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            ["mmdc", "-i", str(in_path), "-o", str(out_path), "-b", _MMDC_BACKGROUND[fmt]],
            capture_output=True,
            timeout=MMDC_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode == 0 and out_path.is_file():
            return out_path.read_bytes()
    return None


def render_ink(source: str, fmt: str) -> bytes:
    """Render via the mermaid.ink web API. Returns file bytes; raises on failure."""
    b64 = base64.urlsafe_b64encode(source.encode("utf-8")).decode("ascii")
    suffix = "?bgColor=white" if fmt == "png" else ""
    resp = requests.get(
        f"https://mermaid.ink/{_INK_PATH[fmt]}/{b64}{suffix}", timeout=INK_TIMEOUT_SECONDS
    )
    resp.raise_for_status()
    return resp.content


def render(source: str, fmt: str, offline: bool = False) -> Optional[bytes]:
    """Best-effort Mermaid render: local mmdc, then mermaid.ink, then None.

    ``fmt`` is ``"svg"`` (inline-embedded HTML) or ``"png"`` (a PDF page image).
    """
    global _warned
    if HAS_MMDC:
        try:
            data = _mmdc(source, fmt)
            if data:
                return data
        except Exception:
            pass
    if offline:
        if not _warned:
            console.warn(
                "--offline is set and mermaid-cli is unavailable; showing raw "
                "diagram source. Install it with `npm install -g @mermaid-js/mermaid-cli`."
            )
            _warned = True
        return None
    try:
        try:
            return render_ink(source, fmt)
        except requests.exceptions.RequestException:
            # Transient failures (dropped connections, timeouts) are common
            # enough on this public endpoint to warrant one retry before
            # falling back to showing the raw source.
            return render_ink(source, fmt)
    except Exception as exc:
        if not _warned:
            console.warn(
                "could not render Mermaid diagram "
                f"({'mmdc failed and ' if HAS_MMDC else ''}mermaid.ink request "
                f"failed: {exc}); showing raw source instead. Install mermaid-cli "
                "(`npm install -g @mermaid-js/mermaid-cli`) for offline rendering."
            )
            _warned = True
        return None
