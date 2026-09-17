#!/bin/bash
# ---------------------------------------------------------------------------
# Show what the FBS filter would keep and drop from this week's college feed
# on the LIVE site. READ ONLY -- creates no week, no game, changes nothing.
#
# Run this before turning the Thursday publish timer back on, and any time a
# college team seems to have gone missing from the board.
#
# It spends a few Odds API credits.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== FBS filter check (read only) ==="
echo
if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" '
  cd /root/gridiron-pools
  export GRIDIRON_DATABASE_URI="$(python3 -c "
import os, re, subprocess
out = subprocess.run([\"systemctl\", \"show\", \"gridiron-server\", \"-p\", \"Environment\"],
                     capture_output=True, text=True).stdout
m = re.search(r\"GRIDIRON_DATABASE_URI=(\\S+)\", out)
path = (m.group(1) if m else \"sqlite:///instance/pools.db\").replace(\"sqlite:///\", \"\")
if not os.path.isabs(path):
    path = os.path.join(\"/root/gridiron-pools\", path)
print(\"sqlite:///\" + path)
")"
  venv/bin/python3 scripts/check_fbs_filter.py
' 2>&1

echo
read -n1 -s -p "Press any key to close this window..."
echo
