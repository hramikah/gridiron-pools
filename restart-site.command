#!/bin/bash
# ---------------------------------------------------------------------------
# Restart the live Gridiron Pools site.
#
# Why this exists: Flask compiles each template once and caches it, because
# TEMPLATES_AUTO_RELOAD is unset and debug is off. A template edit on disk is
# invisible until the server process is recycled. Double-click this file in
# Finder to do that.
#
# It only touches the process LISTENING on port 8090. cloudflared is left
# alone -- the tunnel reconnects by itself once the origin is back.
# ---------------------------------------------------------------------------
set -u
REPO="/Users/davidhramika/gridiron-pools"
PORT=8090
cd "$REPO" || { echo "Cannot find $REPO"; read -n1 -s -p "Press any key..."; exit 1; }

echo "=== Restarting Gridiron Pools ==="
echo

listener() { lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null; }

PIDS=$(listener)
if [ -z "$PIDS" ]; then
  echo "Nothing is listening on $PORT — the site is already down. Starting it."
else
  for p in $PIDS; do
    CMD=$(ps -p "$p" -o comm= 2>/dev/null)
    case "$CMD" in
      *[Pp]ython*) echo "Stopping server (pid $p)"; kill "$p" ;;
      *) echo "!! pid $p on port $PORT is '$CMD', not python — leaving it alone."; echo "   Stop the site by hand and run this again."; read -n1 -s -p "Press any key..."; exit 1 ;;
    esac
  done
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(listener)" ] && break; sleep 1; done
  for p in $(listener); do echo "Still up — forcing (pid $p)"; kill -9 "$p"; done
  sleep 1
fi

# If a launchd job supervises the server it will re-bind on its own. Give it a
# few seconds before starting a second copy, which would just fail on the port.
echo "Waiting to see if it comes back on its own..."
for _ in 1 2 3 4 5 6; do [ -n "$(listener)" ] && break; sleep 1; done

if [ -n "$(listener)" ]; then
  echo "It restarted itself (supervised). Good."
else
  echo "Starting server..."
  mkdir -p logs
  nohup "$REPO/venv/bin/python" "$REPO/serve.py" >> "$REPO/logs/server.log" 2>&1 &
  disown
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do [ -n "$(listener)" ] && break; sleep 1; done
fi

echo
if [ -z "$(listener)" ]; then
  echo "FAILED — nothing is listening on $PORT."
  echo "Last lines of logs/server.log:"
  tail -15 logs/server.log
  read -n1 -s -p "Press any key to close..."
  exit 1
fi

echo "Server is up on port $PORT. Checking the login page..."
sleep 2
# Sanity check: fetch the sign-in page and look for the simulation banner,
# which base.html renders on every page while the server is on a frozen-clock
# testbed database. Finding it proves three things at once -- the server came
# back, it is serving the current templates, and it is on the demo database.
# (This used to grep for the August outage notice, which no longer exists.)
BODY=$(curl -s --max-time 10 "http://127.0.0.1:$PORT/auth/login")
if printf '%s' "$BODY" | grep -q "Site is frozen to simulate"; then
  echo
  echo "  ***  SUCCESS — new templates are live, on the DEMO database.  ***"
  echo "  Check https://gridironinvestment.com/auth/login"
elif printf '%s' "$BODY" | grep -q "Gridiron Pools"; then
  echo
  echo "  Server restarted and the site is up, but the simulation banner is"
  echo "  not on the page — so it is running on the REAL database"
  echo "  (instance/pools.db), not the demo. Run use-demo-db.command if the"
  echo "  demo is what you wanted."
else
  echo
  echo "  Server restarted, but the sign-in page did not look right."
  echo "  Tell Claude — the template may need another look."
fi
echo
read -n1 -s -p "Press any key to close this window..."
echo
