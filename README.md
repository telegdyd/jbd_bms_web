# bms-web

Self-hosted companion to the [BMS Android app](../BMS_Android). The phone uploads finished
recordings over the home wifi; this service parses them, keeps the originals, and serves a
Strava-style browser for rides and battery sessions on the LAN.

Nothing is exposed to the internet: the container binds to the home interface only, and uploads
carry a bearer token.

## Status

Milestone 1 of [docs/plan.md](docs/plan.md) — the ingest core. Parsing, summary, geo, route
simplification and polyline encoding, with tests. No web server yet.

| | |
| --- | --- |
| `bmsweb/parse.py` | CSV → samples. Port of the app's `LogRepository.parse`. |
| `bmsweb/summary.py` | Samples → the figures shown for a session. Port of `LogSummary.of`. |
| `bmsweb/geo.py` | Haversine and the flat-earth projection. Port of `Geo`. |
| `bmsweb/simplify.py` | Route cleanup for drawing. Port of `RouteSimplifier`. |
| `bmsweb/polyline.py` | Encoded polyline, so a list of rides is one request. |
| `bmsweb/cli.py` | `summarise`, for checking a real recording against what the app shows. |

The ports are deliberate, not incidental: the same ride opened on the phone and in the browser must
not disagree. [docs/csv-format.md](docs/csv-format.md) is the contract between the two halves.

## Development

Nothing is installed system-wide and nothing is on `PATH`. `uv` lives in `C:\Users\teleg\DevTools\uv`
and manages its own CPython 3.12.

```bash
C:\Users\teleg\DevTools\uv\uv.exe run --python 3.12 pytest -q
```

```bash
C:\Users\teleg\DevTools\uv\uv.exe run --python 3.12 python -m bmsweb.cli summarise path\to\recording.csv
```

The first run creates `.venv` and installs dependencies; later runs are instant.

## Checking parity against the phone

The fixtures under `tests/fixtures/` are synthetic and hand-computable, which proves the logic is
self-consistent — not that it agrees with the app. To prove that, put real recordings pulled off
the phone in `tests/fixtures/real/` (gitignored) and compare:

```bash
C:\Users\teleg\DevTools\uv\uv.exe run --python 3.12 python -m bmsweb.cli summarise tests\fixtures\real\*.csv
```

against the summary card the app shows for the same session. Every figure should match to the
displayed precision.
