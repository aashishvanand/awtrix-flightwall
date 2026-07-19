#!/bin/bash
set -euo pipefail

CLOCK_IP="YOUR_CLOCK_IP"
ICONS_DIR="./icons"
STATE_FILE="./.upload_state.json"

[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

ok=0
fail=0
skipped=0

tmp_state=$(mktemp)
cp "$STATE_FILE" "$tmp_state"

for f in "$ICONS_DIR"/*.gif; do
  name=$(basename "$f")
  hash=$(shasum -a 256 "$f" | cut -d' ' -f1)
  prev_hash=$(python3 -c "
import json, sys
with open('$tmp_state') as fh:
    state = json.load(fh)
print(state.get('$name', ''))
")

  if [ "$hash" = "$prev_hash" ]; then
    skipped=$((skipped+1))
    continue
  fi

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
done

cp "$tmp_state" "$STATE_FILE"
rm -f "$tmp_state"

echo
echo "Uploaded $ok, skipped $skipped (unchanged), failed $fail"
