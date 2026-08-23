#!/bin/bash
# ---------------------------------------------------------------------------
# Pause the publishweek agent while the demo database is live.
#
# It keeps pulling real betting lines into instance/pools.db, which nobody can
# see while the demo is up, and each pull spends Odds API credits against a
# 500/month plan.
#
# Undo with resume-publishweek.command.
# ---------------------------------------------------------------------------
set -u
PLIST="$HOME/Library/LaunchAgents/com.davidhramika.gridironpools.publishweek.plist"

echo "=== Pausing the publishweek agent ==="
echo
if [ ! -f "$PLIST" ]; then
  echo "No plist at $PLIST - nothing to pause."
  read -n1 -s -p "Press any key to close..."; echo; exit 0
fi

launchctl unload "$PLIST" 2>/dev/null
sleep 1
if launchctl list | grep -q gridironpools.publishweek; then
  echo "  !! It is still listed. Tell Claude."
else
  echo "  ***  publishweek is stopped. No more Odds API calls.  ***"
fi
echo
read -n1 -s -p "Press any key to close this window..."
echo
