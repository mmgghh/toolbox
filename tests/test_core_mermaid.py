"""Tests for the Mermaid-rendering helper shared by pymd2html and mdpdf."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import requests

from pytoolbox.core import mermaid


@pytest.fixture(autouse=True)
def _reset_warn_state(monkeypatch):
    """Every test starts with a clean warn-once flag."""
    monkeypatch.setattr(mermaid, "_warned", False)


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


def test_render_ink_uses_the_right_endpoint_and_background_per_format(monkeypatch):
    seen = {}

    def fake_get(url, timeout=None):
        seen["url"] = url
        return _FakeResponse(b"data")

    monkeypatch.setattr("requests.get", fake_get)

    mermaid.render_ink("graph TD;", fmt="svg")
    assert "/svg/" in seen["url"]
    assert "bgColor" not in seen["url"]

    mermaid.render_ink("graph TD;", fmt="png")
    assert "/img/" in seen["url"]
    assert "bgColor=white" in seen["url"]


def test_prefers_a_local_mmdc_install(monkeypatch):
    monkeypatch.setattr(mermaid, "HAS_MMDC", True)

    def fake_run(cmd, **kwargs):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"<svg>local</svg>")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("subprocess.run", fake_run)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the network was used despite a working mmdc")

    monkeypatch.setattr(mermaid, "render_ink", explode)

    assert mermaid.render("graph TD; A-->B;", fmt="svg") == b"<svg>local</svg>"


def test_falls_back_to_ink_when_mmdc_fails(monkeypatch):
    monkeypatch.setattr(mermaid, "HAS_MMDC", True)
    monkeypatch.setattr("subprocess.run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1))
    monkeypatch.setattr("requests.get", lambda url, timeout=None: _FakeResponse(b"<svg>ink</svg>"))

    assert mermaid.render("graph TD; A-->B;", fmt="svg") == b"<svg>ink</svg>"


def test_offline_skips_the_network_and_warns_once_across_formats(monkeypatch, capsys):
    monkeypatch.setattr(mermaid, "HAS_MMDC", False)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the network was used despite offline=True")

    monkeypatch.setattr(mermaid, "render_ink", explode)

    assert mermaid.render("graph TD; A-->B;", fmt="svg", offline=True) is None
    assert mermaid.render("graph TD; A-->B;", fmt="png", offline=True) is None
    assert capsys.readouterr().err.count("warning:") == 1


def test_network_failure_returns_none_and_warns_once(monkeypatch, capsys):
    monkeypatch.setattr(mermaid, "HAS_MMDC", False)
    monkeypatch.setattr(
        "requests.get",
        lambda url, timeout=None: (_ for _ in ()).throw(requests.exceptions.ConnectionError("down")),
    )

    assert mermaid.render("graph TD; A-->B;", fmt="svg") is None
    assert mermaid.render("graph TD; A-->B;", fmt="svg") is None
    err = capsys.readouterr().err
    assert err.count("warning:") == 1
    assert "could not render Mermaid diagram" in err


def test_a_transient_network_failure_is_retried_once(monkeypatch):
    monkeypatch.setattr(mermaid, "HAS_MMDC", False)
    calls = []

    def flaky(url, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("dropped")
        return _FakeResponse(b"<svg>retried</svg>")

    monkeypatch.setattr("requests.get", flaky)

    assert mermaid.render("graph TD; A-->B;", fmt="svg") == b"<svg>retried</svg>"
    assert len(calls) == 2
