# Ulanzi Feeder — AWTRIX Aircraft Overhead Display

Turns a Ulanzi Pixel Clock (**AWTRIX NG** firmware — the actively-maintained
successor to AWTRIX 3) into a live "what's flying over my house" display:
airline tail-logo icon on the left, flight number and origin-destination
route scrolling next to it. A Ulanzi **TC002** (52x16) works too, on AWTRIX NG
or on the stock Ulanzi firmware — see [n8n workflow](#n8n-workflow).

```
Schedule Trigger (n8n, every 30s)
  -> [OpenSky Network API, FlightRadar24 live feed] in parallel (bounding box around home coordinates)
  -> merge + de-duplicate -> pick nearest in-flight aircraft
  -> adsbdb.com (callsign -> route + airline), compared with the FR24 record already fetched
  -> trust whichever source is live/plausible
  -> PUT to AWTRIX NG pushed-app API (skipped when the payload hasn't changed)
```

> This repo targets **AWTRIX NG's `/api/v1/*` HTTP API**, not the older
> AWTRIX 3 `/api/custom` API (the one exception is the separate template for a
> TC002 on the stock Ulanzi firmware, whose own API also lives at `/api/custom`). If your clock is still on AWTRIX 3, either flash
> it to NG first (see below) or adapt the endpoints per the
> [AWTRIX 3 → NG migration guide](https://blueforcer.github.io/awtrix-ng/guides/migrating-from-awtrix3/).

## Hardware / firmware

**Supported firmware:** [AWTRIX NG](https://blueforcer.github.io/awtrix-ng/) — tested on a Ulanzi TC001
(classic ESP32, 4MB flash, AWTRIX NG 1.2.3) and a Ulanzi TC002 (AWTRIX NG 1.2.2,
installed with the [TC002 USB installer](https://blueforcer.github.io/awtrix-ng/tc002/getting-started/tc002/)).
The steps below are for the TC001.

### Web flasher (easiest, most boards)

1. https://blueforcer.github.io/awtrix-ng/getting-started/flashing/ — connect via
   USB-C in Chrome/Edge/Opera and follow the browser flasher.

### USB / esptool CLI (used for this project's TC001, and needed when the web
flasher isn't an option, e.g. no Chromium browser available)

```bash
pip install esptool
# Grab usb-awtrix-ng-<flashsize>.bin from usb-awtrix-ng.zip on the releases page:
# https://github.com/Blueforcer/awtrix-ng/releases
esptool --chip esp32 --port /dev/cu.usbserial-XXXX erase_flash
esptool --chip esp32 --port /dev/cu.usbserial-XXXX --baud 460800 write_flash 0x0 usb-awtrix-ng-4mb.bin
```

Match the flash-size suffix (`4mb`/`8mb`/`16mb`) to your board — check with
`esptool --port /dev/cu.usbserial-XXXX flash_id` if unsure. TC001 is 4MB.

### First boot (either method)

1. The clock broadcasts its own WiFi hotspot for first-time setup. Connect to
   it (from a phone is easiest) and hand it your home WiFi credentials.
2. Give it a static IP on your router/UniFi controller (this project uses
   `YOUR_CLOCK_IP` as a placeholder throughout).
3. Once it's on your network and confirmed working, block it from reaching the
   internet at your firewall/UniFi controller — it never needs WAN access, everything
   here runs over the LAN.
4. **Settings do not migrate from AWTRIX 3.** If you're moving an existing
   clock to NG, note your current settings (`GET /api/v1/settings` is the NG
   equivalent once flashed) before flashing and re-apply them via the web UI or
   `PATCH /api/v1/settings` afterward.

The clock's web UI lives at `http://<CLOCK_IP>` — apps and settings live there.

## Icon pipeline

No display stores icons. Every push carries the airline logo inline as
base64: AWTRIX NG's `icon` field accepts base64 JPEG/GIF in place of an icon
ID (a TC002 gets it as a data URL in a layout icon box on AWTRIX NG, or in
`image[]` on the stock firmware), and the 64x64 matrix takes raw
pixels as `iconData`. The logos live in an n8n
data table instead, so adding an airline never runs into the clock's
filesystem limit (LittleFS allocates a block per file, so a 4MB board filled
up at ~347 tiny icons) and never touches the workflow.

```
TailFin/tailfin/ -> source tail-logo images, e.g. SQ.webp (IATA) or ASY.webp (ICAO-only operator)
   |
   v  sync_icons.py  (uses convert_tiles.convert())
n8n data table `airline_icons` -> one row per logo:
     code | icao | iata | name               | icon8       | icon16      | icon24
     sq   | sia  | sq   | Singapore Airlines | R0lGOD...   | R0lGOD...   | (1536 chars)
   |
   v  Lookup Icon node (every run)
Build Payload -> TC001 `icon` = icon8
Build Payload TC002 -> data:image/gif;base64,icon16 (layout icon box on AWTRIX NG, `image[].data` on stock firmware)
Build Payload (Matrix64) -> `iconData` = icon24 (plus `icon` = <code>_logo for firmware that looks icons up by name)
```

| Column | Format | Used by |
|---|---|---|
| `code` | logo code = TailFin filename, lowercased (`sq`, `asy`, `00`) | row key |
| `icao` | ICAO designator of the airline the logo shows, lowercase; unique; blank if unsure | lookup key 1 |
| `iata` | IATA code; blank for `00` and ICAO-named logos | lookup key 2 |
| `name` | airline name | informational |
| `icon8` | 8x8 GIF, base64 (~230 chars) | TC001 |
| `icon16` | 16x16 GIF, base64 (~300-800 chars) | TC002 |
| `icon24` | 24x24 raw little-endian RGB565, base64 (1152 bytes = 1536 chars) — same bytes as Mini Flightwall's `icons/<code>_logo.bin` | 64x64 matrix |

All three sizes come from the same `convert_tiles.convert()` crop and
quantize; `icon24` is that GIF unpacked to RGB565 because the matrix firmware
has no image decoder.

```
```

**Which logo:** the lookup key is the airline's **ICAO designator** — the
3-letter code filed in the ATC callsign (SIA, QTR, ASY), taken from FR24's
live feed (field 18 of each aircraft record) or adsbdb's `airline.icao`.
adsbdb's own `airline.iata` is the second key, for the few logos with no known
ICAO code, and the `00` row (generic blank tail) is the fallback. **Lookup
Icon** fetches all three candidates in one query and the payload nodes pick in
that order. Nothing is derived by slicing a flight number: FR24 puts the bare
callsign in its flight-number field for military/private traffic, so `ASY102`
(RAAF) used to become `as` and show Alaska Airlines. `icao` must be unique
across rows (`sync_icons.py` enforces it); leave it blank rather than guess —
a wrong designator shows the wrong logo, a blank one just falls through to
IATA or the generic tail.

Both clocks run AWTRIX NG 1.2.x and get the icon as a data URL:
`data:image/gif;base64,<icon8>` on the TC001. 1.2.x rejects bare base64 with
422 `invalid icon`. (1.1.2 was the other way round: it decoded the whole
string as base64, so the prefix corrupted the icon and it was silently dropped.
The TC001 broke on the 1.2.3 update until Build Payload switched to the data URL.)
The TC002 gets a 52x16 `layout` (logo box on the left, callsign over route)
rather than a plain text/icon payload, which it would draw at double size on
a 26x8 grid.

GIF rather than JPEG because at 8x8 JPEG compression smears colours across
neighbouring pixels; the GIFs are lossless, palette-quantized without
dithering, and ~100-400 bytes each — well inside NG's 8192-byte request limit.

### Adding or updating an airline

Drop the logo into `../TailFin/tailfin/` — `<IATA>.webp`, or `<ICAO>.webp`
for an operator with no IATA code — then:

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python sync_icons.py --dry-run                              # lists new logos
venv/bin/python sync_icons.py --icao zz=ZZA --name "zz=Zed Air"      # convert + insert rows
```

`sync_icons.py` only ever writes rows to the `airline_icons` table — it never
touches the workflow, so there's nothing to publish or re-import afterwards.

- **What it converts:** all three sizes (`icon8`, `icon16`, `icon24`) for
  logos in TailFin that aren't in the table yet, plus any icon column that's
  empty on an existing row, so a newly added size column backfills on its
  own (~2s per size from the ~3000px sources, run in parallel across cores).
- **Options:**
  - `--codes sq,tr` — re-convert logos you've replaced in TailFin
  - `--icao CODE=ICAO` / `--name CODE=NAME` — fix an existing row's mapping
    without converting anything (or edit the row in the n8n UI:
    *Data tables → airline_icons*). `--icao CODE=` clears it.
  - `--all` — re-convert every logo, e.g. after changing `convert_tiles.py`
  - `--mode fin|tight`, `--input DIR` — crop mode / source folder
  - `--dry-run` — report what would change, write nothing
- **Defaults for a new row:** a 2-letter filename becomes `iata`, a 3-letter
  one becomes `icao`; anything else (the ICAO for an IATA-named logo, the
  name) comes from `--icao` / `--name`.
- **Safety checks:** it finds the table by name (no hard-coded id), refuses to
  give two logos the same ICAO designator (e.g. it caught UPS appearing as
  both `5x` and `ups` — `5x` keeps only its IATA key), and after every write
  re-reads the table and fails if any row doesn't match what it wrote.
- **Backup:** after each write it exports the whole table to
  `airline_icons.backup.json`. That file is gitignored, because the logos are
  third-party trademarks.
- **Requirements:** `.env` (see below), `venv` from `requirements.txt` (pinned
  Pillow + numpy; run `pip-audit -r requirements.txt` now and then), and
  `00.webp` (generic blank tail) in TailFin. In the live workflow, airlines
  that fall back to it are logged to the `missing_tailfin` data table (a
  local debugging aid, not in the templates), so that's the to-do list for
  new logos. Every run except `--dry-run` also removes the `missing_tailfin` rows the
  table now has a logo for (`--dry-run` lists them).
- **From TailFin:** the `tailfin-sync` skill in the TailFin repo
  (`TailFin/.claude/skills/tailfin-sync/SKILL.md`) runs this whole flow for
  logos dropped into `TailFin/new_tailfin/`. It identifies each airline's
  ICAO code, uploads the matrix's R2 tiles, moves the sources into
  `tailfin/` and runs this script.

### `convert_tiles.py`

The converter behind the pipeline (also runnable standalone to write GIF
files for inspection):

- **Smart Emblem Focus**: Detects the actual logo mark inside the tailfin (birds, cranes, flags, symbols) and crops tightly around it so emblem details fill 6x6 or 7x7 of the 8x8 matrix instead of 2x2.
- **Aspect Ratio & Centering**: Fits logos proportionally and centers them on a black canvas.
- **Multi-Stage Downscaling**: Uses unsharp masking and two-pass reduction to maintain sharp contrast boundaries.
- **No-Dithering Quantization**: Saves as clean solid-pixel indexed GIFs without Floyd-Steinberg dot-matrix noise.
- **Crop once, render per size**: `prepare()` does the expensive crop on the
  ~3000px source once; `render()` turns it into each icon size. The alpha
  erosion is a separable numpy min filter, byte-identical to PIL's
  `MinFilter` but ~15x faster, so a new logo converts in well under a second.

```bash
venv/bin/python convert_tiles.py --input ../TailFin/tailfin --output /tmp/icons            # 8x8, emblem mode
venv/bin/python convert_tiles.py --input ../TailFin/tailfin --output /tmp/icons --size 16  # TC002 size
```

## n8n workflow

There's one importable template per clock, each containing only that clock's
nodes and Config fields:

| Template | For |
|---|---|
| `n8n_tc001_aircraft_workflow.json` | TC001 on AWTRIX NG |
| `n8n_tc002_aircraft_workflow.json` | TC002 on AWTRIX NG 1.2.2+ (recommended): a 52x16 `layout`, logo box on the left, callsign over route |
| `n8n_tc002_ulanzi_firmware_aircraft_workflow.json` | TC002 on the stock Ulanzi firmware: `POST /api/custom?name=adsb`, see `TC002-PUSH-NOTES.md` |

The stock-firmware template is a frozen copy from before the author's TC002
moved to AWTRIX NG. It is not regenerated by `export_template.py` and no longer
tested against a live clock, but it worked as shipped. It has since had only
the security and timeout changes (OpenSky credential, execution settings,
HTTP timeouts), not the FR24 or push-dedupe changes.

The 64x64 matrix has its own template in the Mini Flightwall repo. Have more
than one display? Import one template and copy the other clock's
**Build Payload / Push / Clear** nodes across, or better, run a single
combined workflow like the live one described under *Workflow copies* below.
Separate workflows would each poll OpenSky, eating into its ~4000
credits/day.

To set one up on a fresh n8n:

1. **Import from File**, then fill in the placeholders in the first **Config**
   node (table below).
2. Create an **OAuth2 API** credential named `OpenSky API`: Grant Type
   *Client Credentials*, Access Token URL
   `https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token`,
   your OpenSky client id/secret (Account Settings -> API Client),
   Authentication *Send credentials in body*, and Allowed HTTP Request Domains
   limited to `opensky-network.org`. Select it in **Fetch Nearby States**. n8n
   fetches, caches and refreshes the token itself, encrypted, so the secret
   never appears in the workflow or its executions.
3. Create an n8n public-API key and put it in `.env` with `n8nurl` (see
   *`.env`* below).
4. Run `venv/bin/python sync_icons.py`. It creates the `airline_icons` data
   table if it doesn't exist, loads every logo from TailFin, and prints the
   table id. Select that table in the **Lookup Icon** node.
5. Activate the workflow.

Config fields:

| Field | What it is |
|---|---|
| `HOME_LAT` / `HOME_LON` | Your coordinates — used as the center of the aircraft search radius |
| `RADIUS_DEG` | Bounding-box half-width in degrees (`0.15` ≈ 15km) |
| `CLOCK_IP` | TC001 (AWTRIX NG) static IP |
| `CLOCK2_IP` | TC002 static IP |
| `MATRIX_IP` | 64x64 matrix (Mini Flightwall) static IP |

Each template only has the fields its clock uses (`CLOCK_IP` *or*
`CLOCK2_IP`); `MATRIX_IP` isn't in either. No secrets live in Config: OpenSky
is an n8n credential (step 2) and **Log Missing Icon** is a native Data Table
node that finds `missing_tailfin` by name.

Workflow settings (carried in the templates): `saveDataSuccessExecution:
none` and `saveExecutionProgress: false`, because at 2,880 runs/day stored
successful runs are tens of MB/day; failed runs are still saved in full.
Set `saveDataSuccessExecution` to `all` temporarily when you need to inspect
a working run. `executionTimeout: 25` keeps a hung run from overlapping the
next one; every HTTP node has a timeout, and the display pushes/clears
continue on error so one offline display doesn't stop the others.
`availableInMCP` is `false`. Also set `N8N_ENCRYPTION_KEY` (and back it up)
and keep execution pruning on (`EXECUTIONS_DATA_PRUNE`, the default).

To reuse this for a second location (a second home, a different feeder site):
duplicate the workflow and change only the Config values — nothing else needs
editing. The `airline_icons` data table is shared; **Lookup Icon** refers to
it by id, so re-select the table there if you import into another n8n.

### Workflow copies — keep them in sync

The live n8n workflow is **one combined workflow** that drives all three
displays (TC001, TC002 and the Mini Flightwall 64x64 matrix) from a single
poll. It exists in these places:

| Where | What it is |
|---|---|
| **n8n** (workflow id = `n8nworkflow` in `.env`) | **The source of truth** — what actually runs every 30s |
| `n8n_aircraft_workflow.local.json` (this repo) | Local copy with the real Config — gitignored |
| `../Mini Flightwall/matrix64_aircraft_workflow.local.json` | Same local copy, kept in the matrix repo — gitignored there too. **Must be byte-identical to the one above** |
| `n8n_tc001_aircraft_workflow.json`, `n8n_tc002_aircraft_workflow.json` (committed) | Per-clock templates for others to import, generated from the local copy by `export_template.py`. Regenerate them whenever the live workflow changes (step 4 below) |

Check that the local copies match each other and live n8n:

```bash
cmp n8n_aircraft_workflow.local.json "../Mini Flightwall/matrix64_aircraft_workflow.local.json" && echo "local copies identical"
venv/bin/python - <<'PY'
import json, urllib.request
env = dict(l.strip().split('=', 1) for l in open('.env') if '=' in l)
req = urllib.request.Request(f"http://{env['n8nurl']}/api/v1/workflows/{env['n8nworkflow']}",
                             headers={"X-N8N-API-KEY": env['n8n']})
live = json.load(urllib.request.urlopen(req))
live = {k: live[k] for k in ("name", "nodes", "connections", "settings")}
print("matches live n8n:", json.load(open('n8n_aircraft_workflow.local.json')) == live)
PY
```

**Changing the workflow:**

1. Start from the **live** workflow — edit it in the n8n UI, or
   `GET /api/v1/workflows/{id}`, change it, and `PUT` it back with only
   `name`, `nodes`, `connections`, `settings` (plus `staticData` if you need
   to change it).
   An active workflow is re-published automatically on `PUT`.
2. Never `PUT` a local file without first confirming it still matches live:
   edits made in the n8n UI aren't in the local files, and pushing a stale
   copy silently reverts them (this nearly lost the Scoot airline-name
   override once).
3. Afterwards write the live workflow back to **both** `.local.json` files
   (same JSON, 4-space indent) and re-run the check above.
4. Regenerate the committed templates: `venv/bin/python export_template.py`.
   For each clock it:
   - **prunes** the combined workflow down to that clock: removes the other
     displays' nodes, the live-only missing-icon debug log
     (`LIVE_ONLY_NODES`), and every Config field nothing reads any more. In
     the TC001 template, Build Payload returns the clock payload directly:
     the live `{ payload, iconCode }` wrapper only exists for the log. That
     edit is an exact-match rule (`TEMPLATE_EDITS`) that stops the export if
     the live code changes.
   - **checks for dead or broken wiring**: a reference to a removed node, a
     node unreachable from the trigger, or a Config field that's unused or
     missing.
   - **scrubs** every value that's specific to your location or instance
     (coordinates, device IPs, credential ids, the id of any Data Table node
     not selected by name) into a placeholder.
   - **refuses to write** if any check fails, or if any real value from the
     local Config or `.env` still appears anywhere in the output.

   If you add a node that belongs to one display, list it in
   `DISPLAY_NODES`. If you add a Config field, give it a placeholder rule.
   The script stops on unknown Config fields rather than guessing.
   Nodes shared by all displays are copied unchanged, so a few fields only
   the matrix reads (e.g. `speedKt`, `fr24AircraftType` in Nearest
   Aircraft) are still computed in the clock templates.

Icon/logo changes never need a workflow change — they go into the
`airline_icons` data table via `sync_icons.py`.

### Data tables

| Table | Written by | Used by |
|---|---|---|
| `airline_icons` | `sync_icons.py` (or the n8n UI) | **Lookup Icon** — logo per ICAO/IATA code for all three displays |
| `missing_tailfin` | **Log Missing Icon** (native Data Table upsert, table picked by name, keyed by airline: ICAO designator, else the callsign's ICAO prefix, else IATA; fills `icao`, `iataCode`, `flightNumber`, `tail`, and `name`/`country` from adsbdb), live workflow only, local debugging. Not logged: registration callsigns, aircraft with no airline code, or runs where Lookup Icon failed. `sync_icons.py` removes rows once their logo is added | You: airlines that showed the generic `00` tail and still need a logo. Fill `category`/`comments` by hand; the workflow never overwrites them |

### `.env` (gitignored)

| Key | Used for |
|---|---|
| `n8n` | n8n public-API key (`X-N8N-API-KEY`) — `sync_icons.py`, workflow sync checks |
| `n8nurl` | n8n `host:port` |
| `n8nworkflow` | Id of the live aircraft workflow |
| `clientId` / `clientSecret` | OpenSky API client (same values as the `OpenSky API` n8n credential) |

Keep it private: `chmod 600 .env`. `sync_icons.py` warns when `n8nurl` is
plain HTTP to a non-local host, since the API key is sent in a header.

**Flow:**

1. **Every 30s** — Schedule Trigger. (OpenSky's free tier is ~4000 API
   credits/day; 30s polling stays comfortably inside that.)
2. **Position stage, run in parallel:**
   - **Fetch Nearby States** — OpenSky's bounding-box query for aircraft near
     home coordinates, authenticated by the `OpenSky API` OAuth2 credential.
   - **Fetch Nearby States (FR24)** — the same bounding box against
     FlightRadar24's live feed (the unofficial
     `data-cloud.flightradar24.com/zones/fcgi/feed.js` endpoint the
     [FlightRadarAPI](https://pypi.org/project/FlightRadarAPI/) Python/Node
     wrappers call), using its `bounds` parameter. Airborne only
     (`gnd=0`, `vehicles=0`, `maxage=900`): ground traffic at Changi is most
     of the box and was filtered out in code anyway.
   - **Merge Position Data** — waits for both, joins them into one item.
3. **Nearest Aircraft** (Code node) — unions both position lists, de-duplicated
   by `icao24` (FR24 reports it uppercase, OpenSky lowercase; OpenSky's entry
   wins on overlap since it's the documented, sanctioned source — FR24 only
   fills in coverage gaps, e.g. an aircraft one network's ground receivers
   missed but the other's caught), then haversine-distance-sorts the combined
   pool and keeps the closest.
4. **Aircraft Found?** — branches to clear the display if nothing's overhead.
5. **Lookup Route (adsbdb)** — free lookup at
   [adsbdb.com](https://api.adsbdb.com) resolving callsign -> origin/destination
   airports + airline IATA code. The FR24 side needs no second call: the
   bounds feed from step 2 already carries each aircraft's route (fields
   11/12) and airline ICAO designator (field 18), matched on the exact callsign.
   (The matrix branch's **Lookup Aircraft (adsbdb)** caches each airframe's
   registration/type in workflow static data for 6h, 1h for unknown hexes.)
6. **Compare Routes** (Code node) — adsbdb and hexdb.io both resolve routes
   from a static flight-number table that can hold decade-old records (a real
   example hit during development: callsign `TGW543`/`TR543` still resolved to
   a 2012-era Tiger Airways Australia domestic route, `SYD-MEL`, on both
   services — even though `TR` is Scoot's IATA code today and Scoot has never
   flown that route). This node decides which route to trust:
   - A live FR24 match wins outright (it reflects what's actually airborne
     right now, not a static table).
   - Otherwise, adsbdb's route is used only if the aircraft's real position is
     within a plausible distance of one of its claimed airports.
   - If only one source has data at all (e.g. adsbdb has no record for a
     private/GA or military/cargo callsign, but FR24 has a filed flight plan
     for it, or vice versa), that source alone is used — confirmed live: a
     callsign `ATN730` cycle had no adsbdb record at all, and FR24 alone
     supplied both the route and the airline (its ICAO designator, feed
     field 18).
   - The cycle is skipped entirely (previous display stays) only when
     *neither* source has anything usable.
   The full comparison (`adsbdb`, `fr24`, `match`, `trustedRoute`,
   `trustedSource`) stays on the item, visible in the n8n execution log for
   every run, so a bad upstream record is obvious at a glance.
7. **Lookup Icon** — Data Table node: one query against `airline_icons` for
   the ICAO match, the IATA match and the `00` row (see *Icon pipeline*).
8. **Build Payload** — constructs the AWTRIX NG pushed-app JSON from the
   verified route, e.g.:
   ```json
   { "text": "SQ123 SIN-KUL", "icon": "R0lGODdhCAAIAIQAA...", "durationMs": 5000, "textCase": "asTyped" }
   ```
   Falls back to callsign-only text if neither source has a trustworthy
   route, and to the generic `00` tail if there's no logo for the airline.
   The node outputs `{ payload, changed }` (plus `iconCode` in the live
   workflow, for the local-debug **Missing Icon?** branch, which logs each
   airline that fell back to `00` at most once an hour). `changed` is false
   when the payload is identical to the last one pushed: a re-PUT restarts
   the clock's scroll, so **Clock Payload Changed?** skips it, but it is still
   re-sent every 5 minutes in case the clock rebooted. **Build Payload TC002**
   does the same by returning nothing. Nearest Aircraft resets this whenever
   the clocks are cleared.
9. **Push to Clock** — `PUT http://<CLOCK_IP>/api/v1/apps/pushed/adsb` (with
   `Content-Type: application/json`). When no aircraft is overhead, **Clear
   Clock** instead sends `DELETE http://<CLOCK_IP>/api/v1/apps/adsb`.

Remember to flip the workflow to **Active** in n8n once it's configured — it
imports inactive by default.

> **A note on `$('NodeName')` references after a `Merge` node:** every
> `$('NodeName').item.json` reference anywhere in this workflow uses
> `.first().json` instead. n8n's `.item` accessor resolves the *paired* input
> item by tracing lineage back through the graph, and that trace breaks with
> `Cannot read properties of undefined` once an item has passed through a
> branch-then-rejoin — even via a proper `Merge` node, confirmed by
> reproducing it on a disposable test workflow. `.first()` just grabs a
> node's first output item directly, with no lineage-tracing involved, so it
> works identically before or after any `Merge` in the graph. If you edit any
> Code node or expression here, keep using `.first()`, not `.item`.

## Repo layout

```
.
├── README.md
├── convert_tiles.py          # one tail logo -> 8x8 / 16x16 / 24x24 GIF
├── requirements.txt          # pinned Pillow + numpy
├── sync_icons.py             # ../TailFin/tailfin/*.webp -> n8n `airline_icons` data table
├── export_template.py        # .local.json -> per-clock templates (pruned, scrubbed, leak-checked)
├── n8n_tc001_aircraft_workflow.json  # importable TC001 template (placeholders, no logos)
├── n8n_tc002_aircraft_workflow.json  # importable TC002 (AWTRIX NG) template (placeholders, no logos)
├── n8n_tc002_ulanzi_firmware_aircraft_workflow.json  # TC002 on stock Ulanzi firmware (frozen)
├── n8n_aircraft_workflow.local.json  # live workflow copy, real config (gitignored)
├── airline_icons.backup.json # export of the airline_icons table (gitignored)
├── TC002-PUSH-NOTES.md       # TC002 stock-firmware HTTP push API notes
└── .env                      # n8n + OpenSky credentials (gitignored, chmod 600)

../TailFin/tailfin/           # source tail logos, <IATA>.webp / <ICAO>.webp (separate folder, not in git)
../Mini Flightwall/matrix64_aircraft_workflow.local.json   # must stay identical to the .local.json here
```

## Before you publish this repo

- **`.env`** holds your n8n API key — already gitignored, do not remove that entry.
- **`tailfin/`** contain airline tail logos and derivatives of them —
  these are third-party trademarked assets, not yours to redistribute, so
  they're gitignored by default. Publish the pipeline (the scripts), not the
  logo files themselves. If you fork this for your own use, regenerate them
  locally from your own source images.
- **`*.local.json`** and **`airline_icons.backup.json`** hold your real
  config and the logo images — gitignored, keep them that way.
- **`n8n_tc001_aircraft_workflow.json` / `n8n_tc002_aircraft_workflow.json`**
  — only ever regenerate them with `export_template.py`, which placeholders
  the Config values and data-table ids and refuses to write if a real value
  leaks through. Don't hand-edit real values into them. The frozen
  `n8n_tc002_ulanzi_firmware_aircraft_workflow.json` already has placeholders.
