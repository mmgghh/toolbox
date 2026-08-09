"""The ``toolbox`` umbrella command.

Groups every pytoolbox CLI under one name so you only have to remember one:
``toolbox fm partition ...`` is exactly ``pyfm partition ...``. The individual
``py*`` commands remain installed and unchanged.

Submodules are imported lazily: ``pymd2pdf`` pulls in fpdf2 and Pillow, and
paying that import cost for ``toolbox net ping`` would be silly (and would
make the whole umbrella fail when an optional dependency is missing).
"""

from __future__ import annotations

import importlib
from typing import Optional

import click

from pytoolbox.core import clipboard, paths
from pytoolbox.core.options import CONTEXT_SETTINGS, AliasedGroup, version_option

#: name -> (module, attribute, one-line description)
SUBCOMMANDS: dict[str, tuple[str, str, str]] = {
    "fm": ("pytoolbox.pyfm", "file_management", "Files and directories (pyfm)"),
    "str": ("pytoolbox.pystr", "str_cli", "Text, clipboard and encoding (pystr)"),
    "jdate": ("pytoolbox.pyjdate", "jdate_cli", "Jalali/Gregorian dates (pyjdate)"),
    "time": ("pytoolbox.pytime", "time_cli", "Time tracking (pytime)"),
    "ssh": ("pytoolbox.pyssh", "ssh_management", "SSH tunnels and rsync (pyssh)"),
    "net": ("pytoolbox.pynet", "net_cli", "Network diagnostics (pynet)"),
    "md2pdf": ("pytoolbox.pymd2pdf", "pymd2pdf_cli", "Markdown to PDF (pymd2pdf)"),
}


class LazyGroup(AliasedGroup):
    """Resolves each subcommand's module only when that subcommand is invoked."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted({*super().list_commands(ctx), *SUBCOMMANDS})

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:
        entry = SUBCOMMANDS.get(cmd_name)
        if entry is not None:
            return self._load(cmd_name, entry)
        return super().get_command(ctx, cmd_name)

    @staticmethod
    def _load(name: str, entry: tuple[str, str, str]) -> click.Command:
        module_name, attribute, description = entry
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise click.ClickException(
                f"`toolbox {name}` needs a dependency that is not installed: {exc}. "
                f"Try `pip install 'pytoolbox[all]'`."
            ) from exc
        command = getattr(module, attribute)
        command.short_help = description
        return command

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Built from the table above rather than by importing every module,
        # which is the whole point of the lazy group.
        rows = [(name, entry[2]) for name, entry in sorted(SUBCOMMANDS.items())]
        rows += [
            (name, self.commands[name].get_short_help_str(limit=60))
            for name in sorted(self.commands)
            if name not in SUBCOMMANDS
        ]
        with formatter.section("Commands"):
            formatter.write_dl(rows)


@click.group(cls=LazyGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def toolbox() -> None:
    """Small command-line tools for everyday local work.

    \b
    Each subcommand is also installed as a standalone command:
      toolbox fm ...      ==  pyfm ...
      toolbox str ...     ==  pystr ...
      toolbox jdate ...   ==  pyjdate ...
      toolbox time ...    ==  pytime ...
      toolbox ssh ...     ==  pyssh ...
      toolbox net ...     ==  pynet ...
      toolbox md2pdf ...  ==  pymd2pdf ...

    \b
    Examples:
      toolbox jdate now
      toolbox fm duplicates ~/downloads
      toolbox net ip
      toolbox doctor
    """


@toolbox.command()
def doctor() -> None:
    """Check which optional dependencies and system tools are available.

    \b
    Reports what works on this machine and what to install for the rest --
    useful on a fresh Termux setup where most system tools are absent.
    """
    import shutil
    import sys

    from pytoolbox import __version__

    click.echo(f"pytoolbox {__version__} on {sys.platform}, Python {sys.version.split()[0]}")
    click.echo(f"Termux: {'yes' if paths.is_termux() else 'no'}")
    click.echo("")

    click.echo("Python packages")
    for module, purpose, extra in (
        ("click", "all commands", ""),
        ("requests", "pynet, pyfm extract-links, pymd2pdf", ""),
        ("fpdf", "pymd2pdf", "pdf"),
        ("PIL", "pymd2pdf images", "pdf"),
        ("openpyxl", "pytime --format excel", "excel"),
        ("arabic_reshaper", "Persian shaping in pymd2pdf", "rtl"),
        ("bidi", "Persian shaping in pymd2pdf", "rtl"),
        ("socks", "SOCKS proxy checks in pyssh", "socks"),
    ):
        try:
            importlib.import_module(module)
            status = click.style("ok", fg="green")
        except ImportError:
            hint = f"  (pip install 'pytoolbox[{extra}]')" if extra else ""
            status = click.style("missing", fg="yellow") + hint
        click.echo(f"  {module:<18} {status:<30} {purpose}")

    click.echo("")
    click.echo("System tools")
    for tool, purpose in (
        ("ssh", "pyssh tunnel / double-tunnel"),
        ("sshpass", "pyssh password authentication"),
        ("rsync", "pyssh rsync-dir"),
        ("ping", "pynet ping (falls back to TCP probes)"),
        ("mmdc", "offline Mermaid rendering in pymd2pdf"),
        ("fc-cache", "font cache refresh for pymd2pdf"),
    ):
        found = shutil.which(tool)
        status = click.style("ok", fg="green") if found else click.style("missing", fg="yellow")
        click.echo(f"  {tool:<18} {status:<30} {purpose}")

    click.echo("")
    click.echo("Clipboard")
    backend = clipboard.backend_name()
    if backend == "none":
        click.echo(f"  {click.style('missing', fg='yellow')} -- pystr clipboard commands are unavailable")
    else:
        click.echo(f"  {click.style('ok', fg='green')} using {backend}")

    click.echo("")
    click.echo("Paths")
    click.echo(f"  config   {paths.config_dir()}")
    click.echo(f"  data     {paths.data_dir()}")
    click.echo(f"  cache    {paths.cache_dir()}")
    click.echo(f"  runtime  {paths.runtime_dir()}")


@toolbox.command("where")
def where_command() -> None:
    """Print the directories pytoolbox reads and writes."""
    click.echo(f"config:  {paths.config_dir()}")
    click.echo(f"data:    {paths.data_dir()}")
    click.echo(f"cache:   {paths.cache_dir()}")
    click.echo(f"runtime: {paths.runtime_dir()}")
    click.echo(f"temp:    {paths.temp_dir()}")


if __name__ == "__main__":  # pragma: no cover
    toolbox()
