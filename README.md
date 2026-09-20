# Ulanzi Feeder — AWTRIX Aircraft Overhead Display

Turns a Ulanzi Pixel Clock (**AWTRIX NG** firmware — the actively-maintained
successor to AWTRIX 3) into a live "what's flying over my house" display:
airline tail-logo icon on the left, flight number and origin-destination
route scrolling next to it.

```
Schedule Trigger (n8n, every 30s)
  -> [OpenSky Network API, FlightRadar24 live feed] in parallel (bounding box around home coordinates)
  -> merge + de-duplicate -> pick nearest in-flight aircraft
  -> [adsbdb.com, FlightRadar24 live feed] in parallel (callsign -> route + airline)
  -> merge + compare -> trust whichever source is live/plausible
  -> PUT to AWTRIX NG pushed-app API
```

> This repo targets **AWTRIX NG's `/api/v1/*` HTTP API**, not the older
> AWTRIX 3 `/api/custom` API. If your clock is still on AWTRIX 3, either flash
> it to NG first (see below) or adapt the endpoints per the
> [AWTRIX 3 → NG migration guide](https://blueforcer.github.io/awtrix-ng/guides/migrating-from-awtrix3/).

## Hardware / firmware

**Supported firmware:** [AWTRIX NG](https://blueforcer.github.io/awtrix-ng/) — tested on a Ulanzi TC001
(classic ESP32, 4MB flash).

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

The clock's web UI lives at `http://<CLOCK_IP>` — icons, apps, and settings all
live there.

## Icon pipeline

AWTRIX icons must be **8x8 (GIF) or 8x8 (JPG), no transparency** — the renderer
glitches on alpha channels. This repo converts real airline tail-logo images down
to that format.

```
tailfin/ -> source tail-logo images, one per IATA code, e.g. tailfin/SQ.webp
   |
   v  convert_tiles.py
icons/   -> 8x8 black-background GIFs, e.g. icons/sq_logo.gif
   |
   v  upload_icons.sh
AWTRIX device  -> /ICONS/sq_logo.gif
```

### 1. `convert_tiles.py`

Converts everything in `tailfin/` to 8x8 icons in `icons/`:

- **Smart Emblem Focus**: Detects the actual logo mark inside the tailfin (birds, cranes, flags, symbols) and crops tightly around it so emblem details fill 6x6 or 7x7 of the 8x8 matrix instead of 2x2.
- **Aspect Ratio & Centering**: Fits logos proportionally and centers them on an 8x8 black canvas with clean 1px padding.
- **Multi-Stage Downscaling**: Uses unsharp masking and two-pass reduction to maintain sharp contrast boundaries.
- **No-Dithering Quantization**: Saves as clean solid-pixel indexed GIFs without Floyd-Steinberg dot-matrix noise.

```bash
python3 -m venv venv && venv/bin/pip install pillow numpy
venv/bin/python convert_tiles.py                 # Default: emblem focus mode
# Or choose a specific mode:
venv/bin/python convert_tiles.py --mode emblem   # Zoomed emblem mark (recommended)
venv/bin/python convert_tiles.py --mode fin      # Full tailfin shape
venv/bin/python convert_tiles.py --mode tight    # Strict bounding box
```

Output filenames follow `{iata-lowercase}_logo.gif`, e.g. `tailfin/SQ.webp` ->
`icons/sq_logo.gif`. Re-run any time you add new files to `tailfin/` — it processes
the whole folder. Newly generated icons land flat in `icons/`; sort them into the
region subfolders below before uploading (see `icons/classification.txt`).

### 2. `icons/` layout — region subfolders

`icons/` is split into subfolders so a single clock only has to carry the
airlines it will realistically see:

```
icons/
├── asia/        africa_me/   americas/   europe/   oceania/   global/   # active & state carriers, by region / intercontinental reach
│                                                                         # (global = default icon 00 + long-haul intercontinental flag carriers)
├── defunct/     # confirmed ceased-operations / merged-away brands — kept as files, excluded from uploads
└── classification.txt   # code -> {folder, status, continent, sg_relevant, name, note} — the research behind this split
```

**Why this exists:** LittleFS allocates a fixed minimum block per file
regardless of how small it is. A 4MB board's ~512KB partition filled up at
347 tiny icons even though the actual image data was only ~77KB — file
*count* is the real budget, not byte size. `classification.txt` records the
status/continent call for all 319 known codes so a future cleanup pass
doesn't have to re-research every airline from scratch — update that file
by hand (or regenerate it) whenever you add or reclassify a code.

### 3. `upload_icons.sh`

Uploads a chosen set of region subfolders to the clock via AWTRIX NG's file
API (`POST /api/v1/files?dir=/ICONS`). `defunct/` is never uploaded, even
with the default region list.

```bash
./upload_icons.sh                       # Uploads all six regions (asia, europe, americas, africa_me, oceania, global)
./upload_icons.sh --regions asia,global # Only Asia + long-haul global carriers, e.g. a Singapore-based clock
./upload_icons.sh --force               # Force re-uploads ALL icons in the selected regions (useful after clock reset)
./upload_icons.sh --clean               # Clears local upload state cache file
```

Set `CLOCK_IP` at the top of the script (or run `CLOCK_IP=192.168.x.x ./upload_icons.sh`).

**Note on the upload API:** unlike AWTRIX 3 (whose file editor was
ESPAsyncWebServer's `SPIFFSEditor` at `/edit`, needing the destination path
embedded in the multipart `filename` field), NG uses a proper REST endpoint —
the target directory is a `?dir=` query param and the uploaded filename
(minus extension) becomes the icon's ID, e.g. `sq_logo.gif` → icon `sq_logo`.
The filesystem partition on a 4MB board is small (~512KB), and each icon
here costs roughly one filesystem block (~1.3KB) regardless of its own
~250-byte size — so it's file count, not KB, that determines how many fit.
Uploading in a tight loop can transiently stress the device's heap — the
script uploads one file at a time, which has worked reliably.

## n8n workflow

`n8n_aircraft_workflow.json` is an importable n8n workflow. Import it via n8n's
**Import from File** and fill in the placeholders in the first **Config** node:

| Field | What it is |
|---|---|
| `HOME_LAT` / `HOME_LON` | Your coordinates — used as the center of the aircraft search radius |
| `RADIUS_DEG` | Bounding-box half-width in degrees (`0.15` ≈ 15km) |
| `CLOCK_IP` | The AWTRIX device's static IP |
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | From a free [OpenSky Network](https://opensky-network.org) account (Account Settings -> API Client) |

To reuse this for a second location (a second home, a different feeder site):
duplicate the workflow and change only those four/six Config values — nothing
else needs editing.

**Flow:**

1. **Every 30s** — Schedule Trigger. (OpenSky's free tier is ~4000 API
   credits/day; 30s polling stays comfortably inside that.)
2. **Position stage, run in parallel:**
   - **Get OpenSky Token** — OAuth2 client-credentials exchange, then
     **Fetch Nearby States** — OpenSky's bounding-box query for aircraft near
     home coordinates.
   - **Fetch Nearby States (FR24)** — the same bounding box against
     FlightRadar24's live feed (the unofficial
     `data-cloud.flightradar24.com/zones/fcgi/feed.js` endpoint the
     [FlightRadarAPI](https://pypi.org/project/FlightRadarAPI/) Python/Node
     wrappers call), using its `bounds` parameter.
   - **Merge Position Data** — waits for both, joins them into one item.
3. **Nearest Aircraft** (Code node) — unions both position lists, de-duplicated
   by `icao24` (FR24 reports it uppercase, OpenSky lowercase; OpenSky's entry
   wins on overlap since it's the documented, sanctioned source — FR24 only
   fills in coverage gaps, e.g. an aircraft one network's ground receivers
   missed but the other's caught), then haversine-distance-sorts the combined
   pool and keeps the closest.
4. **Aircraft Found?** — branches to clear the display if nothing's overhead.
5. **Route stage, run in parallel:**
   - **Lookup Route (adsbdb)** — free lookup at
     [adsbdb.com](https://api.adsbdb.com) resolving callsign -> origin/destination
     airports + airline IATA code.
   - **Verify Route (FR24)** — the same FR24 feed endpoint, this time filtered
     by the aircraft's airline ICAO code and matched on its exact callsign.
   - **Merge Route Data** — waits for both, joins them into one item.
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
     supplied both the route and an airline icon (derived from its flight
     number prefix, the same trick the FlightRadarAPI wrappers use).
   - The cycle is skipped entirely (previous display stays) only when
     *neither* source has anything usable.
   The full comparison (`adsbdb`, `fr24`, `match`, `trustedRoute`,
   `trustedSource`) stays on the item, visible in the n8n execution log for
   every run, so a bad upstream record is obvious at a glance.
7. **Build Payload** — constructs the AWTRIX NG pushed-app JSON from the
   verified route, e.g.:
   ```json
   { "text": "SQ123 SIN-KUL", "icon": "sq_logo", "durationMs": 5000, "textCase": "asTyped" }
   ```
   Falls back to callsign-only text (generic icon) if neither source has a
   trustworthy route.
8. **Push to Clock** — `PUT http://<CLOCK_IP>/api/v1/apps/pushed/adsb` (with
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
├── convert_tiles.py          # tailfin/*.webp -> icons/*_logo.gif
├── upload_icons.sh           # bulk-upload icons/ to the clock
├── n8n_aircraft_workflow.json # importable n8n workflow (secrets scrubbed)
├── tailfin/                  # source airline tail logos (gitignored — see below)
└── icons/                    # generated 8x8 icons — airline logos, sorted into region subfolders
```

## Before you publish this repo

- **`.env`** holds your n8n API key — already gitignored, do not remove that entry.
- **`tailfin/`** contain airline tail logos and derivatives of them —
  these are third-party trademarked assets, not yours to redistribute, so
  they're gitignored by default. Publish the pipeline (the scripts), not the
  logo files themselves. If you fork this for your own use, regenerate them
  locally from your own source images.
- **`n8n_aircraft_workflow.json`** has had its OpenSky client ID/secret replaced with
  placeholders — double-check before committing that you didn't re-paste real
  credentials in while editing.
