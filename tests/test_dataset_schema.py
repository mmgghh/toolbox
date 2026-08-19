"""Tests for the type lattice, identifier naming and schema inference."""

from __future__ import annotations

import datetime as dt

import pytest

from pytoolbox.dataset import naming, schema
from pytoolbox.dataset.types import ValueType, classify, parse_text, unify, unify_all

# ── the type lattice ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ValueType.NULL),
        (True, ValueType.BOOL),
        (False, ValueType.BOOL),
        (3, ValueType.INT),
        (3.5, ValueType.FLOAT),
        ("x", ValueType.STR),
        ([1], ValueType.LIST),
        ({"a": 1}, ValueType.OBJECT),
        (dt.date(2024, 1, 1), ValueType.DATE),
        (dt.datetime(2024, 1, 1, 12), ValueType.DATETIME),
    ],
)
def test_classify(value, expected):
    assert classify(value) is expected


def test_bool_is_not_an_int():
    """bool subclasses int, so the order of the isinstance checks matters."""
    assert classify(True) is ValueType.BOOL


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (ValueType.INT, ValueType.INT, ValueType.INT),
        (ValueType.NULL, ValueType.INT, ValueType.INT),
        (ValueType.INT, ValueType.NULL, ValueType.INT),
        (ValueType.INT, ValueType.FLOAT, ValueType.FLOAT),
        (ValueType.DATE, ValueType.DATETIME, ValueType.DATETIME),
        (ValueType.LIST, ValueType.OBJECT, ValueType.JSON),
        (ValueType.LIST, ValueType.STR, ValueType.JSON),
        (ValueType.INT, ValueType.STR, ValueType.STR),
        (ValueType.BOOL, ValueType.INT, ValueType.STR),
    ],
)
def test_unify(left, right, expected):
    assert unify(left, right) is expected
    assert unify(right, left) is expected


def test_unify_all_ignores_null():
    assert unify_all([ValueType.NULL, ValueType.INT, ValueType.NULL]) is ValueType.INT


def test_unify_all_of_nothing_is_null():
    assert unify_all([]) is ValueType.NULL


# ── inferring types from CSV text ───────────────────────────────────


@pytest.mark.parametrize(
    ("text", "value_type", "value"),
    [
        ("", ValueType.NULL, None),
        ("  ", ValueType.NULL, None),
        ("n/a", ValueType.NULL, None),
        ("true", ValueType.BOOL, True),
        ("FALSE", ValueType.BOOL, False),
        ("yes", ValueType.BOOL, True),
        ("42", ValueType.INT, 42),
        ("-7", ValueType.INT, -7),
        ("3.5", ValueType.FLOAT, 3.5),
        ("1e3", ValueType.FLOAT, 1000.0),
        ("2024-01-05", ValueType.DATE, dt.date(2024, 1, 5)),
        ("2024-01-05T10:30:00", ValueType.DATETIME, dt.datetime(2024, 1, 5, 10, 30)),
        ("hello", ValueType.STR, "hello"),
    ],
)
def test_parse_text(text, value_type, value):
    assert parse_text(text) == (value_type, value)


@pytest.mark.parametrize("text", ["01730", "007", "-0012"])
def test_leading_zeros_stay_text(text):
    """Postcodes and part numbers are identifiers, not numbers."""
    assert parse_text(text)[0] is ValueType.STR


def test_trailing_z_datetime():
    value_type, value = parse_text("2024-01-05T10:30:00Z")
    assert value_type is ValueType.DATETIME
    assert value.utcoffset() == dt.timedelta(0)


# ── identifiers ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("First Name", "first_name"),
        ("userID", "user_id"),
        ("user.id", "user_id"),
        ("Prénom", "prenom"),
        ("  spaced  ", "spaced"),
        ("2024", "_2024"),
        ("!!!", "column"),
        ("a--b", "a_b"),
        ("HTTPStatus", "http_status"),
    ],
)
def test_sanitize(raw, expected):
    assert naming.sanitize(raw) == expected


def test_sanitize_truncates_to_postgres_limit():
    assert len(naming.sanitize("x" * 200)) == naming.MAX_BYTES


# ── scripts other than Latin ────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("نام واحد", "نام_واحد"),
        ("کد پستی", "کد_پستی"),
        ("انبار/سردخانه", "انبار_سردخانه"),
        ("ای دی واحد", "ای_دی_واحد"),
        ("日本語 の 列", "日本語_の_列"),
        ("한국어 이름", "한국어_이름"),
        ("ελληνικά", "ελληνικά"),
    ],
)
def test_a_non_latin_key_keeps_its_letters(raw, expected):
    """Deleting a script that has no ASCII spelling loses the column name."""
    assert naming.sanitize(raw) == expected


def test_non_latin_keys_do_not_all_collapse_together():
    headers = ["نام واحد", "کد پستی", "ظرفیت", "استان"]
    assert len(set(naming.unique(headers))) == len(headers)
    assert naming.FALLBACK not in naming.unique(headers)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Prénom", "prenom"), ("naïve café", "naive_cafe"), ("tiếng Việt", "tieng_viet")],
)
def test_latin_accents_are_still_folded(raw, expected):
    assert naming.sanitize(raw) == expected


def test_a_mark_that_stands_alone_survives():
    """A Devanagari virama has no Latin spelling; dropping it changes the word."""
    assert naming.sanitize("हिन्दी") == "हिन्दी"


def test_a_persian_zero_width_non_joiner_separates_words():
    assert naming.sanitize("می\u200cشود") == "می_شود"


def test_truncation_counts_bytes_not_characters():
    """PostgreSQL's limit is NAMEDATALEN - 1 bytes, so Persian runs out sooner."""
    name = naming.sanitize("ت" * 100)
    assert len(name.encode("utf-8")) <= naming.MAX_BYTES
    assert len(name) < 100


def test_truncation_never_splits_a_character():
    naming.sanitize("ن" * 100).encode("utf-8").decode("utf-8")


def test_unique_breaks_collisions():
    assert naming.unique(["Name", "name", "NAME"]) == ["name", "name_2", "name_3"]


def test_unique_skips_a_taken_suffix():
    assert naming.unique(["a", "a_2", "a"]) == ["a", "a_2", "a_3"]


# ── verbatim identifiers (--raw-names) ──────────────────────────────


@pytest.mark.parametrize("raw", ["First Name", "userID", "Prénom", "2024", "order"])
def test_raw_keeps_a_key_exactly(raw):
    """Every identifier is emitted quoted, so any key is legal as it stands."""
    assert naming.as_identifier(raw, raw=True) == raw


def test_raw_still_names_an_empty_key():
    assert naming.as_identifier("   ", raw=True) == naming.FALLBACK


def test_raw_still_truncates_to_the_postgres_limit():
    """PostgreSQL truncates silently; doing it here is what catches the clash."""
    assert len(naming.as_identifier("x" * 200, raw=True)) == naming.MAX_BYTES


def test_raw_keeps_keys_that_differ_only_in_case_apart():
    assert naming.unique(["Name", "name"], raw=True) == ["Name", "name"]


def test_raw_still_breaks_a_real_collision():
    long = "x" * naming.MAX_BYTES
    assert naming.unique([long + "a", long + "b"], raw=True) == [long, long + "_2"]


# ── schema inference ────────────────────────────────────────────────


@pytest.fixture
def records():
    return [
        {"id": 1, "name": "ann", "tags": ["a", "b"], "address": {"city": "Berlin"}},
        {"id": 2, "name": "bob", "tags": [], "address": {"city": "Rome", "zip": 20121}},
        {"id": 3, "name": None, "tags": ["c"], "address": {"city": "Oslo"}, "extra": 1},
    ]


@pytest.fixture
def root(records):
    return schema.infer(records, ["id", "name", "tags", "address", "extra"])


def test_infer_keeps_column_order(root):
    assert list(root.children) == ["id", "name", "tags", "address", "extra"]


def test_a_null_value_makes_a_field_nullable(root):
    assert root.children["name"].nulls == 1
    assert root.children["name"].nullable


def test_a_missing_key_makes_a_field_nullable(root):
    extra = root.children["extra"]
    assert extra.present == 1
    assert extra.total == 3
    assert extra.nullable
    assert extra.missing == 2


def test_a_field_present_everywhere_is_not_nullable(root):
    assert not root.children["id"].nullable


def test_nested_objects_get_their_own_children(root):
    address = root.children["address"]
    assert list(address.children) == ["city", "zip"]
    assert address.children["zip"].nullable


def test_list_elements_are_described(root):
    assert root.children["tags"].item.type is ValueType.STR
    assert root.children["tags"].type_label == "list[str]"


def test_type_label_shows_a_mixed_field():
    root = schema.infer([{"a": 1}, {"a": "x"}], ["a"])
    assert root.children["a"].type_label == "int|str"
    assert root.children["a"].type is ValueType.STR


def test_columns_collapse_containers_to_json(root):
    columns = {column.source: column for column in schema.columns_of(root)}
    assert columns["tags"].type is ValueType.JSON
    assert columns["address"].type is ValueType.JSON


def test_columns_sanitize_and_rename():
    root = schema.infer([{"First Name": "ann"}], ["First Name"])
    plain = schema.columns_of(root)[0]
    assert plain.name == "first_name"
    assert plain.renamed

    renamed = schema.columns_of(root, {"first_name": "full_name"})[0]
    assert renamed.name == "full_name"


def test_columns_can_keep_the_original_keys():
    root = schema.infer([{"First Name": "ann", "userID": 1}], ["First Name", "userID"])
    assert [column.name for column in schema.columns_of(root, raw=True)] == [
        "First Name",
        "userID",
    ]
    assert not schema.columns_of(root, raw=True)[0].renamed


def test_rename_accepts_the_original_key_too():
    root = schema.infer([{"First Name": "ann"}], ["First Name"])
    assert schema.columns_of(root, {"First Name": "full_name"})[0].name == "full_name"


def test_walk_visits_every_node(root):
    paths = {node.path for node in root.walk()}
    assert {"id", "address", "address.city", "tags", "tags.[]"} <= paths
