"""Tests for forward specifications."""

from __future__ import annotations

import click
import pytest

from pytoolbox.ssh import command


def test_a_local_forward_becomes_dash_l():
    assert command.forward_args(local=["5432:db.internal:5432"]) == [
        "-L",
        "127.0.0.1:5432:db.internal:5432",
    ]


def test_a_local_forward_keeps_an_explicit_bind_address():
    assert command.forward_args(local=["0.0.0.0:5432:db:5432"]) == [
        "-L",
        "0.0.0.0:5432:db:5432",
    ]


def test_public_binds_the_wildcard_address():
    assert command.forward_args(dynamic=["9998"], public=True) == ["-D", "0.0.0.0:9998"]


def test_a_remote_forward_becomes_dash_r():
    assert command.forward_args(remote=["8080:localhost:3000"]) == [
        "-R",
        "8080:localhost:3000",
    ]


def test_a_bare_remote_port_is_a_remote_socks_proxy():
    """ssh -R PORT with no destination proxies for the server."""
    assert command.forward_args(remote=["1080"]) == ["-R", "1080"]


def test_forwards_keep_the_order_they_were_given():
    args = command.forward_args(local=["1:h:1"], remote=["2"], dynamic=["3"])
    assert args == ["-L", "127.0.0.1:1:h:1", "-R", "2", "-D", "127.0.0.1:3"]


@pytest.mark.parametrize(
    "spec", ["", "notaport", "5432", "5432:db", "5432:db:notaport", "1:2:3:4:5", "99999:db:80"]
)
def test_a_malformed_local_forward_is_rejected(spec):
    with pytest.raises(click.ClickException):
        command.forward_args(local=[spec])


@pytest.mark.parametrize("spec", ["", "notaport", "0", "70000", "a:b:c:d:e"])
def test_a_malformed_remote_forward_is_rejected(spec):
    with pytest.raises(click.ClickException):
        command.forward_args(remote=[spec])


def test_the_error_names_the_expected_shape():
    with pytest.raises(click.ClickException) as excinfo:
        command.forward_args(local=["5432"])
    assert "port:host:hostport" in str(excinfo.value)


def test_local_listeners_reports_ports_to_wait_for():
    assert command.local_listeners(local=["5432:db:5432"], dynamic=["9998"]) == [
        ("127.0.0.1", 5432),
        ("127.0.0.1", 9998),
    ]


def test_local_listeners_has_no_channel_for_remote_forwards():
    """A -R listener is on the far side; no local probe can ever see it,
    so the function deliberately offers nowhere to pass one."""
    with pytest.raises(TypeError):
        command.local_listeners(remote=["8080:localhost:3000"])


def test_background_args_without_a_socket():
    assert command.background_args(None) == ["-f"]


def test_background_args_with_a_socket(tmp_path):
    socket = tmp_path / "ctl"
    assert command.background_args(socket) == ["-f", "-M", "-S", str(socket)]


@pytest.mark.parametrize("port", ["¹⁵", "５４３２"])
def test_a_non_ascii_local_port_is_rejected(port):
    """isdigit() is true for non-ASCII digits like superscripts and fullwidth
    numerals; _port must require ASCII digits so a bad spec always becomes a
    ClickException instead of crashing or producing garbage argv."""
    with pytest.raises(click.ClickException):
        command.forward_args(local=[f"{port}:db:5432"])


@pytest.mark.parametrize("port", ["¹⁵", "５４３２"])
def test_a_non_ascii_remote_port_is_rejected(port):
    with pytest.raises(click.ClickException):
        command.forward_args(remote=[port])


@pytest.mark.parametrize("port", ["¹⁵", "５４３２"])
def test_a_non_ascii_dynamic_port_is_rejected(port):
    with pytest.raises(click.ClickException):
        command.forward_args(dynamic=[port])


def test_an_empty_local_bind_is_rejected():
    with pytest.raises(click.ClickException):
        command.forward_args(local=[":5432:host:5432"])


def test_an_empty_dynamic_bind_is_rejected():
    with pytest.raises(click.ClickException):
        command.forward_args(dynamic=[":1080"])


def test_an_empty_remote_bind_is_still_accepted():
    """Unlike -L/-D, a -R bind address is sshd's business, not ours (see
    _remote's docstring), so an empty bind passes through untouched even
    though the analogous -L/-D shape is now rejected."""
    assert command.forward_args(remote=[":8080:localhost:80"]) == [
        "-R",
        ":8080:localhost:80",
    ]


def test_a_bare_remote_port_is_still_accepted():
    """Regression guard: tightening _port's ASCII check must not affect -R's
    ordinary bare-port shape."""
    assert command.forward_args(remote=["1080"]) == ["-R", "1080"]
