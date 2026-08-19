# `pyfm` — files and directories

Also available as `toolbox fm`.

```
partition            Split a directory's contents into numbered subdirectories
merge                Flatten a directory tree into one directory
batch-find-replace   Regex find/replace across files with given extensions
batch-rename         Rename files and directories by regex
duplicates           Find (and optionally delete) files with identical contents
organize             Sort loose files into subdirectories
extract-links        Pull http(s) links out of a file or web page
file-find-replace    Replace a literal string in one file
generate-text-file   Create text files filled with random sentences
```

Every command that changes the filesystem accepts `-n/--dry-run` and prints
exactly what it would do.

---

## `partition`

Splits the **direct children** of a directory into numbered subdirectories.
Choose exactly one of `--partitions`, `--split-count` or `--split-size`.

| Option | Meaning |
| --- | --- |
| `--partitions N` | Create exactly N directories |
| `-c, --split-count N` | About N entries per directory |
| `--split-size N` | About N megabytes per directory |
| `--split-based-on count\|size` | With `--partitions`, how to balance them |
| `--pattern REGEX` | Only entries whose name matches |
| `--dir-prefix NAME` | Prefix for created directories (default `part`) |
| `-d, --destination DIR` | Where to create them (default: the source) |
| `--copy` | Copy instead of moving |

```shell
# Five directories of roughly equal total size.
pyfm partition -s ./data -n 5 --split-based-on size

# CD-sized chunks, previewed first.
pyfm partition -s ./photos --split-size 700 --dir-prefix disc --dry-run

# 100 images per directory.
pyfm partition -s ./photos -c 100 --pattern '.*\.(jpg|png)$'
```

Size balancing is greedy largest-first bin packing, so directories come out
close in total size even when file sizes vary a lot.

## `merge`

Moves files out of a directory tree into one flat destination.

| Option | Meaning |
| --- | --- |
| `--file-pattern REGEX` | Which files to move (by filename) |
| `--dir-pattern REGEX` | Which directories to descend into (by name) |
| `--overwrite yes\|no\|same-size\|keep-both` | Collision handling (default `keep-both`) |
| `--copy` | Copy instead of moving |
| `--keep-empty-dirs` | Do not clean up emptied source directories |

```shell
pyfm merge -s ./shows -d ./flat --file-pattern '.*\.mp4$'
pyfm merge -s ./source -d ./dest --overwrite same-size -v
pyfm merge -s ./a -d ./b --dry-run
```

`keep-both` renames collisions to `name(1).ext`. Emptied source
subdirectories are removed afterwards; the source root itself never is.

## `batch-find-replace`

Regex find/replace across files with the given extensions. Scans only direct
children unless `-R/--recursive` is given.

```shell
pyfm batch-find-replace -d ./docs -x md -x txt -f foo -r bar -v
pyfm batch-find-replace -d ./src -x py -f 'old_name' -r 'new_name' -R
pyfm batch-find-replace -d ./cfg -x env -f '<DOMAIN_PORT>' -r 'example.com:443' -n
```

Two bundled shortcuts expand to ready-made patterns, and can be used as named
backreferences in the replacement (`\g<UUID4>`, `\g<DOMAIN_PORT>`):

- `<UUID4>` — a UUID
- `<DOMAIN_PORT>` — `sub.domain.tld:port`

## `batch-rename`

```shell
pyfm batch-rename -d ./downloads -f ' ' -r '_' -v
pyfm batch-rename -d ./archive -f '2024' -r '2025' --include-dirs -D 2
pyfm batch-rename -d . -f '^IMG_' -r 'photo-' --dry-run
```

`-D/--depth` adds extra levels below the target directory (0 = direct
children). Deeper levels are processed first so renaming a parent never
invalidates a child path. Renames that would overwrite an existing entry are
skipped and reported.

## `duplicates`

Finds files with identical contents. Files are grouped by size first and only
hashed when a size is shared, so a large tree costs one `stat()` per file and
very few reads.

```shell
pyfm duplicates ./photos
pyfm duplicates ./downloads -x pdf --json
pyfm duplicates ./photos --delete --dry-run
pyfm duplicates ./photos --delete -y      # keeps the first of each group
```

## `organize`

Sorts loose files in a directory into subdirectories. Only that directory's
own files are touched, so running it twice is safe.

| `--by` | Groups into |
| --- | --- |
| `ext` | `pdf/`, `jpg/`, `no-extension/` |
| `date` | modification date via `--date-format` (default `%Y-%m`) |
| `name` | first character of the filename |

```shell
pyfm organize ~/downloads --by ext
pyfm organize ~/photos --by date --date-format '%Y/%m' --dry-run
pyfm organize ./books --by name --pattern '\.epub$'
```

## `extract-links`

```shell
pyfm extract-links -s ./page.html --stdout --unique
pyfm extract-links -s 'https://example.com' --pattern '^https://example\.com' --overwrite
pyfm extract-links -s ./page.html -o ./links.txt
```

Asset links (`.js`, `.css`) and bare domain roots are filtered out by default;
`--no-filter` keeps them. Without `--stdout` the links are appended to
`links.txt` (use `--overwrite` to replace it).

## `file-find-replace`

Literal (non-regex) replacement in a single file.

```shell
pyfm file-find-replace -p ./notes.txt -f old-value -r new-value -v
pyfm file-find-replace -p ./config -f 'localhost' -r '0.0.0.0' --dry-run
```

## `generate-text-file`

Creates test files filled with random sentences.

```shell
pyfm generate-text-file -d ./tmp -n 20 -l 50 -p sample -v
pyfm generate-text-file -d ./tmp -n 5        # random 0-100 lines each
```
