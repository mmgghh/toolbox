"""Tests for pytoolbox.ssh.hosts.

No SSH connection is ever made: parsing and resolution are pure, and the
``ssh -G`` boundary is monkeypatched.
"""

from __future__ import annotations

from pytoolbox import pyssh
from pytoolbox.ssh import hosts


def test_hosts_module_owns_the_server_spec():
    server = hosts.parse_server("me:secret@example.com:2222")
    assert (server.user, server.password, server.host, server.port) == (
        "me",
        "secret",
        "example.com",
        2222,
    )


def test_pyssh_still_exports_the_moved_names():
    """The existing CLI and test suite import these from pytoolbox.pyssh."""
    assert pyssh.parse_server is hosts.parse_server
    assert pyssh.Server is hosts.Server
    assert pyssh.load_server_conf is hosts.load_server_conf
    assert pyssh.resolve_server is hosts.resolve_server
    assert pyssh.SERVER_SPEC_RE is hosts.SERVER_SPEC_RE
