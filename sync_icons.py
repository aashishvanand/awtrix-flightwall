"""
Syncs airline logos from the TailFin folder into the n8n `airline_icons` data table.

No display stores icons -- every push carries the logo inline as base64. The
workflow's Lookup Icon node fetches the logo row from this table each run:

  code    logo code = TailFin filename, lowercased (sq, tr, asy, 00)
  icao    ICAO airline designator of the airline the logo shows -- the primary
          lookup key (FR24 feed field 18 / adsbdb airline.icao). Blank when not
          confidently known; must be unique across rows.
  iata    IATA code -- secondary key (adsbdb airline.iata). Blank for 00 and
          for ICAO-named logos.
  name    airline name (informational)
  icon8   8x8 GIF, base64  (TC001 / AWTRIX NG `icon`)
  icon16  16x16 GIF, base64 (TC002 `image[].data`)
  icon24  24x24 raw little-endian RGB565, base64 -- 1152 bytes / 1536 chars
          (64x64 matrix `iconData`, drawn straight into a uint16_t buffer)

By default only logos not yet in the table are converted (the ~3000px sources
take ~2s each), plus any icon column that's empty on an existing row -- so a
newly added size column backfills without --all. The workflow itself is never touched -- adding a logo is just
new rows.

Usage:
  venv/bin/python sync_icons.py --dry-run                   # which logos are new
  venv/bin/python sync_icons.py --icao zz=ZZA --name "zz=Zed Air"
  venv/bin/python sync_icons.py --codes sq,tr               # re-convert updated logos
  venv/bin/python sync_icons.py --icao 3y=MYU               # fix a mapping, no conversion
  venv/bin/python sync_icons.py --all                       # re-convert everything

Reads `n8n` (API key) and `n8nurl` from .env. Every write also exports the
whole table to airline_icons.backup.json (gitignored -- the logos are
third-party trademarks).
"""
import argparse
import base64
import glob
import io
import json
import os
import struct
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor

from PIL import Image

from convert_tiles import convert

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(HERE, "..", "TailFin", "tailfin")
BACKUP = os.path.join(HERE, "airline_icons.backup.json")
TABLE_NAME = "airline_icons"
COLUMNS = ("code", "icao", "iata", "name", "icon8", "icon16", "icon24")
SIZES = {"icon8": 8, "icon16": 16, "icon24": 24}
# Columns stored as raw RGB565 instead of GIF (the matrix firmware has no
# image decoder); same crop/quantize as the GIFs, only the packing differs.
RAW565 = {"icon24"}

# AWTRIX NG rejects request bodies over 8192 bytes; leave room for the text.
MAX_ICON_B64 = 6000


# --- n8n ---------------------------------------------------------------------

def load_env():
    env = {}
    with open(os.path.join(HERE, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    base = env["n8nurl"] if env["n8nurl"].startswith("http") else f"http://{env['n8nurl']}"
    return base.rstrip("/"), env["n8n"]


def api(method, path, body=None, query=None):
    base, key = load_env()
    url = f"{base}/api/v1{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"n8n {method} {path} failed: HTTP {e.code} {e.read().decode(errors='replace')[:500]}")


def find_table(create):
    tables = api("GET", "/data-tables", query={"limit": 250})["data"]
    match = [t for t in tables if t["name"] == TABLE_NAME]
    if match:
        table_id = match[0]["id"]
        have = {c["name"] for c in match[0].get("columns", [])}
        for col in COLUMNS:
            if col in have:
                continue
            if not create:
                print(f"Column '{col}' missing -- it will be added")
                continue
            api("POST", f"/data-tables/{table_id}/columns", {"name": col, "type": "string"})
            print(f"Added column '{col}' to '{TABLE_NAME}'")
        return table_id
    if not create:
        print(f"No '{TABLE_NAME}' data table yet -- it will be created")
        return None
    # First run on a fresh n8n: the Lookup Icon node then needs this table selected.
    table = api("POST", "/data-tables", {"name": TABLE_NAME,
                                         "columns": [{"name": c, "type": "string"} for c in COLUMNS]})
    print(f"Created '{TABLE_NAME}' data table (id {table['id']}) -- select it in the Lookup Icon node")
    return table["id"]


def read_rows(table_id):
    rows, cursor = [], None
    while True:
        query = {"limit": 250, **({"cursor": cursor} if cursor else {})}
        page = api("GET", f"/data-tables/{table_id}/rows", query=query)
        rows += page["data"]
        cursor = page.get("nextCursor")
        if not cursor:
            break
    return {r["code"]: {c: r.get(c) or "" for c in COLUMNS} for r in rows}


def upsert(table_id, row):
    api("POST", f"/data-tables/{table_id}/rows/upsert", {
        "filter": {"type": "and", "filters": [{"columnName": "code", "condition": "eq", "value": row["code"]}]},
        "data": row,
        "returnData": False,
    })


# --- icons -------------------------------------------------------------------

def rgb565(gif_bytes):
    """Little-endian RGB565, row-major -- what the matrix firmware reads."""
    img = Image.open(io.BytesIO(gif_bytes)).convert("RGB")
    out = bytearray()
    for r, g, b in img.get_flattened_data():
        out += struct.pack("<H", ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3))
    return bytes(out)


def encode(job):
    path, col, mode = job
    buf = io.BytesIO()
    convert(path, buf, mode=mode, size=SIZES[col])
    data = buf.getvalue()
    if col in RAW565:
        data = rgb565(data)
        if len(data) != SIZES[col] ** 2 * 2:
            raise ValueError(f"{path}: {col} packed to {len(data)} bytes")
    return base64.b64encode(data).decode("ascii")


def source_paths(input_dir):
    paths = sorted(glob.glob(os.path.join(input_dir, "*.webp")))
    return {os.path.splitext(os.path.basename(p))[0].lower(): p for p in paths}


def convert_codes(cols_by_code, paths_by_code, mode):
    # Fan out across cores; each source is a ~3000px webp.
    jobs = [(code, col) for code in cols_by_code for col in cols_by_code[code]]
    with ProcessPoolExecutor() as pool:
        results = pool.map(encode, [(paths_by_code[c], col, mode) for c, col in jobs])
        out = {code: {} for code in cols_by_code}
        for (code, col), b64 in zip(jobs, results):
            if len(b64) > MAX_ICON_B64:
                raise SystemExit(f"{code}: {col} is {len(b64)} base64 chars, too big to push")
            out[code][col] = b64
    return out


def parse_pairs(values, what):
    pairs = {}
    for v in values:
        if "=" not in v:
            raise SystemExit(f"--{what} expects CODE=VALUE, got {v!r}")
        code, value = v.split("=", 1)
        pairs[code.strip().lower()] = value.strip()
    return pairs


def main():
    parser = argparse.ArgumentParser(description="Sync TailFin logos into the n8n airline_icons data table.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="TailFin folder of <CODE>.webp tail logos")
    parser.add_argument("--mode", choices=["emblem", "fin", "tight"], default="emblem",
                        help="Cropping mode passed to convert_tiles.convert()")
    parser.add_argument("--codes", default="", help="Comma-separated codes to re-convert (updated logos)")
    parser.add_argument("--all", action="store_true", help="Re-convert every logo")
    parser.add_argument("--icao", action="append", default=[], metavar="CODE=ICAO",
                        help="Set a row's ICAO designator (empty value clears it); repeatable")
    parser.add_argument("--name", action="append", default=[], metavar="CODE=NAME",
                        help="Set a row's airline name; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing")
    args = parser.parse_args()

    sources = source_paths(args.input)
    if "00" not in sources:
        raise SystemExit(f"No 00.webp (generic fallback tail) in {args.input}")
    table_id = find_table(create=not args.dry_run)
    rows = read_rows(table_id) if table_id else {}

    icao_set = {k: v.lower() for k, v in parse_pairs(args.icao, "icao").items()}
    name_set = parse_pairs(args.name, "name")
    forced = {c.strip().lower() for c in args.codes.split(",") if c.strip()}
    unknown = (forced | set(icao_set) | set(name_set)) - set(sources) - set(rows)
    if unknown:
        raise SystemExit(f"No source logo or table row for: {', '.join(sorted(unknown))}")

    new = sorted(set(sources) - set(rows))
    convert_set = set(sources) if args.all else set(new) | forced
    # Which icon columns to (re)convert per code: every one for new/forced
    # logos, otherwise just the ones still empty (e.g. a newly added size).
    convert_cols = {c: list(SIZES) for c in convert_set}
    for code, row in rows.items():
        if code in sources and code not in convert_set:
            empty = [col for col in SIZES if not row[col]]
            if empty:
                convert_cols[code] = empty
    backfill = sorted(set(convert_cols) - convert_set)
    orphans = sorted(set(rows) - set(sources))

    # Build the rows that will be written.
    changed = {}
    for code in sorted(set(convert_cols) | set(icao_set) | set(name_set)):
        row = dict(rows.get(code) or {
            "code": code,
            # 2-char logo names are IATA codes; 3-letter ones are ICAO designators.
            "icao": code if len(code) == 3 and code.isalpha() else "",
            "iata": code if len(code) == 2 and code != "00" else "",
            "name": "", "icon8": "", "icon16": "", "icon24": "",
        })
        if code in icao_set:
            row["icao"] = icao_set[code]
        if code in name_set:
            row["name"] = name_set[code]
        changed[code] = row

    # ICAO designators must be unique, or the lookup could pick either logo.
    merged = {**rows, **changed}
    owners = {}
    for code, row in merged.items():
        if row["icao"]:
            owners.setdefault(row["icao"], []).append(code)
    dupes = {k: v for k, v in owners.items() if len(v) > 1}
    if dupes:
        raise SystemExit("ICAO designator on more than one row: "
                         + "; ".join(f"{k.upper()} -> {', '.join(v)}" for k, v in dupes.items()))

    print(f"Source logos: {len(sources)}, table rows: {len(rows)}")
    print(f"New: {', '.join(new) or '-'}")
    if forced or args.all:
        print(f"Re-convert: {'all' if args.all else ', '.join(sorted(forced))}")
    if backfill:
        cols = sorted({col for c in backfill for col in convert_cols[c]})
        print(f"Backfill {', '.join(cols)}: {len(backfill)} row(s)")
    for code in sorted(changed):
        before, after = rows.get(code, {}), changed[code]
        meta = [f"{c}={after[c] or '-'}" for c in ("icao", "iata", "name")
                if before.get(c, None) != after[c]]
        if meta:
            print(f"  {code}: {', '.join(meta)}")
    no_icao = sorted(c for c in new if not changed[c]["icao"])
    if no_icao:
        print(f"New with no ICAO (only matched by IATA): {', '.join(no_icao)} -- set with --icao CODE=ICAO")
    if orphans:
        print(f"Rows with no source logo (kept): {', '.join(orphans)}")
    if args.dry_run or not changed:
        print("Nothing written." if changed else "Already up to date. Nothing written.")
        return

    if table_id is None:
        table_id = find_table(create=True)
    if convert_cols:
        for code, icons in convert_codes(convert_cols, sources, args.mode).items():
            changed[code].update(icons)
    for code, row in changed.items():
        upsert(table_id, row)
    print(f"Upserted {len(changed)} row(s) into '{TABLE_NAME}'")

    final = read_rows(table_id)
    missing = [c for c in changed if final.get(c) != changed[c]]
    if missing:
        raise SystemExit(f"Table doesn't match what was written for: {', '.join(missing)}")
    with open(BACKUP, "w", encoding="utf-8") as f:
        json.dump([final[c] for c in sorted(final)], f, indent=1)
    print(f"{len(final)} rows verified; backup written to {os.path.relpath(BACKUP, HERE)}")


if __name__ == "__main__":
    main()
