# `pycalc` — arithmetic from the shell

Also available as `toolbox calc`.

```shell
pycalc '2**5+56-1'                # 87
pycalc '2^10'                     # 1024
pycalc '(1+2)*3 / 4'              # 2.25
pycalc 'sqrt(2) * sin(pi/4)'      # 1
pycalc 255 --base hex             # 0xff
echo '2+2' | pycalc               # 4
```

Quote anything with a `*` or a `(` in it, or the shell will get there first.
Unquoted arguments are joined back together, so `pycalc 2 + 3` also works —
and a leading minus is read as a negative number, not a mistyped option, so
`pycalc -2**2` gives `-4`.

## What it understands

| | Written as |
| --- | --- |
| Arithmetic | `+` `-` `*` `/` `//` `%` `**` |
| Powers | `**` and `^` — **both** mean "to the power of" |
| Bitwise | `&` `\|` `~` `<<` `>>`, and `xor(a, b)` |
| Comparisons | `<` `<=` `>` `>=` `==` `!=`, printed as `True`/`False` |
| Numbers | `12`, `1.5`, `1.5e3`, `0xff`, `0b1011`, `0o17`, `1_000_000` |
| Constants | `pi`, `e`, `tau`, `inf`, `nan` |
| Functions | `sqrt` `cbrt` `exp` `log` `ln` `log2` `log10` `pow` `abs` `round` `int` `float` `min` `max` `floor` `ceil` `trunc` `fabs` `copysign` `hypot` `gcd` `lcm` `factorial` `comb` `perm` `degrees` `radians` `sin` `cos` `tan` `asin` `acos` `atan` `atan2` `sinh` `cosh` `tanh` `xor` |

### `^`

On a calculator and in a spreadsheet `^` means exponentiation; in Python it
means bitwise exclusive-or. This is a calculator, so `2^10` is 1024. Pass
`--caret xor` for Python's meaning — `xor(2, 10)` does that job under either
setting.

## Answers

Whole answers stay whole and stay exact: `6/2` prints `3`, and `2**100` prints
all thirty-one digits.

Everything else is rounded to twelve significant digits before printing, which
is what turns the honest-but-unhelpful `0.30000000000000004` back into the
`0.3` you asked for. `-p/--precision` changes that; `-p 17` shows the exact
binary value.

```shell
pycalc '0.1 + 0.2'          # 0.3
pycalc '0.1 + 0.2' -p 17    # 0.30000000000000004
pycalc '1/3' -p 3           # 0.333
```

`--base hex|bin|oct` prints a whole answer in another base:

```shell
pycalc '1 << 12' --base hex   # 0x1000
pycalc '0xff & 0x0f' --base bin
```

## From a pipe, or line by line

With no expression, `pycalc` reads one per line from standard input and prints
one answer per line. `ans` holds the previous answer, blank lines and `#`
comments are skipped, and `quit` (or Ctrl-D) ends the session.

```shell
$ pycalc
2 + 2
4
ans * 10
40
quit
```

In a pipeline a bad line stops the run with a non-zero exit code; at an
interactive prompt it prints the error and waits for the next line.

## What it will not do

The expression is parsed with Python's own parser and then walked node by
node, evaluating only the arithmetic listed above. There is no `eval`, no
attribute access, no indexing, no lambda and no import — `pycalc
'__import__("os").system("id")'` is an error, not a shell.

Two calculations that are quick to type and impossible to finish are refused
outright rather than attempted: a power with more than 100,000 digits
(`9**9**9`) and a factorial of more than 10,000.

## Options

```
    --caret power|xor    What ^ means (default: power)
    --base dec|hex|bin|oct   Base for whole answers (default: dec)
-p, --precision N        Significant digits for the rest (default: 12)
```
