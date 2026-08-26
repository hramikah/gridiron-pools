#!/bin/bash
# ---------------------------------------------------------------------------
# Stop the test site, swap in the rebuilt test database, and start it again.
#
# The old testbed/pools.db was corrupted (a file copy landed while the site
# still had it open, which SQLite does not tolerate). testbed/pools.db.new is
# a clean rebuild from instance/testbed-demo.db with the email redirects, the
# 'hunter' test account and the SendGrid settings already applied.
#
# The live site and the droplet are not involved.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PORT=8090

echo "=== Repairing the local test database ==="
echo

if [ ! -f testbed/pools.db.new ]; then
  echo "testbed/pools.db.new is missing -- nothing to swap in."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

listener() { lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null; }
PIDS=$(listener)
if [ -n "$PIDS" ]; then
  for p in $PIDS; do
    CMD=$(ps -p "$p" -o comm= 2>/dev/null)
    case "$CMD" in
      *[Pp]ython*) echo "Stopping the test site (pid $p)"; kill "$p" ;;
      *) echo "!! pid $p on port $PORT is '$CMD', not python -- stop it yourself and run this again."
         read -n1 -s -p "Press any key..."; echo; exit 1 ;;
    esac
  done
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(listener)" ] && break; sleep 1; done
  for p in $(listener); do echo "Forcing (pid $p)"; kill -9 "$p"; done
  sleep 1
else
  echo "The test site was not running."
fi

# Keep the corrupted copy rather than destroying it, in case anything in it
# is ever wanted.
cp testbed/pools.db testbed/pools.db.corrupt-$(date +%Y%m%d-%H%M%S) 2>/dev/null
cat testbed/pools.db.new > testbed/pools.db
echo "Clean database installed."
echo
echo "Starting the test site..."
echo
exec ./start-testbed.command
