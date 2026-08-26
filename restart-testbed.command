#!/bin/bash
# ---------------------------------------------------------------------------
# Restart the LOCAL TEST SITE (not the live site).
#
# Template edits show up on a plain browser refresh. Python edits -- anything
# in blueprints/, scoring.py, app.py -- do not: the running process holds the
# old code until it is recycled. Double-click this file after any such change.
#
# It stops whatever python is listening on port 8090 and then hands over to
# start-testbed.command, which opens testbed/pools.db. The live site on the
# droplet is never touched.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PORT=8090

echo "=== Restarting the local test site ==="
echo

listener() { lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null; }

PIDS=$(listener)
if [ -z "$PIDS" ]; then
  echo "Nothing was running on port $PORT. Starting it fresh."
else
  for p in $PIDS; do
    CMD=$(ps -p "$p" -o comm= 2>/dev/null)
    case "$CMD" in
      *[Pp]ython*) echo "Stopping the test site (pid $p)"; kill "$p" ;;
      *) echo "!! pid $p on port $PORT is '$CMD', not python -- leaving it alone."
         echo "   Close that program yourself and run this again."
         read -n1 -s -p "Press any key to close..."; echo; exit 1 ;;
    esac
  done
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(listener)" ] && break; sleep 1; done
  for p in $(listener); do echo "Still up -- forcing (pid $p)"; kill -9 "$p"; done
  sleep 1
fi

echo "Starting with the current code..."
echo
exec ./start-testbed.command
