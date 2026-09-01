"""Tests for pypass.

`import-chrome` shells out to `pass`, so the subprocess boundary is
monkeypatched (same pattern as tests/test_pyssh.py). `export`/`import`
never touch GPG -- they only move files around -- so those are exercised
against real tmp_path directories with fixture ".gpg" files whose content
is irrelevant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pytoolbox.pypass import ChromeRow, entry_name, existing_entries, read_chrome_csv, store_dir


@pytest.mark.parametrize(
    ("url", "username", "expected"),
    [
        ("https://example.com/login", "alice", "alice@example.com"),
        ("http://example.com/login", "alice", "alice@example.com"),
        ("https://example.com:8443/login", "alice", "alice@example.com:8443"),
        ("http://example.com:8080/login", "alice", "alice@example.com:8080"),
        ("https://example.com/login", "", "example.com"),
        ("https://EXAMPLE.com/login", "alice", "alice@example.com"),
        ("https://example.com/login", "a/b", "a_b@example.com"),
        ("android://hash@com.example.app/", "alice", "alice@com.example.app"),
        ("", "alice", None),
        ("not a url at all", "alice", None),
    ],
)
def test_entry_name(url, username, expected):
    assert entry_name(url, username) == expected


def test_read_chrome_csv(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "name,url,username,password,note\n"
        "Example,https://example.com/login,alice,hunter2,\n"
        "Blank User,https://noauth.example.com/,,s3cret,\n",
        encoding="utf-8",
    )
    rows = read_chrome_csv(csv_path)
    assert rows == [
        ChromeRow(url="https://example.com/login", username="alice", password="hunter2"),
        ChromeRow(url="https://noauth.example.com/", username="", password="s3cret"),
    ]


def test_read_chrome_csv_missing_column(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("name,url,password\nx,https://example.com,hunter2\n", encoding="utf-8")
    with pytest.raises(Exception, match="missing column"):
        read_chrome_csv(csv_path)


def test_existing_entries(tmp_path):
    store = tmp_path / "store"
    (store / "sub").mkdir(parents=True)
    (store / "alice@example.com.gpg").touch()
    (store / "sub" / "bob@example.org.gpg").touch()
    (store / ".gpg-id").touch()
    assert existing_entries(store) == {"alice@example.com", "sub/bob@example.org"}


def test_existing_entries_missing_store(tmp_path):
    assert existing_entries(tmp_path / "nope") == set()


def test_store_dir_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("PASSWORD_STORE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert store_dir() == tmp_path / ".password-store"


def test_store_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(tmp_path / "custom"))
    assert store_dir() == tmp_path / "custom"


def test_store_dir_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(tmp_path / "custom"))
    assert store_dir(str(tmp_path / "explicit")) == tmp_path / "explicit"
