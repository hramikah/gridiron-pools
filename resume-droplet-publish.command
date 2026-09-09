#!/bin/bash
# ---------------------------------------------------------------------------
# Turn the LIVE site's Thursday publish job back on, after
# pause-droplet-publish.command.
#
# Run this once the week you were protecting has passed. Until you do, no week
# publishes itself.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== Resuming the live Thursday publish job ==="
echo
if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" '
  systemctl enable --now gridiron-publish-week.timer 2>&1 | sed "s/^/  /"
  echo
  systemctl is-enabled gridiron-publish-week.timer 2>&1 | sed "s/^/  enabled: /"
  systemctl is-active  gridiron-publish-week.timer 2>&1 | sed "s/^/  active:  /"
  systemctl list-timers gridiron-publish-week.timer --no-pager 2>/dev/null | sed -n "2p" | sed "s/^/  next:    /"
'
echo
read -n1 -s -p "Press any key to close this window..."
echo
