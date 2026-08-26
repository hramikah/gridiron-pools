#!/bin/bash
# ---------------------------------------------------------------------------
# Carry the real member accounts into instance/pools.db, ready for going live.
#
# The demo database holds the members who had signed up before the August data
# loss -- Crusher, GentlemanJack, Pigfoot, TOPHAT, Lord Abbott and the rest,
# with their real addresses -- alongside 135 invented @mockpool.test players.
# Only the real ones are copied, and only the accounts: none of their simulated
# entries, picks or eliminations come with them.
#
# Every imported account gets a NEW RANDOM password. In the demo they all share
# one -- the word "Password" -- and putting that on a public site would be
# handing out the keys. Members sign in the first time through
# "Forgot password", or an admin resets them from the Players page.
#
# Run prepare-live-db.command first. Safe to run twice.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3

echo "=== Importing the real member accounts ==="
echo
"$PY" scripts/import_real_users.py instance/pools.db instance/testbed-demo.db || {
  echo; echo "Stopped -- nothing was changed."
  read -n1 -s -p "Press any key..."; echo; exit 1; }
echo
read -n1 -s -p "Press RETURN to import, or close this window to cancel..." _; echo
echo
"$PY" scripts/import_real_users.py instance/pools.db instance/testbed-demo.db --apply
echo
"$PY" scripts/check_live_db.py instance/pools.db
echo
read -n1 -s -p "Press any key to close this window..."
echo
