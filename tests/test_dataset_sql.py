"""Tests for the SQL dialects, the script emitter and the SQLite back end."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from pytoolbox.dataset import schema
from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.sql import dialects, emit, execute
from pytoolbox.dataset.sql import table as table_module
from pytoolbox.dataset.types import ValueType


@pytest.fixture
def records():
    return [
        {"id": 1, "name": "ann", "score": 1.5, "ok": True, "tags": ["a"], "note": None},
        {"id": 2, "name": "bob", "score": 2, "ok": False, "tags": [], "note": "hi"},
    ]


@pytest.fixture
def columns(records):
    root = schema.infer(records, ["id", "name", "score", "ok", "tags", "note"])
    return schema.columns_of(root)


@pytest.fixture
def spec():
    return table_module.build_spec("people", primary_key=["id"], indexes=["name"])


# ── dialects ────────────────────────────────────────────────────────


def test_sqlite_types():
    sqlite = dialects.get("sqlite")
    assert sqlite.sql_type(ValueType.INT) == "INTEGER"
    assert sqlite.sql_type(ValueType.BOOL) == "INTEGER"
    assert sqlite.sql_type(ValueType.DATE) == "TEXT"
    assert sqlite.sql_type(ValueType.JSON) == "TEXT"


def test_postgres_types():
    postgres = dialects.get("postgres")
    assert postgres.sql_type(ValueType.INT) == "bigint"
    assert postgres.sql_type(ValueType.BOOL) == "boolean"
    assert postgres.sql_type(ValueType.DATETIME) == "timestamp"
    assert postgres.sql_type(ValueType.JSON) == "jsonb"
    assert postgres.sql_type(ValueType.JSON, nested="text") == "text"


def test_nested_is_ignored_on_sqlite():
    """SQLite has no JSON column type, so --nested cannot change anything."""
    assert dialects.get("sqlite").sql_type(ValueType.JSON, nested="text") == "TEXT"


def test_an_integer_wider_than_64_bits_gets_a_wider_type():
    assert dialects.get("postgres").sql_type(ValueType.INT, wide=True) == "numeric"
    assert dialects.get("sqlite").sql_type(ValueType.INT, wide=True) == "TEXT"


def test_unknown_dialect_lists_the_known_ones():
    with pytest.raises(DataError, match="postgres, sqlite"):
        dialects.get("oracle")


@pytest.mark.parametrize(
    ("value", "sqlite_text", "postgres_text"),
    [
        (None, "NULL", "NULL"),
        (True, "1", "TRUE"),
        (False, "0", "FALSE"),
        (7, "7", "7"),
        (1.5, "1.5", "1.5"),
        ("x", "'x'", "'x'"),
        ("it's", "'it''s'", "'it''s'"),
    ],
)
def test_literals(value, sqlite_text, postgres_text):
    assert dialects.get("sqlite").literal(value, ValueType.STR) == sqlite_text
    assert dialects.get("postgres").literal(value, ValueType.STR) == postgres_text


def test_json_literals_are_cast_on_postgres():
    assert dialects.get("postgres").literal({"a": 1}, ValueType.JSON) == '\'{"a":1}\'::jsonb'
    assert dialects.get("sqlite").literal({"a": 1}, ValueType.JSON) == '\'{"a":1}\''


def test_json_literal_is_not_cast_when_nested_is_text():
    literal = dialects.get("postgres").literal({"a": 1}, ValueType.JSON, nested="text")
    assert literal == '\'{"a":1}\''


def test_dates_become_iso_strings():
    literal = dialects.get("postgres").literal(dt.date(2024, 1, 5), ValueType.DATE)
    assert literal == "'2024-01-05'"


def test_nan_and_infinity_become_null():
    """Neither dialect has a portable spelling for them."""
    postgres = dialects.get("postgres")
    assert postgres.literal(float("nan"), ValueType.FLOAT) == "NULL"
    assert postgres.literal(float("inf"), ValueType.FLOAT) == "NULL"


def test_identifiers_are_quoted_and_escaped():
    assert dialects.get("postgres").quote('we"ird') == '"we""ird"'


def test_adapt_for_parameter_binding():
    assert dialects.adapt(True) == 1
    assert dialects.adapt({"a": 1}) == '{"a":1}'
    assert dialects.adapt(dt.date(2024, 1, 5)) == "2024-01-05"
    assert dialects.adapt(2**70) == str(2**70)


def test_is_wide():
    assert dialects.is_wide(2**70)
    assert not dialects.is_wide(5)
    assert not dialects.is_wide(True)


# ── the spec and its validation ─────────────────────────────────────


def test_a_table_name_is_sanitized_with_a_note():
    spec = table_module.build_spec("My Table")
    assert spec.name == "my_table"
    assert spec.notes


def test_an_empty_table_name_is_an_error():
    with pytest.raises(DataError, match="table name is required"):
        table_module.build_spec("  ")


def test_index_groups_split_on_commas():
    spec = table_module.build_spec("t", indexes=["a,b", "c"])
    assert spec.indexes == (("a", "b"), ("c",))


def test_parse_renames():
    assert table_module.parse_renames(["First Name=full_name"]) == {"First Name": "full_name"}


def test_parse_renames_folds_the_new_name_by_default():
    assert table_module.parse_renames(["a=Full Name"]) == {"a": "full_name"}


def test_parse_renames_keeps_the_new_name_verbatim_when_raw():
    assert table_module.parse_renames(["a=Full Name"], raw=True) == {"a": "Full Name"}


def test_a_rename_without_an_equals_is_an_error():
    with pytest.raises(DataError, match="OLD=NEW"):
        table_module.parse_renames(["oops"])


def test_an_unknown_primary_key_suggests_a_real_column(columns, records):
    spec = table_module.build_spec("t", primary_key=["idd"])
    with pytest.raises(DataError, match="Did you mean: id"):
        table_module.validate(spec, columns, records)


def test_an_unknown_index_column_is_an_error(columns, records):
    spec = table_module.build_spec("t", indexes=["nope"])
    with pytest.raises(DataError, match="Index column 'nope'"):
        table_module.validate(spec, columns, records)


def test_a_repeated_primary_key_is_caught_before_inserting():
    records = [{"team": "red"}, {"team": "blue"}, {"team": "red"}]
    columns = schema.columns_of(schema.infer(records, ["team"]))
    spec = table_module.build_spec("t", primary_key=["team"])
    with pytest.raises(DataError, match="not unique"):
        table_module.validate(spec, columns, records)


def test_a_primary_key_with_missing_values_is_an_error(columns, records):
    spec = table_module.build_spec("t", primary_key=["note"])
    with pytest.raises(DataError, match="missing values"):
        table_module.validate(spec, columns, records)


def test_a_compound_primary_key_is_checked_as_a_whole(records, columns):
    spec = table_module.build_spec("t", primary_key=["id", "name"])
    table_module.validate(spec, columns, records)


def test_two_fields_renamed_onto_one_column_are_an_error(records):
    root = schema.infer(records, ["id", "name"])
    columns = schema.columns_of(root, {"name": "id"})
    spec = table_module.build_spec("t")
    with pytest.raises(DataError, match="Two fields map"):
        table_module.validate(spec, columns, records)


def test_wide_columns_are_spotted():
    records = [{"n": 2**70}]
    columns = schema.columns_of(schema.infer(records, ["n"]))
    assert table_module.wide_columns(columns, records) == {"n"}


def test_default_table_name_comes_from_the_root_then_the_filename():
    assert table_module.default_table_name("/tmp/api.json", "data.users") == "users"
    assert table_module.default_table_name("/tmp/My File.csv") == "my_file"
    assert table_module.default_table_name("stdin") is None


# ── the script emitter ──────────────────────────────────────────────


def test_create_table_is_aligned_and_quoted(spec, columns, records):
    script = emit.build(spec, columns, records, dialects.get("postgres"))
    assert 'CREATE TABLE "people" (' in script
    assert '"id"    bigint NOT NULL' in script
    assert '"tags"  jsonb NOT NULL' in script
    assert 'PRIMARY KEY ("id")' in script


def test_a_field_missing_from_some_records_is_nullable(spec, columns, records):
    script = emit.build(spec, columns, records, dialects.get("postgres"))
    assert '"note"  text,' in script


def test_a_primary_key_column_is_never_nullable(records):
    records = [{"id": 1}, {"id": 2, "extra": 3}]
    columns = schema.columns_of(schema.infer(records, ["id", "extra"]))
    spec = table_module.build_spec("t", primary_key=["extra"])
    script = emit.create_table(spec, columns, dialects.get("postgres"))
    assert '"extra" bigint NOT NULL' in script


def test_indexes_are_named_after_the_table_and_columns(columns):
    spec = table_module.build_spec("people", indexes=["name,score"], unique_indexes=["name"])
    script = emit.create_indexes(spec, dialects.get("postgres"))
    assert 'CREATE INDEX "idx_people_name_score" ON "people" ("name", "score");' in script
    assert 'CREATE UNIQUE INDEX "idx_people_name" ON "people" ("name");' in script


def test_replace_drops_first(columns, records):
    spec = table_module.build_spec("t", if_exists="replace")
    script = emit.build(spec, columns, records, dialects.get("sqlite"))
    assert script.index("DROP TABLE IF EXISTS") < script.index("CREATE TABLE")


def test_append_creates_only_if_missing(columns, records):
    spec = table_module.build_spec("t", if_exists="append")
    script = emit.build(spec, columns, records, dialects.get("sqlite"))
    assert "CREATE TABLE IF NOT EXISTS" in script
    assert "DROP TABLE" not in script


def test_inserts_are_batched(columns):
    records = [{"id": index} for index in range(5)]
    columns = schema.columns_of(schema.infer(records, ["id"]))
    spec = table_module.build_spec("t", batch=2)
    script = emit.inserts(spec, columns, records, dialects.get("sqlite"))
    assert script.count("INSERT INTO") == 3


def test_inserts_are_wrapped_in_one_transaction(spec, columns, records):
    script = emit.build(spec, columns, records, dialects.get("sqlite"))
    assert script.count("BEGIN;") == 1
    assert script.rstrip().endswith("COMMIT;")


def test_the_banner_names_the_source(spec, columns, records):
    script = emit.build(
        spec, columns, records, dialects.get("postgres"), origin="api.json", root="data.users"
    )
    assert "-- Generated by pytoolbox pydata from api.json (--root data.users)." in script
    assert "Dialect: postgres. 2 rows, 6 columns." in script


# ── the SQLite back end ─────────────────────────────────────────────


def test_write_creates_the_table_and_the_rows(tmp_path, spec, columns, records):
    db = tmp_path / "app.db"
    assert execute.write(db, spec, columns, records) == 2

    connection = sqlite3.connect(db)
    rows = connection.execute("SELECT id, name, ok, tags, note FROM people ORDER BY id").fetchall()
    connection.close()
    assert rows == [(1, "ann", 1, '["a"]', None), (2, "bob", 0, "[]", "hi")]


def test_write_creates_the_indexes(tmp_path, spec, columns, records):
    db = tmp_path / "app.db"
    execute.write(db, spec, columns, records)
    connection = sqlite3.connect(db)
    names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")]
    connection.close()
    assert "idx_people_name" in names


def test_write_refuses_an_existing_table_by_default(tmp_path, spec, columns, records):
    db = tmp_path / "app.db"
    execute.write(db, spec, columns, records)
    with pytest.raises(DataError, match="already exists"):
        execute.write(db, spec, columns, records)


def test_replace_rebuilds_the_table(tmp_path, columns, records):
    db = tmp_path / "app.db"
    execute.write(db, table_module.build_spec("t"), columns, records)
    spec = table_module.build_spec("t", if_exists="replace")
    execute.write(db, spec, columns, records)
    connection = sqlite3.connect(db)
    count = connection.execute("SELECT count(*) FROM t").fetchone()[0]
    connection.close()
    assert count == 2


def test_append_adds_rows_to_the_existing_table(tmp_path, columns, records):
    db = tmp_path / "app.db"
    execute.write(db, table_module.build_spec("t"), columns, records)
    execute.write(db, table_module.build_spec("t", if_exists="append"), columns, records)
    connection = sqlite3.connect(db)
    count = connection.execute("SELECT count(*) FROM t").fetchone()[0]
    connection.close()
    assert count == 4


def test_write_creates_missing_parent_directories(tmp_path, spec, columns, records):
    db = tmp_path / "nested" / "deeper" / "app.db"
    execute.write(db, spec, columns, records)
    assert db.exists()


def test_a_generated_sqlite_script_actually_runs(tmp_path, spec, columns, records):
    """The emitter and the back end must agree on what SQLite accepts."""
    script = emit.build(spec, columns, records, dialects.get("sqlite"))
    connection = sqlite3.connect(tmp_path / "from_script.db")
    connection.executescript(script)
    rows = connection.execute("SELECT count(*) FROM people").fetchone()[0]
    connection.close()
    assert rows == 2


def test_a_quote_in_a_value_survives_the_script(tmp_path):
    records = [{"note": "it's a 'test'"}]
    columns = schema.columns_of(schema.infer(records, ["note"]))
    spec = table_module.build_spec("t")
    script = emit.build(spec, columns, records, dialects.get("sqlite"))
    connection = sqlite3.connect(tmp_path / "quotes.db")
    connection.executescript(script)
    value = connection.execute("SELECT note FROM t").fetchone()[0]
    connection.close()
    assert value == "it's a 'test'"
