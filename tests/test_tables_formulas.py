from __future__ import annotations

import csv
import io

import pytest

from pytoolbox.core.tables import render_csv, write_excel

HEADERS = ["task", "hours"]


@pytest.mark.parametrize("text", ["=HYPERLINK(\"http://evil\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"])
def test_csv_neutralizes_formula_cells(text):
    rows = [{"task": text, "hours": 1.5}]
    parsed = list(csv.reader(io.StringIO(render_csv(rows, HEADERS))))
    assert parsed[1] == ["'" + text, "1.5"]


@pytest.mark.parametrize("value", [-1.5, 3, "-1.5", "42", "plain", "", None])
def test_csv_leaves_numbers_and_plain_text_alone(value):
    parsed = list(csv.reader(io.StringIO(render_csv([{"task": value, "hours": 0}], HEADERS))))
    assert parsed[1][0] == ("" if value is None else str(value))


def test_excel_neutralizes_formula_cells(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "out.xlsx"
    write_excel(path, [{"task": "=1+1", "hours": -2.5}], HEADERS)
    sheet = openpyxl.load_workbook(path).active
    assert sheet["A2"].value == "'=1+1"
    assert sheet["A2"].data_type == "s"
    assert sheet["B2"].value == -2.5
