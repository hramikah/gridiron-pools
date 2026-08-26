#!/bin/bash
# ---------------------------------------------------------------------------
# Fix what check-live-db.command flags, on instance/pools.db:
#   * load the 32 Loser Pool point values (copied from the demo database,
#     matched by team name, checked against the printed sheet)
#   * unpin the current week, so each pool auto-detects again
#   * remove the leftover placeholder logins -- but never one that holds an
#     entry or a pick, and never the 'admin' account
#
# Shows you exactly what it will do and waits before writing. Backs the
# database up first. The live site and the droplet are not touched -- this
# only edits the file on this Mac, which you then send up.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3

echo "=== Preparing instance/pools.db for going live ==="
echo
"$PY" scripts/prepare_live_db.py instance/pools.db instance/testbed-demo.db || {
  echo; echo "Stopped -- nothing was changed."
  read -n1 -s -p "Press any key..."; echo; exit 1; }
echo
echo "The placeholder logins listed above will be DELETED (the 'admin' account is kept)."
read -n1 -s -p "Press RETURN to apply, or close this window to cancel..." _; echo
echo
"$PY" scripts/prepare_live_db.py instance/pools.db instance/testbed-demo.db --apply --delete-test-users
echo
echo "Re-checking..."
echo
"$PY" scripts/check_live_db.py instance/pools.db
echo
read -n1 -s -p "Press any key to close this window..."
echo
