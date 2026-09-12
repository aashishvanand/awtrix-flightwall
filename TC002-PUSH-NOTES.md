# Pushing content to a Ulanzi TC002 — reusable notes

Learned the hard way while wiring the aircraft-overhead feed to a second
clock. Written up so the same pattern can be dropped into any other n8n
workflow (weather, calendar, etc.) without re-discovering all of this.

## Use HTTP, not MQTT

TC002 supports both, but MQTT needs a broker, a device-specific topic
prefix (`ulanzi_<last4 of device id>`, only discoverable by sniffing
traffic — it's not documented anywhere), and a `custom/<app>` sub-topic.
HTTP needs none of that and is what the existing AWTRIX NG pipeline in
this repo already looks like structurally, so it's the easier fit:

```
POST http://<CLOCK_IP>/api/custom?name=<app_name>
Content-Type: application/json
```

`<app_name>` is your own arbitrary identifier (e.g. `adsb`, `weather`) —
each distinct name is a separate "slot" on the clock. Empty body `{}`
removes that app.

No auth. Source: [atomicstack/tc002-customisation](https://github.com/atomicstack/tc002-customisation)
(`HTTP-API.md`, `CUSTOM-APP.md`) — a reverse-engineered spec, confirmed
correct end-to-end against a real TC002 in this project.

## Payload schema

```json
{
  "duration": 10,
  "text": [
    { "content": "HELLO", "fontHeight": 10, "x": 0, "y": 3, "color": "#FFFFFF" }
  ],
  "image": [
    { "data": "data:image/gif;base64,<...>", "position": [0, 0] }
  ]
}
```

- `duration` — seconds this app stays on screen once it's the clock's
  active app.
- `text[]` — **must be an array**, even for one line. Fields: `content`,
  `fontHeight`, `x`, `y`, `color` (`#RRGGBB`), optional `align`
  (`center`/`right`), optional `rect` (`[x,y,w,h]` clip box).
- `image[]` — see below. There is **no `icon: "name"` field** — TC002 has
  no icon-upload/reference-by-ID system like AWTRIX. Every push embeds the
  actual pixel data.

## Font size

`fontHeight` is a real, continuous parameter, not just "10 vs. not-10" as
the reverse-engineered docs guessed:

| fontHeight | Result |
|---|---|
| 10 | standard/large — confirmed by every doc |
| 6, 7, 8 | all render **identically** to each other — the renderer snaps to a discrete "medium" tier |
| 5 | distinctly smaller, clean and legible — good for two-line layouts |
| 4, 3 | render **identically** to each other and about the same as 5 — floor of a "small" tier, no benefit going below 5 |

Practical takeaway: use `10` for one full-height line, `5` for two
stacked lines (`y:1` and `y:9` leaves clean spacing on the 16px-tall
panel — see the `text[]` example in `n8n_aircraft_workflow.local.json`'s
`Build Payload TC002` node).

## No native scroll

Quote from the reverse-engineered docs: **"The device does not scroll
text."** Long content is just clipped at the display edge — there's no
marquee. The documented workaround (re-push the frame every 0.4–0.5s with
`x` shifted 5px left) is real but not practical from an n8n cron-style
workflow. Design around it instead:

- Keep each line within the ~6px/char budget at `fontHeight:10` (~8-9
  chars centered on the 52px-wide panel), or
- Use `fontHeight:5` for two shorter lines instead of one long scrolling
  one.

## Icons: embed inline, every push

Since there's no icon-by-ID system, any icon must travel as an inline
base64 GIF in the same request:

```json
"image": [{ "data": "data:image/gif;base64,<...>", "position": [0, 0] }]
```

- Generate icons with `convert_tiles.py --size 16` (a `--size` flag was
  added to this repo's existing 8x8-only converter — same emblem-focus
  cropping logic, just parameterized). 16x16 fills the panel's full
  height nicely; 8x8 (the AWTRIX-native size) also works, just smaller.
- Because there's nothing to upload once and reference later, a
  base64 lookup map has to travel with the workflow itself. In practice:
  build a `{iata: base64string}` (or whatever your key is) map once with
  a small Python script and paste it as a `const` inside the n8n Code
  node that builds the payload — see `Build Payload TC002` in
  `n8n_aircraft_workflow.local.json` for the exact pattern. ~280 icons at
  16x16 costs ~190KB of JSON text — completely fine to inline in a Code
  node.
- If a project has no pre-existing icon set (e.g. weather conditions),
  it's simplest to skip `image` entirely and ship text-only — don't
  invent an icon pipeline just for this.

## Text content constraints

- **ASCII only** (0x20–0x7E). No `°`, no smart quotes, no emoji — strip
  or substitute them (`"28C 75%"` not `"28°C 75%"`).
- No automatic case-folding like AWTRIX NG's `textCase: 'upper'` — if you
  want uppercase, `.toUpperCase()` the string yourself before building
  the payload.

## Multiple apps on one clock

Same clock, same `/api/custom` endpoint, different `name=` query param
per logical app (`adsb`, `current_weather`, `aqi`, ...). The clock cycles
between whichever apps are currently active, same mental model as
AWTRIX NG's per-app push endpoint. To clear one, `POST` an empty object
`{}` to that same `name=`.

## Worked example: wiring a second n8n push

Pattern used in `n8n_aircraft_workflow.local.json` for anyone adding this
to another workflow:

1. Add `CLOCK2_IP` to the existing `Config` node.
2. Add a `Build Payload TC002` Code node that reads the same upstream
   data your existing AWTRIX-NG payload builder uses, and reshapes it
   into the schema above (array `text[]`, optional inline `image[]`,
   ASCII/uppercase content).
3. Add a `Push to Clock 2 (TC002)` HTTP node: `POST
   http://{{CLOCK2_IP}}/api/custom?name=<app>`, body = that node's output.
4. Wherever the workflow currently clears the AWTRIX NG app (e.g. a
   `DELETE` node when there's nothing to show), add a parallel `POST
   .../api/custom?name=<app>` with body `{}` for TC002.

## References

- [atomicstack/tc002-customisation](https://github.com/atomicstack/tc002-customisation) — the actual protocol spec (HTTP-API.md, MQTT.md, CUSTOM-APP.md), reverse-engineered but verified correct here.
- [cailurus/PixDeck](https://github.com/cailurus/PixDeck) — reference client (`pixbar_core.py`) confirming `fontHeight:10` default, 6px/char width budget, "no scroll" design.
- [UlanziTechnology/Ulanzi-U-Clock-TC002](https://github.com/UlanziTechnology/Ulanzi-U-Clock-TC002) — official plugin-app SDK (Ulanzi Studio side), useful for browsing community `apps/mqtt/*` examples but not authoritative on the wire protocol itself.
