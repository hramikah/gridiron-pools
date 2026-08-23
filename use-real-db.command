#!/bin/bash
# ---------------------------------------------------------------------------
# Point the LIVE server back at the real database, instance/pools.db.
#
# Undoes use-demo-db.command by removing the GRIDIRON_DATABASE_URI override
# from the launchd job, which puts app.py back on its built-in default. The
# demo file is left on disk untouched -- delete it by hand when you are sure
# you are done with it.
#
#   bash use-real-db.command      (or double-click, once it is chmod +x)
# ---------------------------------------------------------------------------
set -u

REPO="/Users/davidhramika/gridiron-pools"
PLIST="$HOME/Library/LaunchAgents/com.davidhramika.gridironpools.server.plist"
PB=/usr/libexec/PlistBuddy
PORT=8090

echo "=== Switching the live site back to the REAL database ==="
echo

if [ ! -f "$REPO/instance/pools.db" ]; then
  echo "WARNING - there is no file at $REPO/instance/pools.db."
  echo "The server will create an empty one on startup. Continue only if that"
  echo "is what you expect."
  echo
  read -n1 -s -p "Press any key to continue, or Ctrl-C to stop..."; echo
fi

BACKUP="$PLIST.bak-$(date +%Y%m%d-%H%M%S)"
cp "$PLIST" "$BACKUP" && echo "Saved a copy of the launchd job at:" && echo "    $BACKUP"

$PB -c "Delete :EnvironmentVariables:GRIDIRON_DATABASE_URI" "$PLIST" 2>/dev/null
# Leave an empty EnvironmentVariables dict behind rather than risk deleting a
# key something else added. An empty dict is harmless.

echo "Removed the database override."
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

ACTUAL=$(ps eww -p "$PID" 2>/dev/null | tr ' ' '\n' | grep '^GRIDIRON_DATABASE_URI=' | head -1)
echo "Server is up on port $PORT (pid $PID)."
if [ -z "$ACTUAL" ]; then
  echo
  echo "  ***  No override set - the live site is back on instance/pools.db.  ***"
else
  echo "  !! An override is still set: $ACTUAL"
  echo "     Tell Claude."
fi
echo "  https://gridironinvestment.com"
echo
read -n1 -s -p "Press any key to close this window..."
echo
