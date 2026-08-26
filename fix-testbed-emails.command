#!/bin/bash
# ---------------------------------------------------------------------------
# Put the sibling accounts in the LOCAL TEST database back together.
#
# Accounts are linked by sharing an e-mail address -- that is what makes the
# Billing page total every team you own and the "Hi" menu list them. When the
# testbed addresses were redirected for the SendGrid testing each account got
# its own alias, and four commissioner accounts were blanked, so the families
# came apart and a player with three teams saw three separate bills.
#
# This stops the test site, gives every account in a family the same alias
# (test mail still lands in the adventurecoastmarketing inbox), and starts it
# again. It shows you what it will change before it changes it.
#
# The live site, the droplet and instance/testbed-demo.db are NOT touched.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PORT=8090
PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3

echo "=== Re-linking the test database's sibling accounts ==="
echo
echo "This is what will change:"
echo
"$PY" scripts/relink_testbed_emails.py testbed/pools.db instance/testbed-demo.db || {
  echo "Could not read the database."; read -n1 -s -p "Press any key..."; echo; exit 1; }
if [ -f testbed/pools.db.new ]; then
  echo
  echo "testbed/pools.db.new (the copy a reset restores) will get the same treatment:"
  "$PY" scripts/relink_testbed_emails.py testbed/pools.db.new instance/testbed-demo.db | tail -2
fi
echo
read -n1 -s -p "Press RETURN to apply, or close this window to cancel..." key; echo

# The site must be stopped: writing to a SQLite file another process has open
# is what corrupted this database once already.
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

cp testbed/pools.db "testbed/pools.db.before-relink-$(date +%Y%m%d-%H%M%S)"
"$PY" scripts/relink_testbed_emails.py testbed/pools.db instance/testbed-demo.db --apply || {
  echo "FAILED -- the backup copy beside testbed/pools.db is the state before this ran."
  read -n1 -s -p "Press any key..."; echo; exit 1; }

# Same fix on the spare copy, so restoring it later does not bring the split
# families back.
if [ -f testbed/pools.db.new ]; then
  "$PY" scripts/relink_testbed_emails.py testbed/pools.db.new instance/testbed-demo.db --apply | tail -1
fi

echo
echo "Starting the test site..."
echo
exec ./start-testbed.command
