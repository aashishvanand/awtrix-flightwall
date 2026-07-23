# Ulanzi Feeder — AWTRIX Aircraft Overhead Display

Turns a Ulanzi Pixel Clock (AWTRIX 3 firmware) into a live "what's flying over my
house" display: airline tail-logo icon on the left, flight number and
origin-destination route scrolling next to it.

```
Schedule Trigger (n8n, every 30s)
  -> OpenSky Network API (bounding box around home coordinates)
  -> pick nearest in-flight aircraft
  -> adsbdb.com (callsign -> route + airline)
  -> POST to AWTRIX custom app API
```

## Hardware / firmware

1. Flash **AWTRIX 3** onto the clock using the official web flasher (Chrome/Edge/Opera):
   https://blueforcer.github.io/awtrix3/#/flasher
   - Connect via USB-C, click **Connect**, select the serial port, check **Erase Device**,
     click **Install AWTRIX 3**.
2. On first boot the clock broadcasts a `AWTRIX_xxxxx` WiFi hotspot
   (password `12345678`). Connect to it and hand it your home WiFi credentials.
3. Give it a static IP on your router/UniFi controller (this project uses `YOUR_CLOCK_IP` as a placeholder throughout).
4. Once it's on your network and confirmed working, block it from reaching the
   internet at your firewall/UniFi controller — it never needs WAN access, everything
   here runs over the LAN.

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
the whole folder.

### 2. `upload_icons.sh`

Bulk-uploads everything in `icons/` to the clock over its built-in LittleFS file
editor (`http://<CLOCK_IP>/edit`).

```bash
./upload_icons.sh           # Uploads only new or modified icons (cached via .upload_state.json)
./upload_icons.sh --force   # Force re-uploads ALL icons to AWTRIX (useful after clock reset)
./upload_icons.sh --clean   # Clears local upload state cache file
```

Set `CLOCK_IP` at the top of the script (or run `CLOCK_IP=192.168.x.x ./upload_icons.sh`).

**Note on the upload API:** the AWTRIX file editor is ESPAsyncWebServer's
`SPIFFSEditor`. The destination path has to be embedded in the multipart
`filename` field itself (`filename=/ICONS/sq_logo.gif`) — a separate `?path=`
query parameter is silently ignored and the file lands in the root directory
instead. `upload_icons.sh` already does this correctly.

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
2. **Get OpenSky Token** — OAuth2 client-credentials exchange.
3. **Fetch Nearby States** — bounding-box query for aircraft near home coordinates.
4. **Nearest Aircraft** (Code node) — haversine-distance-sorts the results,
   filters out grounded aircraft / empty callsigns, keeps the closest.
5. **Aircraft Found?** — branches to clear the display if nothing's overhead.
6. **Lookup Route (adsbdb)** — free lookup at [adsbdb.com](https://api.adsbdb.com)
   resolving callsign -> origin/destination airports + airline IATA code.
7. **Build Payload** — constructs the AWTRIX custom-app JSON, e.g.:
   ```json
   { "text": "SQ123 SIN-KUL", "icon": "sq_logo", "duration": 5, "textCase": 2 }
   ```
   Falls back to callsign-only text (no icon) if adsbdb has no route match.
8. **Push to Clock** — `POST http://<CLOCK_IP>/api/custom?name=adsb`.

Remember to flip the workflow to **Active** in n8n once it's configured — it
imports inactive by default.

## Repo layout

```
.
├── README.md
├── convert_tiles.py          # tailfin/*.webp -> icons/*_logo.gif
├── upload_icons.sh           # bulk-upload icons/ to the clock
├── n8n_aircraft_workflow.json # importable n8n workflow (secrets scrubbed)
├── tailfin/                  # source airline tail logos (gitignored — see below)
└── icons/                    # generated 8x8 icons — airline logos
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
