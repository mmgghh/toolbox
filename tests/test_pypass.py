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
from click.testing import CliRunner

from pytoolbox.pypass import ChromeRow, entry_name, existing_entries, pass_cli, read_chrome_csv, store_dir


@pytest.fixture
def runner():
    return CliRunner()


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


def test_import_chrome_inserts_each_row(tmp_path, monkeypatch, runner):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "name,url,username,password\n"
        "A,https://a.example.com/,alice,pw-a\n"
        "B,https://b.example.com/,bob,pw-b\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run(cmd, input=None, text=None, capture_output=None, check=False):
        calls.append((cmd, input))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("pytoolbox.pypass.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("pytoolbox.pypass.subprocess.run", fake_run)
    monkeypatch.setattr("pytoolbox.pypass.existing_entries", lambda store: set())
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(tmp_path / "store"))

    result = runner.invoke(pass_cli, ["import-chrome", str(csv_path), "--no-shred"])

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            ["pass", "insert", "-m", "alice@a.example.com"],
            "pw-a\nlogin: alice\nurl: https://a.example.com/\n",
        ),
        (
            ["pass", "insert", "-m", "bob@b.example.com"],
            "pw-b\nlogin: bob\nurl: https://b.example.com/\n",
        ),
    ]
    assert "Imported 2" in result.output


def test_import_chrome_requires_pass(tmp_path, monkeypatch, runner):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "name,url,username,password\nA,https://a.example.com/,alice,pw\n", encoding="utf-8"
    )
    monkeypatch.setattr("pytoolbox.pypass.shutil.which", lambda name: None)

    result = runner.invoke(pass_cli, ["import-chrome", str(csv_path)])

    assert result.exit_code != 0
    assert "pass" in result.output


def test_import_chrome_skips_unparseable_and_empty(tmp_path, monkeypatch, runner):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "name,url,username,password\n"
        "A,,alice,pw\n"
        "B,https://b.example.com/,bob,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("pytoolbox.pypass.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "pytoolbox.pypass.subprocess.run", lambda *a, **k: pytest.fail("should not insert")
    )
    monkeypatch.setattr("pytoolbox.pypass.existing_entries", lambda store: set())

    result = runner.invoke(pass_cli, ["import-chrome", str(csv_path), "--no-shred"])

    assert result.exit_code == 0, result.output
    assert "Imported 0" in result.output
    assert "no usable URL" in result.output
    assert "empty password" in result.output


def test_import_chrome_suffixes_and_skips_duplicates(tmp_path, monkeypatch, runner):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "name,url,username,password\n"
        "A,https://a.example.com/,alice,pw-new\n"
        "B,https://a.example.com/,alice,pw-old\n",
        encoding="utf-8",
    )
    inserted = []

    def fake_run(cmd, input=None, text=None, capture_output=None, check=False):
        if cmd[:2] == ["pass", "show"]:
            class Result:
                returncode = 0
                stdout = "pw-old\nlogin: alice\nurl: https://a.example.com/\n"
                stderr = ""

            return Result()
        inserted.append(cmd)

        class Ok:
            returncode = 0
            stdout = ""
            stderr = ""

        return Ok()

    monkeypatch.setattr("pytoolbox.pypass.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("pytoolbox.pypass.subprocess.run", fake_run)
    monkeypatch.setattr("pytoolbox.pypass.existing_entries", lambda store: {"alice@a.example.com"})

    result = runner.invoke(pass_cli, ["import-chrome", str(csv_path), "--no-shred"])

    assert result.exit_code == 0, result.output
    assert inserted == [["pass", "insert", "-m", "alice@a.example.com-2"]]
    assert "duplicate" in result.output.lower()


def test_import_chrome_dry_run_does_not_insert(tmp_path, monkeypatch, runner):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "name,url,username,password\nA,https://a.example.com/,alice,pw\n", encoding="utf-8"
    )
    monkeypatch.setattr("pytoolbox.pypass.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "pytoolbox.pypass.subprocess.run",
        lambda *a, **k: pytest.fail("dry run must not call pass"),
    )
    monkeypatch.setattr("pytoolbox.pypass.existing_entries", lambda store: set())

    result = runner.invoke(pass_cli, ["import-chrome", str(csv_path), "-n"])

    assert result.exit_code == 0, result.output
    assert "Would import 1" in result.output


def test_import_chrome_prompts_to_shred(tmp_path, monkeypatch, runner):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "name,url,username,password\nA,https://a.example.com/,alice,pw\n", encoding="utf-8"
    )

    def fake_run(cmd, input=None, text=None, capture_output=None, check=False):
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("pytoolbox.pypass.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("pytoolbox.pypass.subprocess.run", fake_run)
    monkeypatch.setattr("pytoolbox.pypass.existing_entries", lambda store: set())

    result = runner.invoke(pass_cli, ["import-chrome", str(csv_path), "-y"])

    assert result.exit_code == 0, result.output
    assert not csv_path.exists()
