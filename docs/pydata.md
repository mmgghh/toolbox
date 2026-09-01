# `pydata` — JSON, CSV and Excel to a schema, a summary and a SQL table

Also available as `toolbox data`.

```shell
pydata tree api.json                       # what shape is this?
pydata summary sales.csv                   # what is in it?
pydata filter api.json --type int          # show me just the numbers
pydata count sales.csv                     # how many rows?
pydata keys staff.xlsx                     # what are the headers?
pydata sql sales.csv -t sales --db app.db  # load it into SQLite
pydata sql api.json -t users --dialect postgres --sql users.sql
pydata sql api.json -i --db app.db         # ask me about the table first
pydata edit sales.csv -c "First Name=full_name"   # fix a header in place
```

These subcommands read the same inferred structure, and `edit` writes names
back into the file. Nothing here needs a
database driver or an ORM: SQLite comes from the standard library, and
PostgreSQL is reached by writing a `.sql` file you run yourself.

## Finding the rows

A JSON document is rarely already a list of rows, so `pydata` looks for one.

| The document is | The table is |
| --- | --- |
| a list of objects | one row per object |
| a single object | one table, one row |
| an object holding exactly one list of objects | that list, with a note on stderr |
| an object holding several | an error naming each candidate |
| a list of bare scalars | one column, called `value` |

`--root` overrides the search with a dotted path:

```shell
pydata tree api.json --root data.users
```

```
$ pydata tree api.json
error: Several row sources found; pick one with --root:
  --root users   list of 120 objects, 7 keys
  --root orders  list of 43 objects, 5 keys
```

CSV and Excel are already tables, so `--root` is rejected for them.

## `tree` — the structure

```
$ pydata tree api.json
Using --root data.users (3 records).
api.json  --root data.users
list of 3 objects, 7 keys

|-- id          int        3/3
|-- First Name  str        3/3
|-- age         int|float  2/3  nullable
|-- tags        list[str]  3/3
|-- address     object     3/3
|   |-- city    str        3/3
|   `-- zip     int|str    2/3  nullable
|-- active      bool       3/3
`-- note        str        1/3  nullable
```

The count is how many records held a real value at that path, so `1/3` on
`note` is what makes it a nullable column later. A field seen with two types
shows both — `int|str` is the plain explanation for why a column came out as
`text`. `--depth N` stops the descent.

## `summary` — the statistics

One row per top-level field. The `min`/`max`/`mean` triple is read according
to the field's type: extremes and an average for numbers and dates, lengths
for text, element counts for lists and objects, a true/false tally for
booleans.

```
field      | column     | type      | non_null | nulls | distinct | min     | max     | mean             | top
-----------+------------+-----------+----------+-------+----------+---------+---------+------------------+---------
id         | id         | int       | 3        | 0     | 3        | 1       | 3       | 2                |
First Name | first_name | str       | 3        | 0     | 3        | len 2   | len 3   | len 2.667        |
age        | age        | int|float | 2        | 1     | 2        | 28.5    | 34      | 31.25            |
tags       | tags       | list[str] | 3        | 0     | 3        | 0 items | 2 items | 1 items          |
active     | active     | bool      | 3        | 0     | 2        |         |         | 2 true / 1 false | true (2)
```

`--format csv|markdown|json|excel` and `-o FILE` work as they do everywhere
else in the toolbox.

## `filter` — part of the data

```shell
pydata filter api.json --type int --type float   # every numeric field
pydata filter api.json -k 'addr*' -k 'first*'    # by name
pydata filter api.json -k '*name*' --type str    # both at once
pydata filter api.json --drop-empty --rows 20
```

`-k/--key` is a glob, matched case-insensitively against both the original key
and the SQL column name, so either spelling finds the field. `-t/--type` takes
`null bool int float date datetime str list object json mixed`; `json` matches
any container and `mixed` matches a field seen with more than one type.
Several `--type` values are OR-ed together, while `--key` and `--type` narrow
the selection together.

### Every sheet at once: `--sheet '*'`

```shell
pydata filter staff.xlsx -k amount --sheet '*'
```

Instead of just the active sheet, `--sheet '*'` reads every sheet of a
workbook and runs the same filter on each. Every row is tagged with the sheet
it came from, and a sheet where nothing matched is skipped rather than
failing the whole command -- so `-k amount --sheet '*'` prints that column
from every sheet that has an `amount`, however differently the rest of each
sheet is shaped:

```
$ pydata filter staff.xlsx -k amount --sheet '*'
sheet | amount
------+-------
Q1    | 120.50
Q1    | 7
Q2    | 340
```

Only Excel accepts `'*'` here; CSV and JSON are already one table.

### Any depth: `--deep`

```shell
pydata filter api.json -k city --deep
```

`-k`/`-t` normally only look at the top level -- `address` matches, but
`address.city` does not. `--deep` looks inside every nested object and list
too, at any depth, the way `orders.[].sku` would be reached by hand. The
output changes shape to match: one row per match instead of one row per
record, naming the path each value was found at.

```
$ pydata filter api.json -k city --deep
record | path          | value
-------+---------------+-------
1      | address.city  | Berlin
1      | items.[].city | X
2      | address.city  | Rome
```

A path is always the original key names, dot-joined, with `.[]` for "an
element of this list" -- the same notation the inferred schema uses
internally. `--raw-names` has no effect here since there is no SQL name to
fold to. `--type mixed` describes a whole column across records, which has
no meaning for a single matched value, so it is rejected with `--deep`.
`--sheet '*'` and `--deep` combine, searching every sheet at any depth.

## `count` — how many rows or objects

```shell
pydata count sales.csv                     # 2 records
pydata count api.json --root data.users    # 3 records
pydata count staff.xlsx                    # every sheet, since --sheet is not given
pydata count staff.xlsx --sheet Q1         # just that sheet
```

Nothing is inferred or typed; `count` only reads far enough to know how many
records there are. For a workbook read without `--sheet`, every sheet is
counted rather than just the active one:

```
$ pydata count staff.xlsx
sheet | count
------+------
Q1    | 42
Q2    | 0
```

An empty sheet counts as `0` instead of failing, since counting is meant to
survey a workbook, not load it.

## `keys` — the keys, titles or headers

```shell
pydata keys api.json                    # top-level JSON keys
pydata keys sales.csv                   # CSV header
pydata keys staff.xlsx                  # every sheet, since --sheet is not given
pydata keys staff.xlsx --sheet Q1       # just that sheet's header
```

Names are folded to snake_case, the same as `tree`, `summary` and `sql`;
`--raw-names` prints them exactly as they appear in the file. For a workbook
read without `--sheet`, sheets that share the exact same columns are grouped
so the columns are not repeated once per sheet:

```
$ pydata keys staff.xlsx
Q1, Q2:
  id
  full_name
  hired

Notes:
  id
  text
```

Unlike an explicit `--sheet`, a sheet with a header row but no data still
lists its keys instead of failing, since this is meant to survey a workbook,
not load it. `--format csv|markdown|json|excel` write one `sheet, key` row
per column instead -- ungrouped, for a consumer that wants every pair -- and
`-o FILE` works as it does everywhere else in the toolbox.

## `sql` — the table

```shell
pydata sql sales.csv -t sales --db app.db
pydata sql api.json -t users --dialect postgres --sql users.sql
pydata sql api.json -t users --dialect postgres --sql - | psql mydb
```

`--db` executes against a real SQLite file with bound parameters. `--sql`
writes a script instead, in whichever `--dialect` you name, and `--sql -`
sends it to stdout. `--dry-run` prints what would happen and writes nothing.

```sql
CREATE TABLE "users" (
    "id"         bigint NOT NULL,
    "first_name" text NOT NULL,
    "age"        double precision,
    "tags"       jsonb NOT NULL,
    "address"    jsonb NOT NULL,
    "active"     boolean NOT NULL,
    "note"       text,
    PRIMARY KEY ("id")
);

CREATE INDEX "idx_users_active" ON "users" ("active");

BEGIN;

INSERT INTO "users" ("id", "first_name", "age", "tags", "address", "active", "note") VALUES
    (1, 'ann', 34, '["a","b"]'::jsonb, '{"city":"Berlin"}'::jsonb, TRUE, NULL),
    (2, 'bob', 28.5, '[]'::jsonb, '{"city":"Rome"}'::jsonb, FALSE, 'hi');

COMMIT;
```

### Naming, keys and indexes

| Option | Does |
| --- | --- |
| `-t, --table NAME` | names the table |
| `-k, --key GLOB` | keeps only the columns matching it; repeatable |
| `-c, --column OLD=NEW` | renames a column; repeatable |
| `--pk COL` | primary key; repeat it for a compound key |
| `--index COLS` | index on comma-separated columns; repeatable |
| `--unique-index COLS` | the same, but `UNIQUE` |
| `--if-exists fail\|replace\|append` | what to do when the table is there already |
| `--batch N` | rows per `INSERT` in a generated script |

Keys and indexes are checked against the data before anything is written. An
unknown column suggests a real one; a primary key that is not actually unique,
or that has missing values, is refused with the offending value named, rather
than blowing up halfway through the insert.

### Choosing the columns

A table does not have to have every field in it. `-k/--key` keeps the columns
matching a glob and drops the rest, spelled and matched exactly as it is in
`filter` — against both the original key and the SQL column name, so either
spelling finds the field, and repeats are OR-ed together.

```shell
pydata sql api.json -t users -k id -k '*name*' --db app.db
```

Nothing matching is an error rather than an empty table. A key or an index on
a column the selection dropped is refused, and says which it was, rather than
claiming the column is missing from data that plainly has it:

```
$ pydata sql api.json -t users -k active --pk id --sql -
error: Primary key column 'id' was excluded by --key.
```

### Column names

The column name comes from the key, folded to lower `snake_case`:
`"First Name"` to `first_name`, `userID` to `user_id`. A key that is already
lower `snake_case` — `id`, `email_address` — passes through untouched. A
leading digit is prefixed with `_`, collisions get a numeric suffix, and every
identifier is emitted quoted, so a column called `select` is still legal.
`tree` and `summary` show the original key beside the SQL name, and
`--column OLD=NEW` matches either spelling.

Folding is not transliteration. Latin accents are dropped, because `prenom` is
what someone typing that column would write — but a script with no ASCII
spelling keeps its letters, because both SQLite and PostgreSQL accept it as a
quoted identifier and deleting it would lose the column name:

| Key | Column |
| --- | --- |
| `First Name` | `first_name` |
| `Prénom` | `prenom` |
| `نام واحد` | `نام_واحد` |
| `انبار/سردخانه` | `انبار_سردخانه` |
| `日本語 の 列` | `日本語_の_列` |
| `हिन्दी` | `हिन्दी` |

A mark that stands on its own is kept rather than deleted for having no Latin
spelling, so a Devanagari virama survives. A Persian zero-width non-joiner
separates words, like a space.

Identifiers are cut to PostgreSQL's limit of 63 **bytes**, not characters, so
a Persian or Chinese name runs out after roughly half as many letters as an
English one — and never mid-character.

The folding exists to keep the table comfortable to query afterwards. A
verbatim `"userID"` column is legal, but then `SELECT userID FROM users` fails
on PostgreSQL, which lowercases unquoted identifiers to `userid` — you would
have to quote that column in every query forever.

`--raw-names` turns the folding off and uses each key exactly as it appears,
for when the table should mirror its source:

```shell
pydata sql api.json -t users --raw-names --dialect postgres --sql -
```

```sql
CREATE TABLE "users" (
    "id"         bigint NOT NULL,
    "First Name" text NOT NULL,
    ...
```

Two things still happen under `--raw-names`, because they are correctness
rather than style: a key with nothing in it is called `column`, and a key
longer than 63 characters is truncated — PostgreSQL does that itself, silently,
which would otherwise turn two long keys into one column without saying so.
Under `--raw-names`, `--column OLD=NEW` also keeps the new name verbatim.

The flag applies to `summary`, `filter` and `sql` alike, so the names you see
are the names you get.

### Types

| Inferred | SQLite | PostgreSQL |
| --- | --- | --- |
| `bool` | `INTEGER` | `boolean` |
| `int` | `INTEGER` | `bigint` |
| `float` | `REAL` | `double precision` |
| `date` | `TEXT` | `date` |
| `datetime` | `TEXT` | `timestamp` |
| `str` | `TEXT` | `text` |
| `list`, `object` | `TEXT` | `jsonb` |

Types unify pairwise across records: a field seen as `int` and then `float`
becomes `float`, `date` and `datetime` become `datetime`, and anything else
that disagrees falls back to `text`, which loses nothing. An integer too large
for 64 bits widens to `numeric` (PostgreSQL) or `TEXT` (SQLite).

Nested objects and lists are stored whole, as `jsonb` on PostgreSQL and as
`TEXT` on SQLite. `--nested text` stores them as `text` on PostgreSQL too;
SQLite has no JSON column type at all, so the option is a no-op there and says
so.

### CSV and Excel

Excel already records whether a cell is a number, a date or text, so those
types are kept. CSV has none, so each column is typed as a whole: every cell
is parsed, and one unparseable cell keeps the whole column as text — which is
what stops a mostly-numeric column with a single `n/a` in it from becoming a
mess. Empty cells, and the words `null`, `none`, `nan`, `n/a` and `na`, become
`NULL`.

Leading zeros keep a field as text. `01730` and `007` are postcodes and part
numbers, not integers.

`--no-infer` keeps everything as text. `--delimiter` overrides the sniffer,
`--sheet` picks an Excel worksheet, and `--limit N` reads only the first N
records.

Without `--sheet`, a workbook is read from its active sheet -- not
necessarily the first one, and not necessarily the one you meant. When the
workbook has more than one sheet, which one was picked is named on stderr:

```
$ pydata tree staff.xlsx
Using --sheet 'Sheet1' (3 sheets in this workbook).
...
```

`pydata filter` alone can also read every sheet at once with `--sheet '*'`,
covered under `filter` below.

```
$ pydata tree sales.csv
|-- id      int        3/3
|-- name    str        3/3
|-- age     int        2/3  nullable
|-- joined  date       3/3
|-- active  bool       3/3
|-- zip     str        3/3
`-- amount  int|float  3/3
```

### `-i/--interactive`

Prints the summary, then asks for the table name, which columns to include,
the primary key and the indexes, suggesting columns that are complete and
unique enough to be a key. Columns are picked by number, by name or by a mix
of the two, and Enter keeps all of them; the order typed is the order the
table gets. Only the columns kept are offered as key candidates.
Everything it prints goes to stderr, so `--sql -` still pipes a clean script
into `psql` while the conversation happens on the terminal.

```
$ pydata sql api.json -i --db app.db
...summary table...

Table name [users]:
Columns: 1 id, 2 first_name, 3 age, 4 email, 5 active
Columns to include (numbers or names, blank for all): 1,2,email
Columns unique and complete enough for a key: id, first_name
Primary key (comma-separated, blank for none) [id]:
Indexes: comma-separated columns; join with + for a composite.
Indexes (blank for none): active, city+zip
Unique indexes (blank for none):

Table    users
Rows     120
Columns  3 of 5
Key      id
Index    active
Index    city, zip

Go ahead? [Y/n]:
```

## `edit` — renaming the names

```shell
pydata edit sales.csv --rename "First Name=full_name"
pydata edit api.json --root data.users -i
pydata edit staff.xlsx --sheet Q1 -i --suggest -o staff-clean.xlsx
```

`edit` changes the titles of a spreadsheet, the keys of a JSON document and
the header of a CSV, in the file itself. Nothing else moves: values, types,
row order and the shape of the document are what they were.

```
$ pydata edit sales.csv --rename "First Name=full_name" --rename id=user_id
column | old        | new
-------+------------+-----------
1      | id         | user_id
2      | First Name | full_name

Rename 2 columns in sales.csv? [y/N]: y
Renamed 2 columns in sales.csv (backup: sales.csv.bak).
```

`--rename` is repeatable and is also spelled `-c/--column`, the same as it is
in `sql`. `OLD` matches the name as `tree` shows it, in any case, or its SQL
spelling — `first_name` finds `First Name` — and a name that is not there is
an error that names a real one rather than a rename that quietly does less
than you asked:

```
$ pydata edit sales.csv --rename nmae=full_name
error: No column called 'nmae'. Did you mean: name?
```

`NEW` is used exactly as you type it. This is the file, not a SQL identifier,
so a header that should read `First Name` is allowed to.

Renames are checked as a set before anything is written. Two columns given
the same new name, or a new name that an untouched column already has, are
refused; swapping two names is not, because the result is unambiguous.

### What each format keeps

| Format | Rewritten | Kept exactly |
| --- | --- | --- |
| CSV | the header line | every data row, byte for byte, including quoting and CRLF |
| Excel | the header cells | formulas, styles, column widths, other sheets, macros |
| JSON | the renamed keys | key order, indentation, the envelope around `--root`, trailing newline |

Newline-delimited JSON stays newline-delimited. A workbook is opened for
writing rather than regenerated, so `--sheet` picks which sheet's header is
renamed and the rest of the file is not touched.

### Safety

The file is copied to `FILE.bak` first, unless `--no-backup`. `-o/--output`
writes a copy and leaves the original alone — no backup is made then, because
nothing is at risk. `-n/--dry-run` prints the change table and writes nothing.

Every write goes to a temporary file beside the destination and is moved into
place, so an interrupted run leaves either the old file or the new one, never
half of either.

Anything else asks first. The confirmation defaults to no, and a run that is
not attached to a terminal takes that default, so `pydata edit` in a script
needs `-y/--yes` to do anything.

### `-i/--interactive`

Prints the summary, then asks for each name in turn. Enter keeps the name, so
walking the whole list without typing is a no-op.

```
$ pydata edit sales.csv -i
...summary table...

Enter keeps a name as it is.
[1/5] id [id]: user_id
[2/5] name [name]: full_name
[3/5] joined [joined]:
[4/5] zip [zip]:
[5/5] amount [amount]:

column | old    | new
-------+--------+-----------
1      | id     | user_id
2      | name   | full_name

Go ahead? [Y/n]:
```

`--suggest` offers the snake_case spelling as each default instead —
`[2/5] First Name [first_name]:` — for cleaning up a whole header at once. A
`--rename` given on the command line pre-fills that column's default, so the
two ways of saying it work together. A name that is empty, or that another
column already has, is refused at its own prompt rather than at the end.

## Reading from stdin

`-` reads standard input, with `--from` naming the kind since there is no
suffix to go by. Excel needs a real path.

```shell
curl -s https://api.example.com/users | pydata tree - --from json
```

Newline-delimited JSON is read as a list, but only after a whole-document
parse fails, so a pretty-printed file is never mistaken for it.

## Install

`pydata` is part of the core install. Reading `.xlsx` needs the `excel` extra:

```shell
pip install -e ".[excel]"
```
