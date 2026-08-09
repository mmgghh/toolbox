# `pystr` — text, clipboard, encoding

Also available as `toolbox str`.

```
search        Search files, a directory tree, inline text or stdin
replace       Preview and apply replacements across files
case          Convert between naming conventions
encode/decode base64, hex, URL escapes, rot13
count         Line, word and character counts
normalize     Fold accents and Arabic forms with the bundled rules
translate     Convert digits and punctuation between English and Persian
clip-search   Search the clipboard
clip-replace  Replace text in the clipboard
getclip       Print the clipboard
setclip       Write the clipboard
```

Commands that read text accept it three ways: a `PATH`, `--text "..."`, or
`--stdin`. Commands that transform text accept `--inplace` to overwrite the
input file.

---

## `search`

```shell
pystr search ./src "TODO"
pystr search . "error" -i -e log --stats
pystr search . 'def\s+main' --regex -e py -v
pystr search . --tag email --tag ip --only-matches
pystr search ./logs "timeout" --file-name '.*\.log$' --count
pystr search "token" --text "token=abcd"
echo "hello world" | pystr search "world" --stdin
pystr search ./src "TODO" --json
```

Queries are literal by default; `--regex` switches to a Python regex.

**Tags** save you from writing common patterns by hand. `--tag`/`-t` is
repeatable and combines with a query:

`url`/`link`, `email`, `ip`/`ipv4`/`ipv6`, `phone`/`mobile`, `zip`, `postal`,
`date`, `time`, `uuid`, `mac`.

Filtering options: `-e/--extension`, `--file-name REGEX`, `-d/--depth`
(0 = only the root directory), `--exclude`/`--exclude-dir` globs, `--hidden`,
`--follow-symlinks`, `--max-size MB`, `--binary`.

Output options: default prints matching paths, `--count` adds per-file counts,
`-v` prints matching lines, `-o/--only-matches` prints just the matched text,
`--stats` adds a summary, `--json` prints everything structured.

Binary files are skipped unless `--binary` is given.

## `replace`

```shell
pystr replace ./src foo bar -e py --dry-run
pystr replace ./docs TODO DONE -i --backup --yes
pystr replace . '(\d+)' '[\1]' --regex --dry-run
```

Always shows the plan (files and match counts) before touching anything, then
asks for confirmation unless `--yes`. `--dry-run` stops after the plan.
`--backup` writes `file.bak` (suffix configurable with `--backup-suffix`).
Takes the same filtering options as `search`.

## `case`

```shell
pystr case --to snake --text "Hello World"        # hello_world
pystr case --to camel --text "some-long_name"     # someLongName
pystr case --to slug --text "Résumé of 2026!"     # resume-of-2026
echo "someValue" | pystr case --to kebab --stdin  # some-value
pystr case ./headings.txt --to title --per-line --inplace
```

Styles: `lower`, `upper`, `title`, `snake`, `kebab`, `camel`, `pascal`,
`slug`, `slug-unicode`. `slug-unicode` keeps non-ASCII letters
(`سلام دنیا` → `سلام-دنیا`); plain `slug` transliterates to ASCII.

`--per-line` converts each line separately, keeping the line structure.

## `encode` / `decode`

```shell
pystr encode --text "hello" --as base64      # aGVsbG8=
pystr decode --text "aGVsbG8=" --as base64   # hello
echo "hello" | pystr encode --stdin --as hex
pystr encode --text "a b&c" --as url         # a%20b%26c
pystr decode ./token.txt --as base64url
```

Schemes: `base64`, `base64url`, `hex`, `url`, `url-plus`, `rot13`. Missing
base64 padding is added automatically, so pasted fragments still decode.

## `count`

```shell
pystr count ./README.md
pystr count ./notes.txt --top 10
echo "a b c" | pystr count --stdin --json
```

Reports lines, words, characters, characters excluding whitespace, and bytes.
`--top N` adds the N most frequent words.

## `normalize`

Applies the bundled normalization table: accented Latin letters are folded to
ASCII, Arabic-Indic digits become Western ones, and typographic punctuation is
simplified.

```shell
pystr normalize --text "Résumé ١٢٣"      # Resume 123
echo "متن   نمونه" | pystr normalize --stdin
pystr normalize ./notes.txt --inplace
```

Note that the table maps en/em dashes to `-` and then removes `-` entirely, so
normalizing twice is not the same as normalizing once.

## `translate`

Converts digits, letters and punctuation between English and Persian forms.
This is *character mapping*, not translation of meaning.

```shell
pystr translate --to en --text "شماره ۱۲۳؟"   # شماره 123?
pystr translate --to fa --text "Issue 123?"    # Issue ۱۲۳؟
pystr translate ./notes.txt --to fa --inplace
```

`--to en` also normalizes Arabic letter variants; `--to fa` maps Arabic forms
to their Persian equivalents (`ي`→`ی`, `ك`→`ک`).

## Clipboard commands

```shell
pystr getclip --trim
pystr setclip "hello"
echo "hello" | pystr setclip --stdin
pystr clip-search "secret" --ignore-case
pystr clip-search --tag email --only-matches
pystr clip-replace "foo" "bar" --yes
```

The backend is chosen automatically: `termux-clipboard-*` on Termux,
`pbcopy`/`pbpaste` on macOS, PowerShell on Windows, then `wl-clipboard`,
`xclip` or `xsel` on Linux. `toolbox doctor` reports which one is in use.
