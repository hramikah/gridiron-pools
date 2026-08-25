#!/bin/bash
# ---------------------------------------------------------------------------
# Deploy the latest code from GitHub to the live site (the DigitalOcean
# droplet at 159.223.111.72). Double-click this file in Finder.
#
# This ONLY updates code. It does not touch any database. The live site
# serves instance/testbed-demo.db and that file is never copied or changed
# by this script.
#
# The Mac is no longer the server -- restart-site.command restarts a Mac
# process that no longer serves anything. This is the script that matters.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"

chmod 600 "$KEY" 2>/dev/null

echo "=== Deploying gridironinvestment.com ==="
echo

if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" '
  set -e
  cd /root/gridiron-pools
  echo "Live now at: $(git rev-parse --short HEAD)"
  git pull --ff-only
  echo "Live after pull: $(git rev-parse --short HEAD)"
  echo
  echo "Restarting the site..."
  systemctl restart gridiron-server
  sleep 5
  systemctl is-active gridiron-server
  curl -s -o /dev/null -w "site responded HTTP %{http_code}\n" http://127.0.0.1:8090/auth/login
'
RC=$?
echo
if [ $RC -eq 0 ]; then
  echo "DONE. Give Cloudflare a few seconds, then reload gridironinvestment.com."
else
  echo "SOMETHING FAILED (exit $RC). Copy the text above and show it to Claude."
fi
read -n1 -s -p "Press any key to close this window..."
echo
