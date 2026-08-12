"""The OPC container: a zip of XML parts, plus the relationships between them."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import click

#: Namespaces used across the parts, registered once so tags can be built by name.
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
}

DOCUMENT_PART = "word/document.xml"
MEDIA_PREFIX = "word/media/"

#: Every Word 97-2003 file starts with the OLE2 compound-document signature.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def qn(tag: str) -> str:
    """Expand a ``prefix:local`` tag into the ``{uri}local`` form ElementTree uses."""
    prefix, _, local = tag.partition(":")
    return f"{{{NS[prefix]}}}{local}"


def attr(element: ET.Element, name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a namespaced attribute, e.g. ``attr(el, "w:val")``."""
    return element.get(qn(name), default)


class Package:
    """A parsed ``.docx``: its XML parts, its relationships and its media."""

    def __init__(
        self,
        parts: dict[str, ET.Element],
        rels: dict[str, str],
        media: dict[str, bytes],
        path: Path,
    ) -> None:
        self._parts = parts
        self._rels = rels
        self._media = media
        self.path = path

    @property
    def document(self) -> ET.Element:
        return self._parts[DOCUMENT_PART]

    def part(self, name: str) -> Optional[ET.Element]:
        """Return a parsed part, or None when the document does not contain it.

        Most parts are optional: a document with no comments simply has no
        ``word/comments.xml``, which is not an error.
        """
        return self._parts.get(name)

    def rel_target(self, rel_id: str) -> Optional[str]:
        """Resolve a relationship id from ``document.xml.rels`` to its target."""
        return self._rels.get(rel_id)

    def media(self, name: str) -> Optional[bytes]:
        """Raw bytes of an embedded file, keyed by its part name."""
        return self._media.get(name)


def _fail(path: Path, message: str) -> click.ClickException:
    return click.ClickException(f"{path}: {message}")


def _parse(data: bytes) -> Optional[ET.Element]:
    """Parse a part, refusing anything carrying a DTD.

    ElementTree expands internal entities, so a crafted document could blow up
    memory ("billion laughs"). No legitimate Word part declares a doctype, so
    rejecting them costs nothing and closes the hole without a new dependency.
    """
    if b"<!DOCTYPE" in data[:4096]:
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def open_docx(path) -> Package:
    """Open a ``.docx`` and parse its parts.

    Raises a ``ClickException`` naming the actual problem -- a legacy ``.doc``,
    an encrypted file and a renamed PDF each get their own message, because
    "not a zip file" tells the user nothing about what to do next.
    """
    path = Path(path)
    header = b""
    try:
        with open(path, "rb") as handle:
            header = handle.read(8)
    except OSError as exc:
        raise _fail(path, f"cannot be read: {exc}") from exc

    if header == _OLE2_MAGIC:
        raise _fail(
            path,
            "is a Word 97-2003 .doc, which is a different format. Convert it first, "
            "e.g. `libreoffice --headless --convert-to docx <file>`.",
        )

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise _fail(path, "is not a Word .docx file (it is not a zip archive).") from exc

    with archive:
        names = set(archive.namelist())
        if "EncryptedPackage" in names:
            raise _fail(path, "is password-protected. Remove the password in Word and try again.")
        if DOCUMENT_PART not in names:
            raise _fail(path, f"is not a Word .docx file (no {DOCUMENT_PART} inside).")

        parts: dict[str, ET.Element] = {}
        media: dict[str, bytes] = {}
        for name in sorted(names):
            if name.startswith(MEDIA_PREFIX):
                media[name] = archive.read(name)
            elif name.endswith(".xml") or name.endswith(".rels"):
                parsed = _parse(archive.read(name))
                if parsed is not None:
                    parts[name] = parsed

        if DOCUMENT_PART not in parts:
            raise _fail(path, f"is not a Word .docx file ({DOCUMENT_PART} is not valid XML).")

        rels = _load_rels(parts.get("word/_rels/document.xml.rels"))
        return Package(parts, rels, media, path)


def _load_rels(part: Optional[ET.Element]) -> dict[str, str]:
    """Map relationship ids to targets. Absent rels are normal, not an error."""
    if part is None:
        return {}
    return {
        rel.get("Id", ""): rel.get("Target", "")
        for rel in part.findall(f"{{{NS['rel']}}}Relationship")
    }
