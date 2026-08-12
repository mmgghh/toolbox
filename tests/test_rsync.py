"""Tests for the rsync command builder.

Nothing here runs rsync. ``build_rsync_command`` is pure, so the argument
list it produces is asserted directly -- in particular the *order* of the
filter rules, which is the part rsync cares about and the part most likely
to regress.
"""

from __future__ import annotations

import click
import pytest

from pytoolbox.core import rsync


def options(**kwargs) -> rsync.RsyncOptions:
    """An options object with the two required fields filled in."""
    kwargs.setdefault("source", "./site")
    kwargs.setdefault("destination", "me@host:/srv/site")
    kwargs.setdefault("ssh_command", "ssh -p 22")
    return rsync.RsyncOptions(**kwargs)


def index(cmd: list[str], *pair: str) -> int:
    """Position of a rule, matched as a consecutive pair of arguments."""
    for i in range(len(cmd) - len(pair) + 1):
        if cmd[i : i + len(pair)] == list(pair):
            return i
    raise AssertionError(f"{pair} not found in {cmd}")


# ── brace expansion ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("*.jpg", ["*.jpg"]),
        ("*.{jpg,png}", ["*.jpg", "*.png"]),
        ("{a,b,c}.txt", ["a.txt", "b.txt", "c.txt"]),
        ("{src,test}/*.{js,ts}", ["src/*.js", "src/*.ts", "test/*.js", "test/*.ts"]),
        ("img{1,2}{a,b}", ["img1a", "img1b", "img2a", "img2b"]),
        ("{a,{b,c}}.txt", ["a.txt", "b.txt", "c.txt"]),
        ("no{braces", ["no{braces"]),
        ("{single}", ["{single}"]),
        ("", [""]),
    ],
)
def test_expand_braces(pattern, expected):
    assert rsync.expand_braces(pattern) == expected


def test_expand_braces_keeps_escaped_braces_literal():
    assert rsync.expand_braces(r"\{a,b\}") == [r"\{a,b\}"]


# ── regex-shaped patterns ───────────────────────────────────────────

@pytest.mark.parametrize(
    "pattern",
    ["^src/", r"\.log$", r".*\.log", r"\d+.txt", r"\w+", r"\stmp", r".*\.(jpe?g|png)"],
)
def test_regex_shaped_patterns_are_detected(pattern):
    assert rsync.looks_like_regex(pattern)


@pytest.mark.parametrize(
    "pattern",
    [".*", "*.jpg", "img_[0-9][0-9].png", "a+b", "file{2,3}", "**/*.js", "cache/", r"a\$b"],
)
def test_valid_globs_are_not_flagged_as_regex(pattern):
    assert not rsync.looks_like_regex(pattern)


def test_regex_shaped_pattern_is_rejected_with_a_suggestion():
    with pytest.raises(click.ClickException) as excinfo:
        rsync.build_rsync_command(options(exclude=(r".*\.log$",)))
    message = str(excinfo.value)
    assert "regex" in message.lower()
    assert "*.log" in message


def test_raw_patterns_disables_preprocessing():
    cmd = rsync.build_rsync_command(
        options(exclude=(r".*\.log$", "*.{jpg,png}"), raw_patterns=True)
    )
    assert "--exclude" in cmd
    assert r".*\.log$" in cmd
    assert "*.{jpg,png}" in cmd
    assert "*.jpg" not in cmd


# ── filter rule ordering ────────────────────────────────────────────

def test_match_builds_the_recursive_only_these_idiom():
    cmd = rsync.build_rsync_command(options(match=("*.jpg", "*.png")))
    assert index(cmd, "--include", "*/") < index(cmd, "--include", "*.jpg")
    assert index(cmd, "--include", "*.jpg") < index(cmd, "--include", "*.png")
    assert index(cmd, "--include", "*.png") < index(cmd, "--exclude", "*")
    assert "-m" in cmd


def test_no_match_means_no_include_rules_and_no_pruning():
    cmd = rsync.build_rsync_command(options(exclude=("*.tmp",)))
    assert "--include" not in cmd
    assert "-m" not in cmd
    assert index(cmd, "--exclude", "*.tmp")


def test_excludes_are_applied_before_matches():
    cmd = rsync.build_rsync_command(options(exclude=("node_modules",), match=("*.js",)))
    assert index(cmd, "--exclude", "node_modules") < index(cmd, "--include", "*/")


def test_full_rule_order_follows_the_spec():
    cmd = rsync.build_rsync_command(
        options(exclude=("a", "b"), match=("*.js",), gitignore=True)
    )
    positions = [
        index(cmd, "--exclude", "a"),
        index(cmd, "--exclude", "b"),
        index(cmd, "--filter", ":- .gitignore"),
        index(cmd, "--include", "*/"),
        index(cmd, "--include", "*.js"),
        index(cmd, "--exclude", "*"),
    ]
    assert positions == sorted(positions)


def test_gitignore_does_not_exclude_the_git_directory():
    cmd = rsync.build_rsync_command(options(gitignore=True))
    assert ".git" not in cmd
    assert ".git/" not in cmd


# ── individual flags ────────────────────────────────────────────────

def test_defaults_compress_and_update():
    cmd = rsync.build_rsync_command(options())
    assert cmd[0] == "rsync"
    assert "-azP" in cmd
    assert "--update" in cmd
    assert cmd[-2:] == ["./site", "me@host:/srv/site"]


def test_no_compress_drops_the_z():
    cmd = rsync.build_rsync_command(options(compress=False))
    assert "-aP" in cmd
    assert "-azP" not in cmd


def test_ignore_existing_replaces_update():
    cmd = rsync.build_rsync_command(options(ignore_existing=True))
    assert "--ignore-existing" in cmd
    assert "--update" not in cmd


def test_mirror_expands_to_both_delete_flags():
    cmd = rsync.build_rsync_command(options(mirror=True))
    assert "--delete" in cmd
    assert "--delete-excluded" in cmd


def test_backup_dir_enables_backup():
    cmd = rsync.build_rsync_command(options(delete=True, backup_dir="../attic"))
    assert "--backup" in cmd
    assert "--backup-dir=../attic" in cmd


def test_sudo_sets_the_remote_rsync_path():
    cmd = rsync.build_rsync_command(options(sudo=True))
    assert "--rsync-path=sudo rsync" in cmd


def test_transport_and_size_flags_pass_through():
    cmd = rsync.build_rsync_command(
        options(bwlimit="1.5m", min_size="1k", max_size="10m", stats=True, checksum=True)
    )
    assert "--bwlimit=1.5m" in cmd
    assert "--min-size=1k" in cmd
    assert "--max-size=10m" in cmd
    assert "--stats" in cmd
    assert "--checksum" in cmd


def test_ssh_command_is_a_single_argument():
    cmd = rsync.build_rsync_command(options(ssh_command="ssh -p 2222 -i '/my key'"))
    assert cmd[index(cmd, "-e") + 1] == "ssh -p 2222 -i '/my key'"


def test_files_from_is_passed_through():
    cmd = rsync.build_rsync_command(options(files_from="/tmp/list.txt"))
    assert "--files-from=/tmp/list.txt" in cmd


# ── rejected combinations ───────────────────────────────────────────

@pytest.mark.parametrize(
    "kwargs",
    [
        {"checksum": True, "size_only": True},
        {"existing": True, "ignore_existing": True},
        {"files_from": "/tmp/l", "match": ("*.js",)},
        {"files_from": "/tmp/l", "gitignore": True},
    ],
)
def test_conflicting_options_are_rejected(kwargs):
    with pytest.raises(click.ClickException):
        rsync.build_rsync_command(options(**kwargs))


# ── pattern files ───────────────────────────────────────────────────

def test_read_pattern_file_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "patterns.txt"
    path.write_text("# a comment\n\n*.tmp\n; another\n  *.log  \n", encoding="utf-8")
    assert rsync.read_pattern_file(path) == ["*.tmp", "*.log"]


def test_read_pattern_file_reports_a_missing_file(tmp_path):
    with pytest.raises(click.ClickException):
        rsync.read_pattern_file(tmp_path / "nope.txt")
