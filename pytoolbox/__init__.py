"""pytoolbox -- a collection of small, dependency-light command-line tools.

Each module exposes one Click CLI, all of which are also reachable through the
single ``toolbox`` umbrella command:

===========  ==================  ==========================================
Console      Module              Purpose
===========  ==================  ==========================================
``pyfm``     ``pytoolbox.pyfm``      files and directories
``pystr``    ``pytoolbox.pystr``     text, clipboard, encoding, translation
``pyjdate``  ``pytoolbox.pyjdate``   Jalali/Gregorian dates
``pytime``   ``pytoolbox.pytime``    time tracking
``pyssh``    ``pytoolbox.pyssh``     SSH tunnels and rsync
``pynet``    ``pytoolbox.pynet``     network diagnostics
``pymd2pdf`` ``pytoolbox.pymd2pdf``  Markdown to PDF
===========  ==================  ==========================================
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
