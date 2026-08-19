"""Tests for the tree renderer, the summary and the field filter."""

from __future__ import annotations

import pytest

from pytoolbox.dataset import render, schema, select, sources, summarize
from pytoolbox.dataset.errors import DataError


@pytest.fixture
def records():
    return [
        {"id": 1, "name": "ann", "age": 34, "tags": ["a", "b"], "at": {"city": "Berlin"}},
        {"id": 2, "name": "bob", "age": 28.5, "tags": [], "at": {"city": "Rome"}, "note": "hi"},
        {"id": 3, "name": "cy", "age": None, "tags": ["c"], "at": {"city": "Oslo"}},
    ]


@pytest.fixture
def root(records):
    return schema.infer(records, ["id", "name", "age", "tags", "at", "note"])


@pytest.fixture
def columns(root):
    return schema.columns_of(root)


@pytest.fixture
def source(records):
    return sources.RecordSource(
        records=records, columns=list(records[0]), origin="api.json", kind="json", root="data.users"
    )


# ── the tree ────────────────────────────────────────────────────────


def test_tree_lists_every_top_level_field(root):
    text = render.render(root)
    for name in ("id", "name", "age", "tags", "at", "note"):
        assert name in text


def test_tree_shows_types_and_counts(root):
    lines = {line.split()[1]: line for line in render.render(root).splitlines()}
    assert "int" in lines["id"]
    assert "2/3" in lines["age"]
    assert "nullable" in lines["age"]
    assert "nullable" not in lines["id"]


def test_tree_descends_into_nested_objects(root):
    assert "city" in render.render(root)


def test_tree_does_not_expand_a_list_of_scalars(root):
    """``list[str]`` already says what the elements are."""
    assert "list[str]" in render.render(root)
    assert "[]" not in render.render(root)


def test_tree_expands_a_list_of_objects():
    root = schema.infer([{"orders": [{"sku": "x"}]}], ["orders"])
    text = render.render(root)
    assert "[]" in text
    assert "sku" in text


def test_depth_stops_the_descent(root):
    assert "city" not in render.render(root, max_depth=1)


def test_the_last_child_uses_a_corner(root):
    lines = render.render(root).splitlines()
    assert lines[-1].startswith("`-- ")


def test_header_names_the_source_and_the_root(source, root):
    text = render.header(source, root)
    assert "api.json" in text
    assert "--root data.users" in text
    assert "list of 3 objects, 6 keys" in text


def test_header_of_a_single_object():
    records = [{"a": 1}]
    source = sources.RecordSource(records, ["a"], "cfg.json", "json", single=True)
    text = render.header(source, schema.infer(records, ["a"]))
    assert "one object, 1 keys" in text


# ── the summary ─────────────────────────────────────────────────────


def test_summary_has_one_row_per_field(root, columns):
    rows = summarize.summarize(root, columns)
    assert [row["field"] for row in rows] == ["id", "name", "age", "tags", "at", "note"]


def test_summary_counts_nulls_and_missing_keys(root, columns):
    rows = {row["field"]: row for row in summarize.summarize(root, columns)}
    assert rows["age"]["non_null"] == 2
    assert rows["age"]["nulls"] == 1
    assert rows["note"]["nulls"] == 2


def test_summary_gives_numbers_their_extremes(root, columns):
    row = {row["field"]: row for row in summarize.summarize(root, columns)}["age"]
    assert row["min"] == "28.5"
    assert row["max"] == "34"
    assert row["mean"] == "31.25"


def test_summary_measures_text_by_length(root, columns):
    row = {row["field"]: row for row in summarize.summarize(root, columns)}["name"]
    assert row["min"] == "len 2"
    assert row["max"] == "len 3"


def test_summary_measures_containers_by_size(root, columns):
    row = {row["field"]: row for row in summarize.summarize(root, columns)}["tags"]
    assert row["min"] == "0 items"
    assert row["max"] == "2 items"


def test_summary_counts_booleans():
    records = [{"ok": True}, {"ok": True}, {"ok": False}]
    root = schema.infer(records, ["ok"])
    row = summarize.summarize(root, schema.columns_of(root))[0]
    assert row["mean"] == "2 true / 1 false"
    assert row["top"] == "true (2)"


def test_summary_counts_distinct_containers():
    records = [{"t": ["a"]}, {"t": ["a"]}, {"t": ["b"]}]
    root = schema.infer(records, ["t"])
    assert summarize.summarize(root, schema.columns_of(root))[0]["distinct"] == 2


def test_summary_shows_the_sql_column_name():
    records = [{"First Name": "ann"}]
    root = schema.infer(records, ["First Name"])
    row = summarize.summarize(root, schema.columns_of(root))[0]
    assert row["field"] == "First Name"
    assert row["column"] == "first_name"


def test_a_value_seen_once_is_not_called_a_top_value(root, columns):
    assert {row["field"]: row for row in summarize.summarize(root, columns)}["id"]["top"] == ""


# ── the filter ──────────────────────────────────────────────────────


def test_key_globs_match_the_original_and_the_column_name():
    records = [{"First Name": "ann", "id": 1}]
    root = schema.infer(records, ["First Name", "id"])
    columns = schema.columns_of(root)
    assert [c.name for c in select.select(root, columns, keys=["first*"])] == ["first_name"]
    assert [c.name for c in select.select(root, columns, keys=["First*"])] == ["first_name"]


def test_types_are_or_ed_together(root, columns):
    chosen = select.select(root, columns, types=["int", "float"])
    assert [column.name for column in chosen] == ["id", "age"]


def test_keys_and_types_narrow_together(root, columns):
    chosen = select.select(root, columns, keys=["a*"], types=["float"])
    assert [column.name for column in chosen] == ["age"]


def test_json_matches_every_container(root, columns):
    chosen = select.select(root, columns, types=["json"])
    assert [column.name for column in chosen] == ["tags", "at"]


def test_mixed_matches_a_field_with_more_than_one_type(root, columns):
    assert [column.name for column in select.select(root, columns, types=["mixed"])] == ["age"]


def test_drop_empty_removes_all_null_fields():
    records = [{"a": 1, "b": None}, {"a": 2, "b": None}]
    root = schema.infer(records, ["a", "b"])
    columns = schema.columns_of(root)
    assert [c.name for c in select.select(root, columns, drop_empty=True)] == ["a"]


def test_an_unknown_type_lists_the_known_ones(root, columns):
    with pytest.raises(DataError, match="Known types"):
        select.select(root, columns, types=["integer"])


def test_matching_nothing_is_an_error(root, columns):
    with pytest.raises(DataError, match="No field matched"):
        select.select(root, columns, keys=["zzz*"])


def test_rows_render_containers_as_compact_json(records, root, columns):
    rows = select.rows_for(records, columns)
    assert rows[0]["tags"] == '["a","b"]'
    assert rows[0]["at"] == '{"city":"Berlin"}'


def test_rows_render_booleans_in_lower_case():
    assert select.display(True) == "true"
    assert select.display(False) == "false"


def test_rows_can_be_capped(records, root, columns):
    assert len(select.rows_for(records, columns, limit=2)) == 2
