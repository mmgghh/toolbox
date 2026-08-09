"""Tests for the pyfm file-management commands."""

from __future__ import annotations

import json
import os

from pytoolbox.pyfm import file_management


def test_partition_by_count(runner, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    for index in range(5):
        (source / f"f{index}.txt").write_text("x" * index, encoding="utf-8")

    result = runner.invoke(
        file_management,
        ["partition", "-s", str(source), "-n", "2", "--split-based-on", "count"],
    )
    assert result.exit_code == 0, result.output
    parts = sorted(p.name for p in source.iterdir() if p.is_dir())
    assert parts == ["part-1", "part-2"]
    assert len(list((source / "part-1").iterdir())) == 3
    assert len(list((source / "part-2").iterdir())) == 2


def test_partition_dry_run_changes_nothing(runner, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")

    result = runner.invoke(
        file_management,
        ["partition", "-s", str(source), "-n", "2", "--split-based-on", "count", "--dry-run"],
    )
    assert result.exit_code == 0
    assert sorted(p.name for p in source.iterdir()) == ["a.txt"]


def test_partition_requires_exactly_one_mode(runner, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    result = runner.invoke(file_management, ["partition", "-s", str(source)])
    assert result.exit_code != 0
    assert "exactly one" in result.stderr.lower()


def test_partition_by_split_count(runner, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    for index in range(5):
        (source / f"f{index}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(file_management, ["partition", "-s", str(source), "-c", "2"])
    assert result.exit_code == 0, result.output
    parts = sorted(p.name for p in source.iterdir() if p.is_dir())
    assert parts == ["part-1", "part-2", "part-3"]


def test_merge_flattens_a_tree(runner, tree, tmp_path):
    destination = tmp_path / "dst"
    destination.mkdir()
    result = runner.invoke(
        file_management, ["merge", "-s", str(tree), "-d", str(destination)]
    )
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in destination.iterdir()) == [
        "notes.md",
        "one.txt",
        "three.txt",
        "two.txt",
    ]
    # Emptied source subdirectories are cleaned up, the root is kept.
    assert tree.exists()
    assert not (tree / "a").exists()


def test_merge_keep_both_renames_collisions(runner, tmp_path):
    source = tmp_path / "src"
    (source / "x").mkdir(parents=True)
    (source / "y").mkdir()
    (source / "x" / "same.txt").write_text("one", encoding="utf-8")
    (source / "y" / "same.txt").write_text("two", encoding="utf-8")
    destination = tmp_path / "dst"
    destination.mkdir()

    result = runner.invoke(
        file_management,
        ["merge", "-s", str(source), "-d", str(destination), "--overwrite", "keep-both"],
    )
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in destination.iterdir()) == ["same(1).txt", "same.txt"]


def test_merge_leaves_the_source_alone_when_nothing_matches(runner, tree, tmp_path):
    destination = tmp_path / "dst"
    destination.mkdir()
    result = runner.invoke(
        file_management,
        ["merge", "-s", str(tree), "-d", str(destination), "--file-pattern", r".*\.nope$"],
    )
    assert result.exit_code == 0, result.output
    assert (tree / "a").exists()
    assert (tree / "b").exists()


def test_merge_dry_run(runner, tree, tmp_path):
    destination = tmp_path / "dst"
    destination.mkdir()
    result = runner.invoke(
        file_management, ["merge", "-s", str(tree), "-d", str(destination), "--dry-run"]
    )
    assert result.exit_code == 0
    assert list(destination.iterdir()) == []


def test_batch_find_replace(runner, tmp_path):
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    (tmp_path / "b.md").write_text("hello world", encoding="utf-8")

    result = runner.invoke(
        file_management,
        ["batch-find-replace", "-d", str(tmp_path), "-x", "txt", "-f", "world", "-r", "there"],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello there"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "hello world"


def test_batch_find_replace_recursive(runner, tree):
    result = runner.invoke(
        file_management,
        ["batch-find-replace", "-d", str(tree), "-x", "txt", "-f", "alpha", "-r", "ALPHA", "-R"],
    )
    assert result.exit_code == 0, result.output
    assert (tree / "a" / "one.txt").read_text(encoding="utf-8") == "ALPHA"
    assert (tree / "b" / "three.txt").read_text(encoding="utf-8") == "ALPHA"


def test_batch_find_replace_dry_run(runner, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    result = runner.invoke(
        file_management,
        ["batch-find-replace", "-d", str(tmp_path), "-x", "txt", "-f", "hello", "-r", "bye", "-n"],
    )
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "hello"
    assert "would change" in result.output


def test_batch_find_replace_rejects_bad_regex(runner, tmp_path):
    result = runner.invoke(
        file_management,
        ["batch-find-replace", "-d", str(tmp_path), "-f", "([", "-r", "x"],
    )
    assert result.exit_code != 0
    assert "not a valid regex" in result.stderr


def test_batch_rename(runner, tmp_path):
    (tmp_path / "my file.txt").write_text("x", encoding="utf-8")
    result = runner.invoke(
        file_management, ["batch-rename", "-d", str(tmp_path), "-f", " ", "-r", "_"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "my_file.txt").exists()


def test_batch_rename_skips_conflicts(runner, tmp_path):
    (tmp_path / "a1.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b1.txt").write_text("y", encoding="utf-8")
    result = runner.invoke(
        file_management, ["batch-rename", "-d", str(tmp_path), "-f", "^a", "-r", "b"]
    )
    assert result.exit_code == 0
    assert (tmp_path / "a1.txt").exists()
    assert "conflict" in result.output.lower()


def test_duplicates_finds_identical_files(runner, tree):
    result = runner.invoke(file_management, ["duplicates", str(tree), "--json"])
    assert result.exit_code == 0, result.output
    groups = json.loads(result.output)
    assert len(groups) == 1
    # The reported paths use the platform separator, so split with os.path.
    assert sorted(os.path.basename(p) for p in groups[0]["files"]) == ["one.txt", "three.txt"]


def test_duplicates_delete_keeps_one(runner, tree):
    result = runner.invoke(file_management, ["duplicates", str(tree), "--delete", "-y"])
    assert result.exit_code == 0, result.output
    survivors = sorted(p.name for p in tree.rglob("*.txt"))
    assert survivors == ["one.txt", "two.txt"]


def test_duplicates_delete_dry_run(runner, tree):
    result = runner.invoke(file_management, ["duplicates", str(tree), "--delete", "-n"])
    assert result.exit_code == 0
    assert (tree / "b" / "three.txt").exists()
    assert "would delete" in result.output


def test_organize_by_extension(runner, tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    (tmp_path / "noext").write_text("z", encoding="utf-8")

    result = runner.invoke(file_management, ["organize", str(tmp_path), "--by", "ext"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "txt" / "a.txt").exists()
    assert (tmp_path / "md" / "b.md").exists()
    assert (tmp_path / "no-extension" / "noext").exists()


def test_organize_dry_run(runner, tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = runner.invoke(file_management, ["organize", str(tmp_path), "-n"])
    assert result.exit_code == 0
    assert (tmp_path / "a.txt").exists()
    assert not (tmp_path / "txt").exists()


def test_file_find_replace(runner, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("aaa bbb aaa", encoding="utf-8")
    result = runner.invoke(
        file_management, ["file-find-replace", "-p", str(target), "-f", "aaa", "-r", "ccc"]
    )
    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == "ccc bbb ccc"


def test_generate_text_file(runner, tmp_path):
    result = runner.invoke(
        file_management, ["generate-text-file", "-d", str(tmp_path), "-n", "3", "-l", "2"]
    )
    assert result.exit_code == 0, result.output
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["file-1.txt", "file-2.txt", "file-3.txt"]
    assert len((tmp_path / "file-1.txt").read_text(encoding="utf-8").splitlines()) == 2


def test_extract_links_to_stdout(runner, tmp_path):
    page = tmp_path / "page.html"
    page.write_text(
        '<a href="https://example.com/a/page">x</a> <a href="https://example.com/a/page">y</a>',
        encoding="utf-8",
    )
    result = runner.invoke(
        file_management, ["extract-links", "-s", str(page), "--stdout", "--unique"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "https://example.com/a/page"
