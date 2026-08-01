# BMS web service — implementation plan

A self-hosted companion to the Android app: the phone uploads finished recordings to a container
on the home server, which parses them, keeps the originals, and serves a Strava-style browsing UI
on the LAN.

Decisions taken up front:

| Question | Choice |
| --- | --- |
| Network | LAN only. Server binds to the home interface, phone syncs on home wifi. No ports open to the internet. |
| Stack | Python 3.12 + FastAPI + SQLite, single container, static frontend served by the same app. |
| Sync trigger | Automatic on unmetered wifi via WorkManager, plus a manual "Sync now" button. |
| Scope | Every recording is ingested. Geo-tagged ones get the ride view; stationary ones (solar, bench) get charts only. |

---

## 1. The contract between the two halves

The CSV **is** the interface. Both writers are already stable and self-describing:

- `LogWriter` (BMS) — `timestamp, elapsed_s, volts, amps, watts, soc_percent, remaining_ah,
  temp*_c…, cell*_mv…, delta_mv, min_cell_mv, max_cell_mv, [latitude, longitude, altitude_m,
  speed_kmh, gps_accuracy_m,] balance_bits`
- `Ekd01LogWriter` — `timestamp, elapsed_s, speed_kmh, assist_level, trip_km, odometer_km,
  battery_bars, battery_percent`

Rules the server must honour, all of them already true of the files:

1. **Columns are found by name, never by index.** `LogWriter` appends new columns on the end
   precisely so old files stay loadable — the server inherits that guarantee.
2. **Cell and temperature counts vary per file.** Derived from the header, same as
   `LogRepository.parse`.
3. **Kind is detected from the header signature, not the filename.** Both writers use the same
   `yyyyMMdd-HHmmss_label.csv` naming and the same directory; `speed_kmh + assist_level + odometer_km`
   with no `volts` means EKD01.
4. **The last row may be truncated** by a process kill. Short rows are skipped, not fatal.
5. **Dropouts are gaps in the timestamp column**, never blank rows. Gap threshold is derived, not
   assumed: `max(3 × median inter-sample delta, 5 s)` (`LogRepository.gapThresholdMs`).
6. **Timestamps carry an explicit UTC offset.** Store epoch millis *and* the offset — local-day
   grouping in the UI must match what the rider saw, and rides across a DST boundary or abroad
   must not shift days.

Write this up as `docs/csv-format.md` and treat it as versioned. It is the thing that keeps a
2026 recording readable by a 2028 server.

**Metric parity is a hard requirement.** The server recomputes every figure the app shows, and the
numbers must agree to the last decimal, or the same ride will read differently in two places. Port
`LogSummary.of` verbatim, including the constants that make it honest:

- `MAX_USABLE_ACCURACY_M = 20.0` — fixes vaguer than this do not contribute distance
- `MIN_STEP_M = 1.5` — sub-jitter steps are discarded so a parked phone does not accrue kilometres
- `MOVING_SPEED_KMH = 1.0` — moving-time threshold
- Energy across a dropout is **excluded**, not interpolated
- `whPerKm` is withheld below 0.1 km rather than shown as noise

A golden-file test fixes this: check a handful of real recordings into `server/tests/fixtures/`
with their expected summaries, generated once from the app's own output.

---

## 2. Server

### 2.1 Layout

Its own repository (`Code/bms-web`), separate from the Android project: the home server clones
only this, and nothing about the Android build travels with it. `✓` marks what milestone 1 landed.

```
bms-web/
  pyproject.toml       ✓
  Dockerfile
  docker-compose.yml
  bmsweb/
    main.py            FastAPI app, routes, static mount
    config.py          env-driven settings
    db.py              SQLite connection, migrations, schema_version
    ingest.py          upload → store raw → parse → summarise → index
    parse.py           ✓ CSV → samples (port of LogRepository.parse)
    summary.py         ✓ samples → summary (port of LogSummary.of)
    geo.py             ✓ haversine + local-metre projection (port of Geo)
    simplify.py        ✓ accuracy filter → min separation → Douglas-Peucker (port of RouteSimplifier)
    polyline.py        ✓ encoded polyline for the map
    cli.py             ✓ bmsctl summarise, for parity-checking real recordings
    api/               sessions.py, stats.py, health.py
    static/            index.html, app.js, style.css, vendored leaflet + uplot
    templates/         optional Jinja shell
  docs/
    plan.md            ✓ this document
    csv-format.md      ✓ the contract between phone and server
  tests/
    fixtures/*.csv     ✓ synthetic, hand-computable
    fixtures/real/     real recordings for parity checks (gitignored)
```

### 2.2 Storage

Two stores, deliberately:

- **`/data/raw/YYYY/MM/<sha8>_<original-name>.csv`** — the uploaded file, byte-for-byte, never
  modified. This is the source of truth. If the summary logic changes, everything is rebuildable.
- **`/data/bms.sqlite`** — the parsed index that makes browsing fast.

```sql
CREATE TABLE sessions (
  id             INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL UNIQUE,      -- upload idempotency key
  source_name    TEXT NOT NULL,             -- original filename
  raw_path       TEXT NOT NULL,
  kind           TEXT NOT NULL,             -- 'bms' | 'ekd01'
  device_label   TEXT,
  started_at_ms  INTEGER NOT NULL,
  ended_at_ms    INTEGER NOT NULL,
  tz_offset_min  INTEGER NOT NULL,
  local_date     TEXT NOT NULL,             -- YYYY-MM-DD, for calendar grouping
  duration_ms    INTEGER, sample_count INTEGER,
  cell_count     INTEGER, temp_count INTEGER,
  has_location   INTEGER NOT NULL DEFAULT 0,
  is_ride        INTEGER NOT NULL DEFAULT 0, -- has_location AND distance_km >= 0.2
  -- summary, mirroring LogSummary
  charged_wh REAL, discharged_wh REAL, peak_charge_w REAL, peak_discharge_w REAL,
  min_volts REAL, max_volts REAL, min_temp_c REAL, max_temp_c REAL, max_delta_mv INTEGER,
  soc_start INTEGER, soc_end INTEGER, gap_count INTEGER, gap_ms INTEGER,
  distance_km REAL, moving_seconds INTEGER, max_speed_kmh REAL, wh_per_km REAL,
  ascent_m REAL, descent_m REAL,
  min_lat REAL, min_lon REAL, max_lat REAL, max_lon REAL,
  polyline       TEXT,                      -- simplified, encoded
  -- user-editable, the Strava part
  title TEXT, notes TEXT, tags TEXT,
  uploaded_at_ms INTEGER, parsed_at_ms INTEGER, schema_version INTEGER NOT NULL
);

CREATE TABLE samples (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  t_ms INTEGER NOT NULL, elapsed_s REAL,
  volts REAL, amps REAL, watts REAL, soc INTEGER, remaining_ah REAL,
  delta_mv INTEGER, min_cell_mv INTEGER, max_cell_mv INTEGER,
  lat REAL, lon REAL, alt_m REAL, speed_kmh REAL, accuracy_m REAL,
  cells_mv TEXT, temps_c TEXT,              -- JSON arrays: cell count varies per file
  PRIMARY KEY (session_id, t_ms)
) WITHOUT ROWID;
```

Cells and temps as JSON arrays rather than wide columns — the pack's cell count is a property of
the file, and a fixed-width table would break the first time a different pack is logged.

`schema_version` on every row drives **reparse**: bump the constant, run `bmsctl reparse --all`,
and every session is rebuilt from its raw CSV with the new logic. This is the payoff for keeping
the originals.

Volume: a 1 Hz ride of two hours is ~7 k rows. A multi-day solar session is ~86 k rows/day —
still nothing for SQLite, but it is why the series endpoint downsamples rather than returning
everything.

### 2.3 API

All under `/api/v1`, all authenticated with `Authorization: Bearer <token>` except `/health`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness. Also what the phone probes to decide "am I home?" |
| `POST` | `/sessions` | Multipart upload: `file`, plus `sha256`, `kind`, `device_label`, `tz_offset_min`, `app_version`. |
| `GET` | `/sessions` | Filter by `from`/`to`/`kind`/`is_ride`/`q`, paginated, newest first. |
| `GET` | `/sessions/{id}` | Full summary + metadata. |
| `GET` | `/sessions/{id}/track` | GeoJSON or encoded polyline, `?simplify=<metres>`. |
| `GET` | `/sessions/{id}/series` | `?fields=watts,volts,speed,soc&points=2000` — downsampled, gap-aware. |
| `GET` | `/sessions/{id}/raw.csv` | The original file back. |
| `PATCH` | `/sessions/{id}` | `title`, `notes`, `tags`. |
| `DELETE` | `/sessions/{id}` | Removes index rows; raw file moved to `/data/trash/` rather than unlinked. |
| `GET` | `/stats` | `?period=week\|month\|year\|all` — totals, streaks, per-day buckets. |

Upload semantics that make retries safe:

- The **`sha256` is the idempotency key**. Same hash → `200 {"status":"duplicate","id":…}`, no
  reparse, no duplicate row. This is what lets the phone retry blindly after a dropped wifi
  connection.
- Filename alone is *not* a key: two recordings can start in the same second on different devices,
  and the phone's filename is not globally unique.
- The server **verifies the hash** it computes against the one the client claimed, and rejects a
  mismatch — that is the truncated-upload check.
- Parse happens inline for files under a few MB, on a background task above that; the response
  returns immediately with `{"status":"accepted"}` and the row appears once parsed.

Downsampling for `/series` uses **min/max bucketing, not averaging** — a 1-second 900 W current
spike must survive being drawn at 2000 points across a three-hour ride, and an average erases it.
Gaps above the session's derived threshold come back as explicit `null` breaks so the chart draws
a discontinuity instead of a straight line across missing time, matching the app.

### 2.4 Frontend

No build step. Vendored `leaflet.js` and `uPlot` (both tiny, both offline-capable), plain ES
modules, one stylesheet. A Node toolchain on the home server would be the largest moving part of
the whole project for no gain here.

Pages:

- **Dashboard** — this week / month / year totals (distance, energy, moving time, Wh/km), a
  GitHub-style calendar heatmap of activity, and the last few sessions as cards with route
  thumbnails.
- **Rides** — the list. Filter by date range and device, free-text search over title/notes.
- **Ride detail** — the centrepiece:
  - Leaflet map with the simplified route, coloured by a selectable channel (speed / power / SOC)
  - Summary tiles matching `SummaryCard` in the app
  - Linked uPlot charts (power, voltage, current, SOC, speed, altitude, cell spread) sharing
    **one cursor**, exactly as `LogDetailScreen`'s single scrubber does — hovering a chart moves a
    marker along the map and reads every other chart at that moment
  - Splits table (per kilometre: time, avg speed, Wh, ascent)
  - Editable title and notes; download the original CSV
- **Sessions** — non-geo recordings (solar, bench). Same charts, no map.
- **Compare** *(later)* — two sessions overlaid on elapsed time, which is how a pack change or a
  firmware change actually gets judged.

Map tiles come from OpenStreetMap and need internet on the *viewing* computer; the ride is still
fully usable without them because the drawn-track fallback (the app's `MapMode.TRACK`) works from
coordinates alone. If offline maps matter later, add a tile-cache proxy endpoint.

Dark mode from the start — it will mostly be read in the evening.

### 2.5 Container

See `docker-compose.yml` and `.env.example`. Everything is a variable with a working default, so
the compose file itself never needs editing:

- `BMS_BIND` — `8080` listens on every interface, which is the sensible default on a home LAN.
  Prefix an address (`192.168.1.10:8080`) to pin it to one interface. This is a preference, not a
  requirement; a home router is not forwarding the port either way.
- `BMS_UPLOAD_TOKEN` — **empty by default, meaning no authentication.** Worth setting only for the
  unglamorous reason that an unauthenticated POST which writes files to disk is reachable by
  anything on the network, including a page open in a browser tab. It costs one header.

`python:3.12-slim`, non-root user, `uvicorn` run directly (no gunicorn — this serves one
household), healthcheck through the interpreter that is already in the image rather than pulling
in curl for one request.

Backup: `/data` holds both stores. A nightly `sqlite3 /data/bms.sqlite ".backup /data/backup/…"`
plus whatever already backs up the server covers it, and even total loss of the SQLite file is
recoverable by reparsing `/data/raw`.

---

## 3. Android side

New package `hu.telegdy.bms.sync`, four files, no change to the recording path itself.

**`SyncSettings` / `SyncSettingsStore`** — SharedPreferences `"sync"`, following the exact shape of
`LoggingSettingsStore`: `enabled`, `baseUrl`, `token`, `wifiOnly`, `deleteAfterDays` (0 = never).
Editable from a new section in `SettingsScreen`, with a "Test connection" button that hits
`/health` and reports the result inline.

**`SyncStateStore`** — a second SharedPreferences file mapping `filename → "<sha>:<size>:<uploadedAtMs>"`.
Deliberately not a database: the state is a few dozen short strings, and it must survive being
wrong (a wiped entry costs one redundant upload, which the server deduplicates).

**`BmsUploader`** — OkHttp (a new entry in `libs.versions.toml`; hand-rolling multipart over
`HttpURLConnection` is possible but is the kind of code that fails only on the ride you cared
about). Computes SHA-256 while streaming the file, posts multipart, interprets `201`/`200
duplicate`/`4xx` distinctly — a `4xx` marks the file permanently failed rather than retrying it
forever.

**`SyncWorker`** — WorkManager, constraints `NetworkType.UNMETERED` + `BatteryNotLow`. Enqueued
from `BmsController.stopLogging()` so a finished ride uploads the moment the phone is home, and
also registered as a ~6-hourly periodic job to catch anything that failed. It:

1. Lists `LogWriter.logDirectory()` via `LogRepository.list`
2. **Skips the currently open log file** — a session still being appended to must never be
   uploaded, or the server indexes a partial ride
3. Skips files whose recorded sha+size match the stored state
4. Uploads oldest first, updating state per file

UI: a small status chip per row in `LogsScreen` (synced / pending / failed), and a "Sync now"
action. The list already derives everything from the filename, so this is one extra lookup per row.

### Two gotchas worth naming now

**Cleartext HTTP.** Android blocks plain HTTP by default. A LAN server without a certificate needs
a `res/xml/network_security_config.xml` permitting cleartext for exactly one host (`bms.home.arpa`
or the server's IP), referenced from `<application android:networkSecurityConfig=…>`. Not a blanket
`usesCleartextTraffic="true"`. The alternative — a self-signed cert pinned in the app — is more
work for a threat model that is already "my own LAN".

**`INTERNET` is already granted** in the manifest (for map tiles), so no new permission is needed.

---

## 4. Build order

Each milestone is independently useful, and the first two need no app changes at all — existing
CSVs can be pulled off the phone and `curl`ed in.

1. ~~**Ingest core.** Parser, summary, geo, simplifier + golden-file tests against real recordings.
   This is where metric parity is won or lost; do it before any web code.~~ **Done**, except that
   the fixtures are synthetic — parity against a real recording from the phone is still unproven.
2. ~~**Server + API + container.** Upload, storage, reparse CLI, read endpoints, health. Verify by
   uploading a month of existing recordings by hand.~~ **Done**, plus `bmsctl import` for loading
   recordings straight off disk. Verification against a real month is still outstanding.
3. **Frontend v1.** List and detail pages: summary tiles, map, linked charts. Usable at this point.
4. **Android sync.** Settings + manual "Sync now" first (easy to debug), then WorkManager
   automation once uploads are known-good.
5. **Dashboard polish.** Totals, calendar heatmap, splits, route colouring, editable titles.
6. **Later, if wanted.** Cross-session pack health (capacity fade, cell-spread drift, Wh/km trend),
   session comparison, GPX/FIT export for pushing rides to Strava proper.

## 5. Known risks

- **GPS altitude is noisy.** Ascent computed naively from raw `altitude_m` will report hundreds of
  metres of climb on a flat ride. It needs smoothing plus a threshold before it is worth showing —
  or leave `ascent_m` out of v1 rather than display a confident wrong number.
- **Long solar sessions** are the stress case, not rides: tens of thousands of rows, and a chart
  request that returns them all will lock the browser. The downsampling endpoint is not optional.
- **Distance depends on GPS quality**, so the same route can differ by a few percent between rides.
  Worth remembering before reading trends into Wh/km.
- **Clock skew** between phone and server is irrelevant by design — every timestamp comes from the
  file, never from the server's own clock.
