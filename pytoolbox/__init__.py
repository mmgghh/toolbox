"""pytoolbox -- a collection of small, dependency-light command-line tools.

Each module exposes one Click CLI, all of which are also reachable through the
single ``toolbox`` umbrella command:

==============  ========================  ========================================================
Console         Module                    Purpose
==============  ========================  ========================================================
``pyfm``        ``pytoolbox.pyfm``        files and directories: split, merge, rename, deduplicate
``pystr``       ``pytoolbox.pystr``       text: search, replace, clipboard, case, encoding
``pyjdate``     ``pytoolbox.pyjdate``     Jalali (Persian) and Gregorian dates
``pytime``      ``pytoolbox.pytime``      time tracking, stored in one SQLite file
``pyssh``       ``pytoolbox.pyssh``       SSH tunnels and transfers
``pynet``       ``pytoolbox.pynet``       network diagnostics: DNS, ports, HTTP, WHOIS
``pyps``        ``pytoolbox.pyps``        processes and memory: top, search, kill, swap
``pycalc``      ``pytoolbox.pycalc``      arithmetic from the shell
``pymd2pdf``    ``pytoolbox.pymd2pdf``    Markdown to PDF
``pymd2html``   ``pytoolbox.pymd2html``   Markdown to HTML
``pydocx2md``   ``pytoolbox.pydocx2md``   Word documents to Markdown
``pydocx2pdf``  ``pytoolbox.pydocx2pdf``  Word documents to PDF
``pypdf2md``    ``pytoolbox.pypdf2md``    PDF to Markdown
==============  ========================  ========================================================
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
