"""Where pytoolbox keeps its config, data, cache and runtime files.

Follows the XDG Base Directory spec on Linux/Termux, uses the conventional
per-platform locations on macOS/Windows, and never writes inside the
installed package (which may live in a read-only site-packages tree).
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

APP_NAME = "pytoolbox"

#: Set ``PYTOOLBOX_HOME`` to keep every pytoolbox file under one directory.
HOME_ENV = "PYTOOLBOX_HOME"


def is_termux() -> bool:
    """Whether we are running inside Termux on Android."""
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    return Path("/data/data/com.termux/files/usr").is_dir()


def is_windows() -> bool:
    """Whether we are running on Windows."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """Whether we are running on macOS."""
    return sys.platform == "darwin"


def termux_prefix() -> Optional[Path]:
    """Return the Termux ``$PREFIX`` directory, or ``None`` off Termux."""
    prefix = os.environ.get("PREFIX")
    if prefix and "com.termux" in prefix:
        return Path(prefix)
    fallback = Path("/data/data/com.termux/files/usr")
    return fallback if fallback.is_dir() else None


def _override_root() -> Optional[Path]:
    raw = os.environ.get(HOME_ENV)
    return Path(raw).expanduser() if raw else None


def _xdg(env_var: str, default: Path) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser() / APP_NAME
    return default


def config_dir() -> Path:
    """Directory for user-editable configuration files."""
    root = _override_root()
    if root:
        return root / "config"
    if is_windows():
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP_NAME
    if is_macos():
        return Path.home() / "Library/Application Support" / APP_NAME
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config" / APP_NAME)


def data_dir() -> Path:
    """Directory for durable user data (databases, exports kept between runs)."""
    root = _override_root()
    if root:
        return root / "data"
    if is_windows():
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_NAME
    if is_macos():
        return Path.home() / "Library/Application Support" / APP_NAME
    return _xdg("XDG_DATA_HOME", Path.home() / ".local/share" / APP_NAME)


def cache_dir() -> Path:
    """Directory for regenerable files."""
    root = _override_root()
    if root:
        return root / "cache"
    if is_windows():
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_NAME / "Cache"
    if is_macos():
        return Path.home() / "Library/Caches" / APP_NAME
    return _xdg("XDG_CACHE_HOME", Path.home() / ".cache" / APP_NAME)


def runtime_dir() -> Path:
    """Directory for short-lived files tied to a running process (PIDs, secrets).

    ``XDG_RUNTIME_DIR`` is preferred because it is usually a private tmpfs that
    gets wiped at logout. Termux does not define it, so we fall back to the
    cache directory rather than a world-readable ``/tmp``.
    """
    root = _override_root()
    if root:
        return root / "run"
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if raw and Path(raw).is_dir():
        return Path(raw) / APP_NAME
    return cache_dir() / "run"


def ensure_dir(path: Path, private: bool = False) -> Path:
    """Create ``path`` (and parents) if needed and return it.

    With ``private=True`` the directory is restricted to the owner, which
    matters for :func:`runtime_dir` where we store SSH passwords.
    """
    path.mkdir(parents=True, exist_ok=True)
    if private and not is_windows():
        try:
            path.chmod(stat.S_IRWXU)
        except OSError:  # pragma: no cover - unusual filesystems (e.g. FAT)
            pass
    return path


def write_private_file(path: Path, content: str) -> Path:
    """Write ``content`` to ``path`` with owner-only permissions.

    The file is created with the restrictive mode already in place, so the
    secret is never briefly readable by other users.
    """
    ensure_dir(path.parent, private=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def temp_dir() -> Path:
    """A writable temporary directory that also works under Termux."""
    prefix = termux_prefix()
    if prefix is not None:
        candidate = prefix / "tmp"
        if candidate.is_dir() and os.access(candidate, os.W_OK):
            return candidate
    return Path(tempfile.gettempdir())


def font_dirs() -> list[Path]:
    """Font directories to search, most specific first.

    Termux has no ``/usr/share/fonts``; its packages install under ``$PREFIX``
    and users commonly drop faces into ``~/.termux/fonts`` or ``~/.fonts``.
    """
    dirs: list[Path] = [
        Path.home() / ".local/share/fonts",
        Path.home() / ".fonts",
    ]
    prefix = termux_prefix()
    if prefix is not None:
        dirs.extend(
            [
                Path.home() / ".termux/fonts",
                prefix / "share/fonts",
                prefix / "share/fonts/TTF",
                prefix / "share/fonts/truetype",
            ]
        )
    dirs.extend(
        [
            Path("/usr/share/fonts/truetype/dejavu"),
            Path("/usr/share/fonts/truetype"),
            Path("/usr/share/fonts/TTF"),
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path.home() / "Library/Fonts",
        ]
    )
    if is_windows():
        dirs.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
    return dirs


def find_font(*names: str) -> Optional[Path]:
    """Return the first existing font file matching any of ``names``.

    Each directory is searched non-recursively first (cheap, covers the common
    layout) and then one level deep, which is how Debian and Termux group
    families into per-family subdirectories.
    """
    for directory in font_dirs():
        if not directory.is_dir():
            continue
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        for name in names:
            try:
                match = next(iter(sorted(directory.glob(f"*/{name}"))), None)
            except OSError:  # pragma: no cover - unreadable directory
                continue
            if match is not None:
                return match
    return None
