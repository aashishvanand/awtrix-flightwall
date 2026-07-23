#!/bin/bash
set -euo pipefail

CLOCK_IP="${CLOCK_IP:-YOUR_CLOCK_IP}"
ICONS_DIR="./icons"
STATE_FILE="./.upload_state.json"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    -f|--force)
      FORCE=1
      ;;
    --clean)
      rm -f "$STATE_FILE"
      echo "Cleared upload state file ($STATE_FILE)."
      exit 0
      ;;
    -h|--help)
      echo "Usage: $0 [options]"
      echo ""
      echo "Bulk uploads icons in $ICONS_DIR to AWTRIX light clock at $CLOCK_IP."
      echo ""
      echo "Options:"
      echo "  -f, --force    Force re-upload all icons regardless of state file"
      echo "  --clean        Reset/delete local upload state file ($STATE_FILE)"
      echo "  -h, --help     Show this help message"
      exit 0
      ;;
  esac
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
  echo "Force upload enabled: re-uploading all icons to AWTRIX (${CLOCK_IP})..."
else
  echo "Uploading icons to AWTRIX (${CLOCK_IP})..."
fi

tmp_state=$(mktemp)
cp "$STATE_FILE" "$tmp_state"

list_tmp=$(mktemp)
skip_tmp=$(mktemp)

# Fast batch hash calculation & state check using python
python3 -c "
import os, glob, hashlib, json

icons_dir = '$ICONS_DIR'
state_file = '$tmp_state'
force = bool($FORCE)

with open(state_file) as fh:
    state = json.load(fh)

files = sorted(glob.glob(os.path.join(icons_dir, '*.gif')))
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
        -F "data=@${f};filename=/ICONS/${name}" \
        "http://${CLOCK_IP}/edit" -o /dev/null; then
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

