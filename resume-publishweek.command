#!/bin/bash
# ---------------------------------------------------------------------------
# Turn the publishweek agent back on. Run this when the demo is over and the
# live site is back on instance/pools.db.
# ---------------------------------------------------------------------------
set -u
PLIST="$HOME/Library/LaunchAgents/com.davidhramika.gridironpools.publishweek.plist"

echo "=== Resuming the publishweek agent ==="
echo
if [ ! -f "$PLIST" ]; then
  echo "No plist at $PLIST - nothing to resume."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

launchctl load "$PLIST" 2>/dev/null
sleep 1
if launchctl list | grep -q gridironpools.publishweek; then
  echo "  ***  publishweek is running again.  ***"
else
  echo "  !! It did not come back. Tell Claude."
fi
echo
read -n1 -s -p "Press any key to close this window..."
echo
