#!/bin/bash
# ---------------------------------------------------------------------------
# Point the LIVE server at the demo database.
#
# Same site, same domain, same launchd job -- only the database file changes.
# instance/pools.db is not opened, moved or modified by this.
#
# The demo file is named "testbed-demo.db" on purpose: the frozen clock in
# helpers.now_eastern() and the seed guard in testbed_guard.py both key off
# the word "testbed" in the database path. Naming the file that way is what
# lets the demo run without switching either safety off.
#
#   bash use-demo-db.command      (or double-click, once it is chmod +x)
#
# Undo with use-real-db.command.
# ---------------------------------------------------------------------------
set -u

REPO="/Users/davidhramika/gridiron-pools"
PLIST="$HOME/Library/LaunchAgents/com.davidhramika.gridironpools.server.plist"
DB="$REPO/instance/testbed-demo.db"
URI="sqlite:///$DB"
PB=/usr/libexec/PlistBuddy
PORT=8090

echo "=== Switching the live site to the DEMO database ==="
echo

if [ ! -f "$DB" ]; then
  echo "FAILED - no demo database at:"
  echo "    $DB"
  echo
  echo "Seed it first:"
  echo "    cd $REPO"
  echo "    export GRIDIRON_DATABASE_URI=\"$URI\""
  echo "    venv/bin/python3 seed.py"
  echo "    venv/bin/python3 seed_live_demo_thursday.py"
  echo
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

# Keep a copy of the launchd job as it was, every time, so there is always a
# way back even if this script is the thing that is wrong.
BACKUP="$PLIST.bak-$(date +%Y%m%d-%H%M%S)"
cp "$PLIST" "$BACKUP" || { echo "Could not back up $PLIST"; read -n1 -s -p "Press any key..."; exit 1; }
echo "Saved a copy of the launchd job at:"
echo "    $BACKUP"

# Idempotent: create the dict if it is missing, drop any previous value, set
# the new one. PlistBuddy errors on Add-when-present, so those are silenced.
$PB -c "Add :EnvironmentVariables dict" "$PLIST" 2>/dev/null
$PB -c "Delete :EnvironmentVariables:GRIDIRON_DATABASE_URI" "$PLIST" 2>/dev/null
$PB -c "Add :EnvironmentVariables:GRIDIRON_DATABASE_URI string $URI" "$PLIST" || {
  echo "FAILED to write the plist."; read -n1 -s -p "Press any key..."; exit 1; }

echo "Set GRIDIRON_DATABASE_URI to:"
echo "    $URI"
echo
echo "Reloading the server job..."
launchctl unload "$PLIST" 2>/dev/null
sleep 2
launchctl load "$PLIST" || { echo "FAILED to load the job."; read -n1 -s -p "Press any key..."; exit 1; }

for _ in $(seq 1 20); do
  PID=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null | head -1)
  [ -n "${PID:-}" ] && break
  sleep 1
done

echo
if [ -z "${PID:-}" ]; then
  echo "FAILED - nothing is listening on $PORT."
  echo "Last lines of logs/server.log:"
  tail -20 "$REPO/logs/server.log"
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

# Read the database path out of the running process's own environment, which
# is the only answer that cannot be wrong.
ACTUAL=$(ps eww -p "$PID" 2>/dev/null | tr ' ' '\n' | grep '^GRIDIRON_DATABASE_URI=' | head -1)
echo "Server is up on port $PORT (pid $PID)."
echo "  running with: ${ACTUAL:-(could not read the process environment)}"
echo
case "$ACTUAL" in
  *testbed-demo.db) echo "  ***  The live site is now serving the DEMO database.  ***" ;;
  "")               echo "  Could not confirm from the process. Check the site and the log." ;;
  *)                echo "  !! It came up on a DIFFERENT database. Tell Claude." ;;
esac
echo "  https://gridironinvestment.com"
echo
read -n1 -s -p "Press any key to close this window..."
echo
