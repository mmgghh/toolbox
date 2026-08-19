# `pyjdate` — Jalali and Gregorian dates

Also available as `toolbox jdate`.

```
now / current      Current date and time in both calendars
convert            Convert a date, timestamp or relative interval
interval           First and last moment of a year, month, day or range
distance           How far a date is from now
distance-between   The difference between two dates
```

Every command prints the Gregorian date, the Jalali date and the Unix
timestamp, with weekday names in both calendars.

---

## Input formats

**Concrete dates** — pick the calendar with exactly one of `-g/--gregorian`,
`-j/--jalali`, `-e/--epoch`:

```
2026-01-04            2026/01/04            20260104
04-01-2026            Jan 04 2026           January 4, 2026
2026-01-04 10:43      2026-01-04T10:43:45   2026-01-04 10:43:45+03:30
1404/10/14 10:44:46   1700000000            (with -e)
```

Month names work in both calendars: `-g -m feb`, `-j -m mehr`.

**Relative intervals** — PostgreSQL style, resolved against the current time:

```
1 y        3 days      -3.4 hours      2 days 04:30
90 minutes 1y2mon10d   1 week          04:30
```

Units: `y/year(s)`, `mon/month(s)`, `w/week(s)`, `d/day(s)`, `h/hour(s)`,
`m/min/minute(s)`, `s/sec/second(s)`. Years and months shift the calendar
month (so 31 Jan + 1 month lands on 28/29 Feb); days and smaller units are
exact elapsed time.

## `now`

```shell
pyjdate now
pyjdate now --fa      # Persian script for Jalali month and weekday names
pyjdate now --json
```

```
Gregorian: 2026-08-09 04:32:13 (August, Sunday)
Jalali:    1405-05-18 04:32:13 (Mordad, Yekshanbeh)
Unix:      1786237333
```

`current` is the same command under its original name.

## `convert`

```shell
pyjdate convert -g "2026-01-04 10:43"
pyjdate convert -j "1404/10/14 10:44:46" --fa
pyjdate convert -e 1700000000 --json
pyjdate convert -i "-3.4 hours"
pyjdate convert -g -y 2026 -m feb -d 20
```

Instead of a full date you can pass the parts separately with `-y/--year`,
`-m/--month`, `-d/--day`, `-H/--hour`, `--minute`, `--second`.

`--json` returns both calendars broken into fields, plus the weekday, epoch
and ISO forms — useful for scripting:

```json
{
  "gregorian": { "date": "2026-01-04", "month_name": "January", "weekday": "Sunday", ... },
  "jalali":    { "date": "1404-10-14", "month_name": "Dey", "weekday": "Yekshanbeh", ... },
  "unix": "1767467400",
  "iso": "2026-01-04T00:00:00+03:30"
}
```

## `interval`

Prints the first and last moment of a period.

```shell
pyjdate interval -j -y 1404                     # a whole Jalali year
pyjdate interval -g -y 2026 -m 02               # a Gregorian month
pyjdate interval -j -y 1404 -m mehr -d 12 --fa  # one day
pyjdate interval -g -s "2026-01-01" --end "2026-01-31"
pyjdate interval -e -s 1700000000 --end 1700086400
pyjdate interval -g -y 2026 -m 02 --json
```

`--json` gives `start` and `end`, each in the same shape `convert --json` uses.

Jalali month lengths and leap years are handled correctly: months 1–6 have 31
days, 7–11 have 30, and Esfand has 29 or 30.

## `distance`

```shell
pyjdate distance -g "2026-03-21 08:00"
pyjdate distance -j 1405/01/01
pyjdate distance -i "2 days 4 hours" --json
```

## `distance-between`

Either endpoint may be a full date, a Unix timestamp (with `-e`), or a
relative interval resolved against now.

```shell
pyjdate distance-between -g -s "2026-01-01 00:00" --end "2026-01-02 12:00"
pyjdate distance-between -j -s 1404/01/01 --end 1405/01/01
pyjdate distance-between -g -s "-3 days" --end "6 hours" --json
```

Both distance commands print:

```
Gregorian: 0 years, 0 months, 1 days, 12 hours, 0 minutes, 0 seconds
Jalali:    0 years, 0 months, 1 days, 12 hours, 0 minutes, 0 seconds
Total days:    1.5
Total hours:   36
Total seconds: 129600
Human:         1d 12h
```

Component distances are computed in each calendar separately, because "one
month later" differs between them.

## Using the conversion from Python

```python
from pytoolbox.pyjdate import gregorian_to_jalali, jalali_to_gregorian, weekday_name
from datetime import date

gregorian_to_jalali(2026, 1, 4)     # (1404, 10, 14)
jalali_to_gregorian(1404, 10, 14)   # (2026, 1, 4)
weekday_name("jalali", date(2026, 1, 4))  # 'Yekshanbeh'
```

The conversion uses the jalaali-js algorithm and round-trips exactly.
