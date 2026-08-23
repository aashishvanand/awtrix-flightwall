#!/bin/bash
set -euo pipefail

CLOCK_IP="${CLOCK_IP:-YOUR_CLOCK_IP}"
ICONS_DIR="./icons"
STATE_FILE="./.upload_state.json"
ALL_REGIONS="asia europe americas africa_me oceania global"
REGIONS=""

FORCE=0
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
  arg="${args[$i]}"
  case "$arg" in
    -f|--force)
      FORCE=1
      ;;
    --regions)
      i=$((i+1))
      REGIONS="${args[$i]//,/ }"
      ;;
    --clean)
      rm -f "$STATE_FILE"
      echo "Cleared upload state file ($STATE_FILE)."
      exit 0
      ;;
    -h|--help)
      echo "Usage: $0 [options]"
      echo ""
      echo "Bulk uploads icons in $ICONS_DIR to an AWTRIX NG clock at $CLOCK_IP."
      echo "icons/ is split into subfolders: $ALL_REGIONS, plus defunct/ and review/"
      echo "(defunct/review are never uploaded, even with --regions all)."
      echo ""
      echo "Options:"
      echo "  --regions <list>  Comma-separated subfolders to upload (default: all six above)."
      echo "                    e.g. --regions asia,global for a Singapore-based clock."
      echo "  -f, --force       Force re-upload all icons regardless of state file"
      echo "  --clean           Reset/delete local upload state file ($STATE_FILE)"
      echo "  -h, --help        Show this help message"
      exit 0
      ;;
  esac
  i=$((i+1))
done

if [ -z "$REGIONS" ]; then
  REGIONS="$ALL_REGIONS"
fi

for r in $REGIONS; do
  case " $ALL_REGIONS " in
    *" $r "*) ;;
    *)
      echo "Error: unknown region '$r'. Valid regions: $ALL_REGIONS"
      exit 1
      ;;
  esac
  if [ ! -d "$ICONS_DIR/$r" ]; then
    echo "Error: $ICONS_DIR/$r does not exist."
    exit 1
  fi
done

if [ "$CLOCK_IP" = "YOUR_CLOCK_IP" ]; then
  echo "Error: CLOCK_IP is set to placeholder 'YOUR_CLOCK_IP'."
  echo "Set CLOCK_IP inside upload_icons.sh or run: CLOCK_IP=192.168.x.x $0"
  exit 1
fi

[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

ok=0
fail=0
skipped=0

if [ "$FORCE" -eq 1 ]; then
  echo "Force upload enabled: re-uploading icons from [$REGIONS] to AWTRIX (${CLOCK_IP})..."
else
  echo "Uploading icons from [$REGIONS] to AWTRIX (${CLOCK_IP})..."
fi

tmp_state=$(mktemp)
cp "$STATE_FILE" "$tmp_state"

list_tmp=$(mktemp)
skip_tmp=$(mktemp)

# Fast batch hash calculation & state check using python
python3 -c "
import os, glob, hashlib, json

icons_dir = '$ICONS_DIR'
regions = '$REGIONS'.split()
state_file = '$tmp_state'
force = bool($FORCE)

with open(state_file) as fh:
    state = json.load(fh)

files = sorted(f for r in regions for f in glob.glob(os.path.join(icons_dir, r, '*.gif')))
to_upload = []
skipped_count = 0

for f in files:
    name = os.path.basename(f)
    with open(f, 'rb') as fh:
        h = hashlib.sha256(fh.read()).hexdigest()
    if not force and state.get(name) == h:
        skipped_count += 1
    else:
        to_upload.append((f, name, h))

with open('$skip_tmp', 'w') as fh:
    fh.write(str(skipped_count))

with open('$list_tmp', 'w') as fh:
    for f, name, h in to_upload:
        fh.write(f'{f}\t{name}\t{h}\n')
"

skipped=$(cat "$skip_tmp")

if [ -f "$list_tmp" ]; then
  while IFS=$'\t' read -r f name hash; do
    [ -z "$f" ] && continue
    if curl -s -f --max-time 10 \
        -F "file=@${f}" \
        "http://${CLOCK_IP}/api/v1/files?dir=/ICONS" -o /dev/null; then
      echo "OK   $name"
      ok=$((ok+1))
      python3 -c "
import json
with open('$tmp_state') as fh:
    state = json.load(fh)
state['$name'] = '$hash'
with open('$tmp_state', 'w') as fh:
    json.dump(state, fh, indent=2, sort_keys=True)
"
    else
      echo "FAIL $name"
      fail=$((fail+1))
    fi
  done < "$list_tmp"
fi

rm -f "$list_tmp" "$skip_tmp"
cp "$tmp_state" "$STATE_FILE"
rm -f "$tmp_state"

echo
echo "Uploaded $ok, skipped $skipped (unchanged), failed $fail"
if [ "$skipped" -gt 0 ] && [ "$FORCE" -eq 0 ]; then
  echo "Tip: Run '$0 --force' to force re-uploading all unchanged icons to AWTRIX."
fi

