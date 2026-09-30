# bms-web

Self-hosted companion to the [BMS Android app](../BMS_Android). The phone uploads finished
recordings over the home wifi; this service parses them, keeps the originals, and serves a
Strava-style browser for rides and battery sessions on the LAN.

Nothing is exposed to the internet: the container binds to the home interface only, and uploads
carry a bearer token. The one thing it fetches is an elevation tile the first time a ride needs
one — see [below](#your-effort).

## Status

Milestones 1–4 of [docs/plan.md](docs/plan.md): the ingest core, the service around it, the web
frontend, and the Android app's uploader (`hu.telegdy.bms.sync`, in the app repo). Recordings can
also be loaded straight off disk with `bmsctl import`.

Plus heart rate, from either of two places: the Wear OS app, which the phone writes straight into
the recording, or a GPX exported from Strava and attached to the recording it belongs to. See
[below](#heart-rate).

And the rider's side of a ride: how much of the work was theirs rather than the motor's, time in
heart rate zones, and calories. See [below](#your-effort).

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
| `bmsweb/gpx.py` | GPX → points. Heart rate out of a Strava export. |
| `bmsweb/align.py` | Measuring the offset between two devices' clocks. |
| `bmsweb/companions.py` | Attaching a GPX to a recording, and serving its channels. |
| `bmsweb/terrain.py` | Ground height under a point, from SRTM elevation tiles. |
| `bmsweb/altitude.py` | Which height to believe: barometer, elevation map or GPS. |
| `bmsweb/effort.py` | The rider's power, heart rate zones, calories. |
| `bmsweb/profile.py` | The rider: weight, age and the rest those need. |
| `bmsweb/api/` | The v1 API: health, sessions, companions, stats, pack, profile. |
| `bmsweb/static/` | The frontend: dashboard, lists, ride detail. No build step. |
| `bmsweb/cli.py` | `summarise`, `import`, `reparse`, `attach`. |

The ports are deliberate, not incidental: the same ride opened on the phone and in the browser must
not disagree. [docs/csv-format.md](docs/csv-format.md) is the contract between the two halves.

## Running it

```bash
docker compose up -d --build
```

Nothing needs configuring. `cp .env.example .env` first if you want to change the port, set a
token, or move the timezone.

State lives in the `bms-data` volume — the SQLite index and every uploaded original. That volume is
the only thing worth backing up, and even losing the database costs a `bmsctl reparse`, not a ride.

### Portainer

Add a stack with the **Repository** method pointed at this repo; the compose file works as-is with
no environment variables set. Portainer does **not** read `.env` from the repository, so set
anything you want to change in the stack's own "Environment variables" box.

To update, use **Pull and redeploy**. The compose file sets `pull_policy: build`, so every deploy
rebuilds the image from the code it just pulled instead of restarting the old one.

Two things are worth knowing, because both fail in the same confusing way — a container that
restarts for ever while the stack cheerfully reports itself deployed:

**Use the named volume.** The container runs as an ordinary user (uid 10001). Docker creates a
bind-mounted host directory as root and mounts it over `/data`, discarding the ownership the image
set, so the app cannot write and dies on startup. The compose file uses a named volume for exactly
this reason. If you do want the data at a specific host path, either `chown -R 10001:10001` that
directory first, or add `user: "0:0"` to the service and accept it running as root.

**Check the container log before anything else.** It prints what it resolved on every start:

```
bms-web ready
  data      /data
  sessions  0
  auth      off (no token set)
```

If you see that and still cannot reach it, the service is fine and the problem is the port
mapping or the host firewall. If you see a message about uid 10001 instead, it is the volume.

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
| `GET /api/v1/sessions/{id}/trim` | The whole recording as uploaded, whatever the trim, for the editor. |
| `PUT /api/v1/sessions/{id}/trim` | Keep only `start_ms`–`end_ms`; both null undoes the trim. |
| `DELETE /api/v1/sessions/{id}` | Index row goes, original moves to `data/trash/`. |
| `POST /api/v1/sessions/{id}/companions` | Attach a GPX. Idempotent on the content hash. |
| `GET /api/v1/sessions/{id}/companions` | What is attached, with its heart rate figures. |
| `PATCH /api/v1/sessions/{id}/companions/{cid}` | `offset_ms`, or `realign` to measure it again. |
| `DELETE /api/v1/sessions/{id}/companions/{cid}` | Detach; the GPX moves to `data/trash/`. |
| `GET /api/v1/sessions/{id}/effort` | The rider's share of the work, zones and calories. |
| `GET /api/v1/profile` | The rider profile, with the maximum heart rate and zones in use. |
| `PUT /api/v1/profile` | Replace it. |
| `GET /api/v1/stats` | Totals and per-day buckets for the dashboard. |

Interactive docs at `/docs` while the service is running.

### Loading recordings without the phone

```bash
BMS_DATA_DIR=./data python -m bmsweb.cli import /path/to/logs/
```

### Heart rate

#### From the watch

With the Wear OS app open during a ride, the phone adds an `hr_bpm` column to the recording (see
[docs/csv-format.md](docs/csv-format.md)). Nothing needs doing here: the ride page charts it, puts
average and maximum beside the other figures and in each split, can colour the route by it, and
the ride list shows the average. It is on the recording's own clock, so there is no offset to
measure.

Recordings uploaded before this server understood the column carry it already, but their figures
were computed without it. Run a `reparse` after updating (inside the container, from Portainer's
console: `python -m bmsweb.cli reparse`) and they pick it up.

When a recording has the watch's heart rate, that is the one charted; an attached GPX still
contributes cadence.

#### From a Strava export

The pack knows everything about itself and nothing about the rider. Open the ride on Strava, choose
**⋯ → Export GPX**, and attach the file to the session — in the browser, at the bottom of the ride
page, or from the command line:

```bash
BMS_DATA_DIR=./data python -m bmsweb.cli attach 42 evening_loop.gpx
```

Heart rate and cadence then appear as two more charts on the same shared cursor, and as two more
summary tiles. The GPX is kept whole next to the CSVs, and the rows in the database are derived
from it, so an attached file survives a `reparse` exactly as a title does.

The two files come from two clocks. Rather than trust them to agree, the server matches the ride's
own speed against the speed implied by the GPX's points and reports the shift it found, along with
the correlation it achieved — visible on the page, and overridable by hand if the match looks
wrong. A recording too short or too stationary to match on is attached unshifted and says so.

Note **Export GPX**, not **Export Original**: the original is a FIT file, which this does not read.
And a GPX only carries a heart rate if the activity was recorded with one.

### Your effort

Fill in **You** in the sidebar — weight and year of birth are the two that matter — and every ride
page gains a **Your effort** card: your work in watt-hours against the motor's, your share, your
average power, time in heart rate zones and calories. Your power is charted too, and each split
gets a column for it. Nothing is stored: the figures are worked out whenever a ride is opened, so
a corrected weight applies to every ride at once.

**Your power** is what the ride needed beyond what the motor gave. Moving the bike takes rolling
resistance, air, climbing and speeding up, all of which follow from the route, the speed, the
heights and the mass; the pack says what the motor drew, and about 80 % of that reaches the
road. The rest was you. It is an estimate, and shown as one, with a range from running the model
with its constants set low and high. On a slope steep enough to carry you, you count as coasting.
The hard work of holding on down a trail is not power into the bike, so it is not in this figure —
the heart rate and the calories do count it. The reasoning, and what was tried on real rides to get
there, is at the top of `bmsweb/effort.py`.

**The heights** come from an elevation map (SRTM, about 30 m resolution), because a phone's GPS
altitude is not good enough to measure climbing with. The first time a ride needs a tile, the
server fetches it — about 7 MB per 1°×1° square, from the public `elevation-tiles-prod` bucket on
AWS — and keeps it in `data/dem/`; after that it works offline. The request names only the
square. Set `BMS_DEM_URL=off` to never fetch; tiles copied into `data/dem/` by hand still work, and
without any, GPS altitude is used and the page says so.

When the watch starts sending its barometer (a `pressure_hpa` column, see
[docs/csv-format.md](docs/csv-format.md)), those rides use it for the shape of each climb and the
map for its level. Nothing needs changing here for that.

**Zones** are fractions of your maximum heart rate: the one you set if you know it; otherwise
estimated from your age as 208 − 0.7 × age, raised to the highest heart rate you have actually
recorded if that is higher. **Calories** use the Keytel heart rate equations — ±20–30 % for any
one person.

### Trimming a ride

A recording left running after the ride — into the car, say — can be cut back with **Trim** on the
ride page. Drag the two handles over the speed trace (a drive shows up as a wall of it) and the map
redraws as they move: the kept stretch in colour, the rest in grey, with the start and end marked.

Nothing is deleted. The trim is two moments stored on the session, and every figure, split, chart
and map is rebuilt from the samples between them; the original CSV stays whole on disk, and the
**CSV** download still gives it back byte for byte. A trim survives a `reparse`, can be widened
again, and **Whole recording** undoes it.

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
