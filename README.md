# bms-web

Self-hosted companion to the [BMS Android app](../BMS_Android). The phone uploads finished
recordings over the home wifi; this service parses them, keeps the originals, and serves a
Strava-style browser for rides and battery sessions on the LAN.

Nothing is exposed to the internet: the container binds to the home interface only, and uploads
carry a bearer token.

## Status

Milestones 1–4 of [docs/plan.md](docs/plan.md): the ingest core, the service around it, the web
frontend, and the Android app's uploader (`hu.telegdy.bms.sync`, in the app repo). Recordings can
also be loaded straight off disk with `bmsctl import`.

The phone side has been built and unit-tested, and its requests were checked against a live server,
but it has not yet run on an actual phone.

| | |
| --- | --- |
| `bmsweb/parse.py` | CSV → samples. Port of the app's `LogRepository.parse`. |
| `bmsweb/summary.py` | Samples → the figures shown for a session. Port of `LogSummary.of`. |
| `bmsweb/geo.py` | Haversine and the flat-earth projection. Port of `Geo`. |
| `bmsweb/simplify.py` | Route cleanup for drawing. Port of `RouteSimplifier`. |
| `bmsweb/polyline.py` | Encoded polyline, so a list of rides is one request. |
| `bmsweb/ingest.py` | Upload → raw file on disk → parsed rows in SQLite. |
| `bmsweb/db.py` | Schema and migrations. |
| `bmsweb/splits.py` | Per-kilometre breakdown of a ride. |
| `bmsweb/api/` | The v1 API: health, sessions, stats. |
| `bmsweb/static/` | The frontend: dashboard, lists, ride detail. No build step. |
| `bmsweb/cli.py` | `summarise`, `import`, `reparse`. |

The ports are deliberate, not incidental: the same ride opened on the phone and in the browser must
not disagree. [docs/csv-format.md](docs/csv-format.md) is the contract between the two halves.

## Running it

```bash
cp .env.example .env && docker compose up -d --build
```

Everything in `.env` already has a working default. `BMS_BIND=8080` listens on every interface,
which is what you want on a home LAN; put an address in front of the port to pin it to one
interface instead. `BMS_UPLOAD_TOKEN` empty means no authentication.

All state lives in `./data` — the SQLite index and every uploaded original. That directory is the
only thing worth backing up, and even losing the database costs a `bmsctl reparse`, not a ride.

### The API

| | |
| --- | --- |
| `GET /api/v1/health` | Unauthenticated, so the phone can probe it to decide it is home. |
| `POST /api/v1/sessions` | Multipart upload. Idempotent on the content hash. |
| `GET /api/v1/sessions` | Filter by kind, local date range, free text; paginated. |
| `GET /api/v1/sessions/{id}` | Everything stored for one session. |
| `GET /api/v1/sessions/{id}/track` | Route points and bounds; `?detail=full` for every fix. |
| `GET /api/v1/sessions/{id}/splits` | Per-kilometre rows; `?km=` to resize them. |
| `GET /api/v1/sessions/{id}/series` | Charts, min/max downsampled, with dropouts listed. |
| `GET /api/v1/sessions/{id}/raw.csv` | The original file back, byte for byte. |
| `PATCH /api/v1/sessions/{id}` | Title and notes. |
| `DELETE /api/v1/sessions/{id}` | Index row goes, original moves to `data/trash/`. |
| `GET /api/v1/stats` | Totals and per-day buckets for the dashboard. |

Interactive docs at `/docs` while the service is running.

### Loading recordings without the phone

```bash
BMS_DATA_DIR=./data python -m bmsweb.cli import /path/to/logs/
```

### After changing how a figure is computed

Bump `SCHEMA_VERSION` in `bmsweb/__init__.py`, then rebuild the history from the stored originals:

```bash
BMS_DATA_DIR=./data python -m bmsweb.cli reparse
```

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
