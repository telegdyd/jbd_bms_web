# The recording format

The CSV written by the Android app is the interface between the phone and this service. Nothing
else is shared — no ORM, no generated client, no protobuf. This document is the contract, and
`bmsweb/parse.py` implements exactly what is written here.

Both writers live in the app under `app/src/main/java/hu/telegdy/bms/data/`:
`LogWriter.kt` and `Ekd01LogWriter.kt`.

## Naming and location

`yyyyMMdd-HHmmss_<device-label>.csv`, all in one directory on the phone, both kinds together. The
label is the BLE device name with everything outside `[A-Za-z0-9._-]` replaced by `_`, truncated to
40 characters.

The filename is **not** an identifier. Two recordings can start in the same second on different
devices, and nothing stops the same name existing on two phones. The service keys on the SHA-256
of the file content instead.

## BMS recordings

```
timestamp,elapsed_s,volts,amps,watts,soc_percent,remaining_ah,
temp1_c…tempN_c,cell1_mv…cellN_mv,delta_mv,min_cell_mv,max_cell_mv,
[latitude,longitude,altitude_m,speed_kmh,gps_accuracy_m,]balance_bits
```

| Column | Notes |
| --- | --- |
| `timestamp` | `yyyy-MM-dd'T'HH:mm:ss.SSSXXX`, fixed width, always with an explicit offset. |
| `elapsed_s` | Seconds since the session started, 3 decimals. The app reads this from the tail to show a duration without parsing the file. |
| `volts`, `amps`, `watts` | Pack level. **Positive watts is charging, negative is discharging.** |
| `soc_percent` | Integer, as the BMS reports it. |
| `remaining_ah` | 3 decimals. |
| `temp*_c` | One column per probe the pack exposes. The count varies by pack. |
| `cell*_mv` | One column per cell, integer millivolts. The count varies by pack. |
| `delta_mv`, `min_cell_mv`, `max_cell_mv` | Across the cells present in that row; all three are empty if no cell reported. |
| `latitude`…`gps_accuracy_m` | Present only if location logging was on when the session started. 6 decimals of coordinate, ≈0.1 m. |
| `balance_bits` | Raw bitfield; bit N is cell N+1. One column of fixed meaning survives a pack with a different cell count. |

## EKD01 recordings

```
timestamp,elapsed_s,speed_kmh,assist_level,trip_km,odometer_km,battery_bars,battery_percent
```

Only what the display actually transmits. It puts no motor current, power or pack voltage on the
BLE link, so there is nothing to invent. `trip_km` is integrated from wheel speed by the writer,
deliberately independent of the lifetime `odometer_km`, so a ride's distance stands alone.

## Rules a reader must follow

1. **Find columns by name, never by index.** New columns are appended on the end precisely so that
   every earlier recording stays loadable. Positional reads are only safe for the seven fixed
   leading columns of a BMS file and the block sizes derived from the header.
2. **Cell and temperature counts come from the header**, per file. They are a property of the pack
   that was logged, not of the format.
3. **Detect the kind from the header**, not the filename. A file with no `volts` column and the
   display's columns is an EKD01 recording.
4. **A missing column is missing, not the last column.** Kotlin's `getOrNull(-1)` is null; a Python
   port that indexes with `-1` will silently report `balance_bits` as a latitude.
5. **The final row may be truncated.** A session killed mid-write leaves a short row. Skip it.
   Never fail the file for it.
6. **Timestamps must carry an offset.** One that does not is skipped rather than assumed to be in
   the reader's own zone.
7. **Dropouts are gaps in the timestamp column**, never blank rows. The threshold is derived, not
   assumed: `max(3 × median inter-sample delta, 5000 ms)`. The log interval is configurable, so a
   session logged every 10 s must not read as one continuous outage.
8. **Locale is fixed.** Numbers are always formatted with `Locale.ROOT`, so the decimal separator
   is a point even on a Hungarian phone. A comma there would corrupt every numeric column.

## Derived figures

Everything shown for a session is recomputed from the samples, never stored by the phone. The
rules that keep those figures honest are in `bmsweb/summary.py`, ported from `LogSummary.of`:

- Energy across a dropout is **excluded**, never interpolated — otherwise a long outage invents
  watt-hours.
- Fixes claiming worse than **20 m** accuracy contribute no distance.
- Steps shorter than **1.5 m** are receiver jitter and are discarded, so a parked phone does not
  accumulate kilometres.
- Moving time counts intervals that begin at **1 km/h** or above.
- Wh/km is **withheld below 100 m** of travel rather than shown as a confident nonsense figure.

Route drawing uses a different, looser set (`bmsweb/simplify.py`, from `RouteSimplifier`): 25 m
accuracy limit, 6 m minimum separation, 3 m Douglas-Peucker epsilon. What is accurate enough to
draw and what is accurate enough to measure are not the same question.

## Versioning

Adding a column is a compatible change; readers ignore what they do not know. Renaming or removing
one is not, and neither is changing a unit or a sign convention. If that ever has to happen, the
writers gain a `format_version` column and readers branch on its absence meaning 1.
