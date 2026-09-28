"""
Generates the committed per-clock n8n templates from the live workflow copy
(n8n_aircraft_workflow.local.json):

  n8n_tc001_aircraft_workflow.json   TC001 (AWTRIX NG) only
  n8n_tc002_aircraft_workflow.json   TC002 only

The live workflow drives every display from one poll (splitting it would
multiply OpenSky/FR24 calls), so each template is that workflow pruned to one
clock: the other displays' nodes, the live-only missing-icon debug log and
every Config field nothing reads any more are removed. Every location- and
instance-specific value is replaced by a placeholder.

It refuses to write if a template has dead or broken wiring (a reference to a
removed node, a node unreachable from the trigger, an unused or missing Config
field) or if any real value from the local Config or .env leaks through.

Usage:
  venv/bin/python export_template.py
"""
import copy
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "n8n_aircraft_workflow.local.json")

TRIGGER = "Every 30s"
CONFIG = "Config (edit per location)"

# Local debugging only -- logs airlines that fell back to the generic tail to
# the missing_tailfin data table. Never shipped in a template.
LIVE_ONLY_NODES = ["Missing Icon?", "Missing Icon Row", "Log Missing Icon (Data Table)"]

# Exact code edits for the templates, (node, parameter, old, new). In the live
# workflow Build Payload also outputs iconCode for the missing-icon log; the
# template has no log, so it returns the clock payload directly. Each `old`
# must match exactly once, so a change to the live code stops the export
# instead of producing a half-edited template.
TEMPLATE_EDITS = {
    "tc001": [
        ("Build Payload", "jsCode", "const iconCode = iconRow ? iconRow.code : '00';\n", ""),
        ("Build Payload", "jsCode",
         "// iconCode rides alongside (not inside) the clock payload so Missing Icon?\n"
         "// can log fallbacks; Push to Clock sends only `payload`.\n"
         "return [{ json: { payload, iconCode } }];",
         "return [{ json: payload }];"),
        ("Push to Clock", "jsonBody", "={{ JSON.stringify($json.payload) }}", "={{ JSON.stringify($json) }}"),
    ],
    "tc002": [],
}

# Nodes that belong to exactly one display; everything else is shared.
DISPLAY_NODES = {
    "tc001": ["Build Payload", "Push to Clock", "Clear Clock"],
    "tc002": ["Build Payload TC002", "Push to Clock 2 (TC002)", "Clear Clock 2 (TC002)"],
    # The 64x64 matrix has its own template in the Mini Flightwall repo.
    "matrix": ["Lookup Aircraft (adsbdb)", "Merge Matrix Data", "Build Payload (Matrix64)",
               "Push to Matrix", "Clear Matrix"],
}
TEMPLATES = {
    "tc001": ("n8n_tc001_aircraft_workflow.json", "Ulanzi TC001 - Aircraft Overhead"),
    "tc002": ("n8n_tc002_aircraft_workflow.json", "Ulanzi TC002 - Aircraft Overhead"),
}

CONFIG_PLACEHOLDERS = {
    "HOME_LAT": 0,
    "HOME_LON": 0,
    "RADIUS_DEG": None,  # not location-identifying; kept as-is
    "CLOCK_IP": "YOUR_CLOCK_IP",
    "CLOCK2_IP": "YOUR_CLOCK2_IP",
    "MATRIX_IP": "YOUR_MATRIX_IP",
    "OPENSKY_CLIENT_ID": "PASTE_CLIENT_ID",
    "OPENSKY_CLIENT_SECRET": "PASTE_CLIENT_SECRET",
    "N8N_URL": "YOUR_N8N_HOST:5678",
    "N8N_API_KEY": "PASTE_N8N_API_KEY",
    "MISSING_ICON_TABLE_ID": "YOUR_MISSING_ICON_TABLE_ID",
}
# Data Table nodes reference their table by this instance's id.
TABLE_PLACEHOLDERS = {"Lookup Icon": "YOUR_AIRLINE_ICONS_TABLE_ID"}


def load_env():
    env = {}
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip().strip("'\"")
    return env


def config_assignments(wf):
    node = next(n for n in wf["nodes"] if n["name"] == CONFIG)
    return node["parameters"]["assignments"]["assignments"]


def config_refs(node):
    # Config values are read as $('Config ...').first().json.FIELD, or via an
    # alias: const cfg = $('Config ...').first().json; ... cfg.FIELD
    text = json.dumps(node["parameters"])
    access = r"\$\('" + re.escape(CONFIG) + r"'\)\.first\(\)\.json"
    refs = set(re.findall(access + r"\.([A-Za-z0-9_]+)", text))
    for alias in re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*" + access + r"\s*;", text):
        refs |= set(re.findall(r"\b" + alias + r"\.([A-Za-z0-9_]+)", text))
    return refs


def prune(wf, keep_display):
    drop = {n for d, nodes in DISPLAY_NODES.items() if d != keep_display for n in nodes} | set(LIVE_ONLY_NODES)
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in drop]
    conns = {}
    for src, outputs in wf["connections"].items():
        if src in drop:
            continue
        conns[src] = {"main": [[c for c in branch if c["node"] not in drop] for branch in outputs["main"]]}
    wf["connections"] = conns

    nodes = {n["name"]: n for n in wf["nodes"]}
    for node, param, old, new in TEMPLATE_EDITS[keep_display]:
        value = nodes[node]["parameters"][param]
        if value.count(old) != 1:
            raise SystemExit(f"{node}.{param} no longer matches the template edit -- update TEMPLATE_EDITS:\n  {old!r}")
        nodes[node]["parameters"][param] = value.replace(old, new)

    # Config fields nothing reads any more (other clocks' IPs, the log's n8n settings).
    used = set().union(*(config_refs(n) for n in wf["nodes"]))
    assignments = config_assignments(wf)
    assignments[:] = [a for a in assignments if a["name"] in used]
    return wf


def check(wf, label):
    names = {n["name"] for n in wf["nodes"]}
    problems = []
    for n in wf["nodes"]:
        for ref in set(re.findall(r"\$\('([^']+)'\)", json.dumps(n["parameters"]))):
            if ref not in names:
                problems.append(f"{n['name']} references removed node '{ref}'")
    for src, outputs in wf["connections"].items():
        for branch in outputs["main"]:
            for c in branch:
                if c["node"] not in names:
                    problems.append(f"{src} connects to missing node '{c['node']}'")
    seen, todo = set(), [TRIGGER]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        todo += [c["node"] for b in wf["connections"].get(cur, {}).get("main", []) for c in b]
    problems += [f"{n} is unreachable from '{TRIGGER}'" for n in sorted(names - seen)]
    fields = {a["name"] for a in config_assignments(wf)}
    used = set().union(*(config_refs(n) for n in wf["nodes"]))
    problems += [f"Config field {f} is never used" for f in sorted(fields - used)]
    problems += [f"Config field {f} is used but not defined" for f in sorted(used - fields)]
    if problems:
        raise SystemExit(f"{label}: refusing to write\n  " + "\n  ".join(problems))


def scrub(wf):
    """Replace real values with placeholders; return the set of real values removed."""
    secrets = set()
    for a in config_assignments(wf):
        if a["name"] not in CONFIG_PLACEHOLDERS:
            raise SystemExit(f"Config field {a['name']} has no placeholder rule -- add one first")
        placeholder = CONFIG_PLACEHOLDERS[a["name"]]
        if placeholder is None:
            continue
        # Coordinates are matched by prefix so they're caught in any formatting.
        secrets.add(str(a["value"])[:6] if a["name"] in ("HOME_LAT", "HOME_LON") else str(a["value"]))
        a["value"] = placeholder
    nodes = {n["name"]: n for n in wf["nodes"]}
    for name, placeholder in TABLE_PLACEHOLDERS.items():
        if name in nodes:
            secrets.add(nodes[name]["parameters"]["dataTableId"]["value"])
            nodes[name]["parameters"]["dataTableId"] = {"__rl": True, "mode": "id", "value": placeholder}
    return secrets


def main():
    with open(SOURCE, encoding="utf-8") as f:
        live = json.load(f)
    env = load_env()
    env_secrets = {v for k, v in env.items()
                   if k in ("n8n", "clientId", "clientSecret", "n8nurl", "n8nworkflow") and v}

    for display, (filename, name) in TEMPLATES.items():
        wf = prune(copy.deepcopy(live), display)
        check(wf, filename)
        secrets = (scrub(wf) | env_secrets) - {""}
        out = {"name": name, "nodes": wf["nodes"], "connections": wf["connections"],
               "settings": wf.get("settings", {})}
        text = json.dumps(out, indent=4)
        leaks = [s for s in secrets if s in text]
        if leaks:
            raise SystemExit(f"{filename}: refusing to write, {len(leaks)} real value(s) still present")
        with open(os.path.join(HERE, filename), "w", encoding="utf-8") as f:
            f.write(text)
        fields = [a["name"] for a in config_assignments(wf)]
        print(f"Wrote {filename}: {len(out['nodes'])} nodes, {len(text) // 1024} KB, "
              f"Config {', '.join(fields)}; checked for {len(secrets)} real values")


if __name__ == "__main__":
    main()
