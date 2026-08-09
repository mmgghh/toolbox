"""Tests for the pystr text commands."""

from __future__ import annotations

import json

import pytest

from pytoolbox import pystr
from pytoolbox.pystr import str_cli

# ── pure functions ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("style", "text", "expected"),
    [
        ("snake", "Hello World", "hello_world"),
        ("snake", "someValue", "some_value"),
        ("kebab", "Hello World", "hello-world"),
        ("camel", "hello world", "helloWorld"),
        ("camel", "some-long_name", "someLongName"),
        ("pascal", "hello world", "HelloWorld"),
        ("slug", "Résumé of 2026!", "resume-of-2026"),
        ("upper", "abc", "ABC"),
        ("title", "hello wide world", "Hello Wide World"),
    ],
)
def test_case_converters(style, text, expected):
    assert pystr.CASE_CONVERTERS[style](text) == expected


def test_slugify_unicode_keeps_letters():
    assert pystr.slugify("سلام دنیا", allow_unicode=True) == "سلام-دنیا"


@pytest.mark.parametrize(
    ("scheme", "plain", "encoded"),
    [
        ("base64", "hello", "aGVsbG8="),
        ("base64url", "hi?>", "aGk_Pg=="),
        ("hex", "hi", "6869"),
        ("url", "a b&c", "a%20b%26c"),
        ("url-plus", "a b", "a+b"),
    ],
)
def test_encoding_roundtrip(scheme, plain, encoded):
    assert pystr._apply_encoding(plain, scheme, decode=False) == encoded
    assert pystr._apply_encoding(encoded, scheme, decode=True) == plain


def test_rot13_is_symmetric():
    assert pystr._apply_encoding("hello", "rot13", decode=False) == "uryyb"
    assert pystr._apply_encoding("uryyb", "rot13", decode=True) == "hello"


def test_translate_to_english_digits():
    assert pystr.translate_text("شماره ۱۲۳؟", "en") == "شماره 123?"


def test_translate_to_persian_letters():
    assert pystr.translate_text("كتاب", "fa") == "کتاب"


def test_normalize_text_folds_accents_and_arabic_digits():
    assert pystr.normalize_text("Résumé ١٢٣") == "Resume 123"


def test_normalize_text_drops_plain_hyphens():
    # The bundled rules map en/em dashes to '-' and then remove '-' entirely,
    # so normalizing is deliberately not idempotent for dashes.
    assert pystr.normalize_text("a — b") == "a - b"
    assert pystr.normalize_text("a - b") == "a  b"


def test_strip_ansi():
    assert pystr.strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_tag_patterns_match_expected_shapes():
    import re

    assert re.search(pystr.COMMON_TAG_PATTERNS["email"], "write to a.b@example.com now")
    assert re.search(pystr.COMMON_TAG_PATTERNS["ipv4"], "host 192.168.1.10 up")
    assert re.search(pystr.COMMON_TAG_PATTERNS["uuid"], "id 123e4567-e89b-12d3-a456-426614174000")


# ── CLI ─────────────────────────────────────────────────────────────

def test_search_in_directory(runner, tree):
    result = runner.invoke(str_cli, ["search", str(tree), "alpha"])
    assert result.exit_code == 0, result.output
    assert "one.txt" in result.output
    assert "three.txt" in result.output


def test_search_with_extension_filter(runner, tree):
    result = runner.invoke(str_cli, ["search", str(tree), "title", "-e", "md"])
    assert result.exit_code == 0
    assert "notes.md" in result.output
    assert "one.txt" not in result.output


def test_search_json(runner, tree):
    result = runner.invoke(str_cli, ["search", str(tree), "alpha", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["files_matched"] == 2
    assert payload["matches"] == 2


def test_search_text_only_matches(runner):
    result = runner.invoke(
        str_cli, ["search", "--tag", "email", "--only-matches", "--text", "ping a@b.com ok"]
    )
    assert result.exit_code == 0
    assert result.output.strip() == "a@b.com"


def test_search_requires_a_query(runner, tree):
    result = runner.invoke(str_cli, ["search", str(tree)])
    assert result.exit_code != 0
    assert "required" in result.stderr.lower()


def test_replace_dry_run_leaves_files_alone(runner, tree):
    result = runner.invoke(str_cli, ["replace", str(tree), "alpha", "omega", "--dry-run"])
    assert result.exit_code == 0
    assert "Total replacements: 2" in result.output
    assert (tree / "a" / "one.txt").read_text(encoding="utf-8") == "alpha"


def test_replace_applies_with_yes(runner, tree):
    result = runner.invoke(str_cli, ["replace", str(tree), "alpha", "omega", "--yes"])
    assert result.exit_code == 0, result.output
    assert (tree / "a" / "one.txt").read_text(encoding="utf-8") == "omega"


def test_replace_with_backup(runner, tree):
    result = runner.invoke(
        str_cli, ["replace", str(tree), "alpha", "omega", "--yes", "--backup"]
    )
    assert result.exit_code == 0
    assert (tree / "a" / "one.txt.bak").read_text(encoding="utf-8") == "alpha"


def test_case_command(runner):
    result = runner.invoke(str_cli, ["case", "--to", "snake", "--text", "Hello World"])
    assert result.exit_code == 0
    assert result.output.strip() == "hello_world"


def test_encode_and_decode_commands(runner):
    encoded = runner.invoke(str_cli, ["encode", "--text", "hello", "--as", "base64"])
    assert encoded.output.strip() == "aGVsbG8="
    decoded = runner.invoke(str_cli, ["decode", "--text", "aGVsbG8=", "--as", "base64"])
    assert decoded.output.strip() == "hello"


def test_decode_reports_bad_input(runner):
    result = runner.invoke(str_cli, ["decode", "--text", "zz!!", "--as", "hex"])
    assert result.exit_code != 0
    assert "could not decode" in result.stderr.lower()


def test_count_command_json(runner):
    result = runner.invoke(str_cli, ["count", "--stdin", "--json"], input="a b c\nd e\n")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["lines"] == 2
    assert payload["words"] == 5


def test_count_top_words(runner):
    result = runner.invoke(str_cli, ["count", "--text", "a a b", "--top", "1"])
    assert result.exit_code == 0
    assert result.output.strip().endswith("2  a")


def test_normalize_command(runner):
    result = runner.invoke(str_cli, ["normalize", "--text", "test"])
    assert result.exit_code == 0


def test_translate_command(runner):
    result = runner.invoke(str_cli, ["translate", "--to", "en", "--text", "۱۲۳"])
    assert result.exit_code == 0
    assert result.output.strip() == "123"


def test_translate_inplace(runner, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("۱۲۳", encoding="utf-8")
    result = runner.invoke(str_cli, ["translate", str(target), "--to", "en", "--inplace"])
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "123"
