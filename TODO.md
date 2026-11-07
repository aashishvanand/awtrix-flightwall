# TODO

Findings from a security and performance review of the workflows and Python tooling.
Items marked done were deployed to the live n8n workflow on 2026-11-07 (the
OpenSky secret now lives in the `OpenSky API` n8n credential). The ones
still open need doing by hand: rotating secrets, checking the n8n host's
encryption key, and network changes.

## Security

### 1. Plaintext secrets in the workflow and execution history (high)
`n8n_aircraft_workflow.local.json` keeps `OPENSKY_CLIENT_SECRET`, `N8N_API_KEY`, `MISSING_ICON_TABLE_ID` and `N8N_URL` in the Config node. Config output is saved with every execution, so these are written to the n8n executions DB every 30s. `Get/Refresh OpenSky Token` also outputs `access_token`, which gets stored too.

- [x] Replace the `Log Missing Icon (Data Table)` HTTP Request node with the native **Data Table → Row → Upsert** node. This removes `N8N_URL`, `N8N_API_KEY` and the plaintext HTTP hop.
- [x] Move OpenSky to an n8n **OAuth2 client-credentials** credential on `Fetch Nearby States`. Delete `Get/Refresh OpenSky Token` and the client id/secret from Config.
- [x] Set workflow settings `saveDataSuccessExecution: "none"` and `saveExecutionProgress: false`, and enable execution pruning. Keep full data for errors if `Compare Routes` debugging is still needed.
- [ ] Verify `N8N_ENCRYPTION_KEY` is set and backed up.
- [ ] Rotate the OpenSky secret and the n8n API key after migrating.

### 2. `availableInMCP: true` (high)
Set in the live workflow and in all three templates. The n8n MCP server returns node parameters (Config values aren't credentials, so they aren't stripped) and can execute or edit workflows.

- [x] Set `availableInMCP: false` in the live workflow and all templates, unless MCP access is deliberately used.

### 3. File permissions (quick fix)
- [x] `chmod 600 .env` (currently `-rw-r--r--`).
- [x] `chmod 600 missing_tailfin.backup.json`, since it holds callsigns/registrations correlated with the home location.

### 4. Plain HTTP and unauthenticated LAN control (medium)
n8n is reached over plain HTTP on the LAN, and `Log Missing Icon` sends the API key in a header. The clocks and matrix accept unauthenticated `PUT /api/v1/apps/pushed/...` / `POST /api/display`.

- [ ] Firewall the clocks/matrix so only the n8n host can reach them.
- [ ] Use HTTPS or localhost for n8n.
- [ ] Enable AWTRIX auth if the firmware supports it.
- [ ] Put the IoT devices on a segmented VLAN.

### 5. Python hardening (medium/low)
- [x] `sync_icons.py`: stop silently building `http://` when `n8nurl` has no scheme. Refuse or warn for non-loopback hosts so the API key doesn't go out in cleartext.
- [x] Add a pinned `requirements.txt` (Pillow, numpy) and run `pip-audit` periodically.
- [x] `export_template.py`: scrub every `dataTableId`, not only the Lookup Icon table, or switch Data Table nodes to "By Name" (`airline_icons`, `missing_tailfin`) so no instance ids are embedded.

## Performance

### 1. Icon conversion bottleneck, ~7× speedup available (high)
About 2.2s per size (6.7s per logo for 3 sizes). 82% of that is `ImageFilter.MinFilter(19)` in `convert_tiles.py:54`, running at full ~3000px resolution.

- [x] Erode faster: done as an exact separable numpy min filter instead of a downscaled mask, so output is byte-identical (all 357 logos verified). Originally suggested: erode a downscaled alpha mask instead (benchmarked: `00.webp` 2.28s → 0.35s, `SQ.webp` 2.62s → 0.32s, byte-identical GIFs). On a 15-logo sample, 13 were identical and 2 differed only in the detected emblem crop, so spot-check visually.
- [x] Compute the crop/emblem box once per logo and render all three sizes from it (refactor `convert()` / `sync_icons.encode`). Target: ~6.7s → ~1s per new logo.
- [x] Vectorize `rgb565()` with numpy instead of the per-pixel `struct.pack` loop.

### 2. FR24 feed fetched twice per cycle (high)
`Fetch Nearby States (FR24)` and `Verify Route (FR24)` hit the same endpoint. The bounds response already contains callsign, origin/destination (fields 11/12) and airline ICAO (field 18).

- [x] Carry the matching record through `Nearest Aircraft`, then drop `Verify Route (FR24)` and the route Merge. Saves 2,880 requests/day. Validate against live data first.

### 3. Execution history bloat (high)
2,880 executions/day, each storing the OpenSky/FR24 responses, Lookup Icon rows (~7 KB base64) and Config secrets: tens of MB/day, and it slows the n8n UI.

- [x] Covered by `saveDataSuccessExecution: "none"` + pruning (Security #1).

### 4. No HTTP timeouts (high)
All 12 HTTP nodes have `options: {}`. A hung call blocks a run while new runs start every 30s.

- [x] Set `options.timeout` to ~5–8s for external APIs and ~3s for clock/matrix pushes.
- [x] Set the workflow `executionTimeout` to ~25s.
- [x] Set `onError: continueRegularOutput` on `Push to Clock`, `Clear Clock`, `Push to Matrix` and the OpenSky token/fetch nodes, so one dead display or an OpenSky 429 doesn't fail the whole cycle. This also stops a failed push from losing `lastShownKey` in the rotation static data.

### 5. Repeated adsbdb lookups (medium)
- [x] Cache `Lookup Aircraft (adsbdb)` results by `icao24` in workflow static data with a ~24h TTL.
- [x] Upsert to `missing_tailfin` only when the missing airline changes, not every cycle.

### 6. Clock pushes every 30s even when unchanged (medium)
Pushed AWTRIX apps persist (`lifetimeMs` defaults to 0), and a re-PUT restarts scrolling.

- [x] Hash the TC001/TC002 payloads and skip identical pushes. Leave the matrix push as is, since its payload changes with altitude/progress.

### 7. Trim the FR24 feed request (low)
- [x] Use `gnd=0`, `vehicles=0` and a shorter `maxage` (~900–1800s) instead of `gnd=1`, `vehicles=1`, `maxage=14400` plus filtering in code. Check coverage at the home location first.

### 8. `sync_icons.py` I/O (low/medium)
- [x] Cache env/config: `api()` re-reads `.env` on every request.
- [ ] Reuse one HTTP session instead of opening a connection per request. (Not done: urllib has no session; the thread pool removes most of the cost.)
- [x] Call `find_table()` once per run (currently twice).
- [x] Use a small thread pool (4–6) for upserts and `prune_missing` deletes. This matters for a full `--all` sync.

## Already verified as fine
- 30s trigger: the 0.15° radius makes a 0.09 sq° bbox, which costs 1 OpenSky credit per call. That's 2,880/day, under the 4,000 standard-user budget. A missing `secondsInterval` defaults to 30.
- OpenSky token caching with a 60s safety margin is correct.
- The optional lookups (adsbdb route, FR24, Lookup Icon) already have retry/onError.
- `.gitignore` covers `.env`, `*.local.json`, backups, logos and `.claude`.
- Committed templates use placeholders. No secrets were found in git history.
