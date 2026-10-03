#!/bin/bash
# ---------------------------------------------------------------------------
# What the LIVE Thursday publish job actually did. READ ONLY.
#
# Shows the commit the droplet is serving, the last runs from
# logs/publish_week.log, and the timer's next fire time.
#
# The line to look for is "College: skipped N non-FBS game(s)". If it is
# there, the FBS filter was live for that run. If it is missing, that run
# used the old publisher and the college board has FCS games on it.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== Live publish job: what it did ==="
echo
if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" '
  cd /root/gridiron-pools
  echo "--- commit the droplet is serving ---"
  git --no-pager log --oneline -1
  echo
  echo "--- does the live publisher have the FBS filter? ---"
  if [ -f fbs.py ] && grep -q "is_fbs" publisher.py; then
    echo "  YES -- fbs.py present and publisher.py uses it"
  else
    echo "  NO  -- this droplet is still running the unfiltered publisher"
  fi
  echo
  echo "--- last 25 lines of logs/publish_week.log ---"
  tail -25 logs/publish_week.log 2>/dev/null || echo "  (no log yet)"
  echo
  echo "--- most recent service run ---"
  systemctl status gridiron-publish-week.service --no-pager -n 15 2>&1 | tail -20
  echo
  echo "--- timer ---"
  systemctl list-timers gridiron-publish-week.timer --no-pager 2>/dev/null | sed -n "1,2p"
' 2>&1

echo
read -n1 -s -p "Press any key to close this window..."
echo
