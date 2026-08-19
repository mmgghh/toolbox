"""Tests for reading JSON, CSV and Excel, and for finding the rows."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from pytoolbox.dataset import readers, sources
from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.types import ValueType

openpyxl = pytest.importorskip("openpyxl", reason="Excel support is an optional extra")


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ── JSON ────────────────────────────────────────────────────────────


def test_a_top_level_list_is_the_rows(tmp_path):
    path = write(tmp_path, "a.json", json.dumps([{"a": 1}, {"a": 2}]))
    source = sources.load(path)
    assert len(source) == 2
    assert source.root == ""
    assert not source.single


def test_a_top_level_object_is_one_row(tmp_path):
    path = write(tmp_path, "a.json", json.dumps({"a": 1, "b": {"c": 2}}))
    source = sources.load(path)
    assert source.records == [{"a": 1, "b": {"c": 2}}]
    assert source.single


def test_a_single_nested_list_is_found_and_reported(tmp_path):
    document = {"meta": {"page": 1}, "data": {"items": [{"a": 1}, {"a": 2}]}}
    path = write(tmp_path, "a.json", json.dumps(document))
    source = sources.load(path)
    assert source.root == "data.items"
    assert len(source) == 2
    assert any("data.items" in note for note in source.notes)


def test_several_candidate_lists_are_an_error_that_names_them(tmp_path):
    document = {"users": [{"a": 1}], "orders": [{"b": 2}]}
    path = write(tmp_path, "a.json", json.dumps(document))
    with pytest.raises(DataError) as excinfo:
        sources.load(path)
    message = str(excinfo.value)
    assert "--root users" in message
    assert "--root orders" in message


def test_root_picks_a_list_explicitly(tmp_path):
    document = {"users": [{"a": 1}], "orders": [{"b": 2}, {"b": 3}]}
    path = write(tmp_path, "a.json", json.dumps(document))
    assert len(sources.load(path, root="orders")) == 2


def test_root_can_point_at_an_object(tmp_path):
    path = write(tmp_path, "a.json", json.dumps({"cfg": {"x": 1}}))
    source = sources.load(path, root="cfg")
    assert source.single
    assert source.records == [{"x": 1}]


def test_an_unknown_root_names_the_missing_segment(tmp_path):
    path = write(tmp_path, "a.json", json.dumps({"a": {"b": [{"c": 1}]}}))
    with pytest.raises(DataError, match="No key 'nope'"):
        sources.load(path, root="a.nope")


def test_a_list_of_scalars_becomes_one_column(tmp_path):
    path = write(tmp_path, "a.json", json.dumps([1, 2, 3]))
    source = sources.load(path)
    assert source.records == [{"value": 1}, {"value": 2}, {"value": 3}]


def test_mixed_scalars_and_objects_are_wrapped(tmp_path):
    path = write(tmp_path, "a.json", json.dumps([{"a": 1}, 2]))
    assert sources.load(path).records == [{"a": 1}, {"value": 2}]


def test_an_empty_list_is_an_error(tmp_path):
    path = write(tmp_path, "a.json", "[]")
    with pytest.raises(DataError, match="empty list"):
        sources.load(path)


def test_broken_json_reports_where(tmp_path):
    path = write(tmp_path, "a.json", '{"a": }')
    with pytest.raises(DataError, match="Not valid JSON"):
        sources.load(path)


def test_newline_delimited_json_is_read_as_a_list(tmp_path):
    path = write(tmp_path, "a.json", '{"a": 1}\n{"a": 2}\n')
    assert len(sources.load(path)) == 2


def test_limit_stops_early_and_says_so(tmp_path):
    path = write(tmp_path, "a.json", json.dumps([{"a": i} for i in range(10)]))
    source = sources.load(path, limit=3)
    assert len(source) == 3
    assert any("--limit 3" in note for note in source.notes)


def test_an_unknown_suffix_asks_for_from(tmp_path):
    path = write(tmp_path, "a.dat", "[]")
    with pytest.raises(DataError, match="--from"):
        sources.load(path)


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(DataError, match="No such file"):
        sources.load(tmp_path / "nope.json")


# ── CSV ─────────────────────────────────────────────────────────────


def test_csv_infers_a_type_per_column(tmp_path):
    path = write(
        tmp_path,
        "a.csv",
        "id,age,joined,active,zip\n1,34,2024-01-05,true,01730\n2,,2024-03-11,false,10115\n",
    )
    source = sources.load(path)
    first = source.records[0]
    assert first["id"] == 1
    assert first["joined"] == dt.date(2024, 1, 5)
    assert first["active"] is True
    assert first["zip"] == "01730"
    assert source.records[1]["age"] is None


def test_one_bad_cell_keeps_the_whole_column_as_text(tmp_path):
    path = write(tmp_path, "a.csv", "n\n1\n2\nabc\n")
    assert [record["n"] for record in sources.load(path).records] == ["1", "2", "abc"]


def test_no_infer_keeps_everything_as_text(tmp_path):
    path = write(tmp_path, "a.csv", "n,m\n1,2\n")
    assert sources.load(path, infer=False).records == [{"n": "1", "m": "2"}]


def test_csv_delimiter_is_sniffed(tmp_path):
    path = write(tmp_path, "a.csv", "a;b\n1;2\n")
    assert sources.load(path).records == [{"a": 1, "b": 2}]


def test_tsv_uses_tabs(tmp_path):
    path = write(tmp_path, "a.tsv", "a\tb\n1\t2\n")
    assert sources.load(path).records == [{"a": 1, "b": 2}]


def test_blank_headers_are_named(tmp_path):
    path = write(tmp_path, "a.csv", "a,,b\n1,2,3\n")
    assert list(sources.load(path).records[0]) == ["a", "column_2", "b"]


def test_duplicate_headers_are_separated(tmp_path):
    path = write(tmp_path, "a.csv", "a,a\n1,2\n")
    assert list(sources.load(path).records[0]) == ["a", "a_2"]


def test_short_rows_are_padded(tmp_path):
    path = write(tmp_path, "a.csv", "a,b,c\n1,2\n")
    assert sources.load(path).records[0]["c"] is None


def test_a_header_with_no_rows_is_an_error(tmp_path):
    path = write(tmp_path, "a.csv", "a,b\n")
    with pytest.raises(DataError, match="no data rows"):
        sources.load(path)


def test_root_is_rejected_for_csv(tmp_path):
    path = write(tmp_path, "a.csv", "a\n1\n")
    with pytest.raises(DataError, match="JSON only"):
        sources.load(path, root="x")


# ── Excel ───────────────────────────────────────────────────────────


@pytest.fixture
def workbook(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Q1"
    sheet.append(["ID", "Name", "Hired", "Pay"])
    sheet.append([1, "ann", dt.datetime(2024, 1, 5), 5200.5])
    sheet.append([2, "bob", None, 4100])
    other = wb.create_sheet("Q2")
    other.append(["ID"])
    other.append([9])
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return path


def test_excel_keeps_the_types_the_sheet_recorded(workbook):
    source = sources.load(workbook)
    assert source.records[0]["ID"] == 1
    assert source.records[0]["Hired"] == dt.datetime(2024, 1, 5)
    assert source.records[1]["Hired"] is None


def test_excel_reads_the_active_sheet_by_default(workbook):
    assert len(sources.load(workbook)) == 2


def test_excel_sheet_can_be_chosen(workbook):
    assert sources.load(workbook, sheet="Q2").records == [{"ID": 9}]


def test_an_unknown_sheet_lists_the_real_ones(workbook):
    with pytest.raises(DataError, match="Q1, Q2"):
        sources.load(workbook, sheet="Nope")


def test_excel_no_infer_flattens_to_text(workbook):
    assert sources.load(workbook, infer=False).records[0]["ID"] == "1"


# ── shared ──────────────────────────────────────────────────────────


def test_detect_kind():
    from pathlib import Path

    assert readers.detect_kind(Path("a.JSON")) == "json"
    assert readers.detect_kind(Path("a.xlsx")) == "excel"
    assert readers.detect_kind(Path("a.bin")) is None


def test_find_candidates_does_not_descend_into_lists():
    document = {"users": [{"orders": [{"id": 1}]}]}
    paths = [candidate.path for candidate in sources.find_candidates(document)]
    assert paths == ["users"]


def test_ordered_columns_follows_first_appearance():
    records = [{"b": 1}, {"a": 2, "b": 3}]
    assert sources.ordered_columns(records) == ["b", "a"]


def test_value_types_survive_the_round_trip(tmp_path):
    path = write(tmp_path, "a.csv", "n\n1.5\n")
    from pytoolbox.dataset.types import classify

    assert classify(sources.load(path).records[0]["n"]) is ValueType.FLOAT
