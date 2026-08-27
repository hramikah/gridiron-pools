#!/bin/bash
# ---------------------------------------------------------------------------
# Put a full week of games on the LOCAL TEST SITE so the pick pages have
# something to pick -- 16 NFL games in every pool plus 8 college games in
# Gridiron, due this coming Saturday at noon, every kickoff after that so the
# whole slate stays selectable.
#
# It writes only to testbed/pools.db. The script it runs refuses outright if
# pointed anywhere else, so it cannot touch the live site or instance/pools.db.
#
# Safe to run again whenever you want a clean slate -- it clears the test
# site's existing weeks (and their picks) first.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
REPO="$(pwd)"

export GRIDIRON_DATABASE_URI="sqlite:///$REPO/testbed/pools.db"
export SEASON_YEAR="${SEASON_YEAR:-2026}"

PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3

echo "=== Seeding the test site with a full slate ==="
echo "database: testbed/pools.db   (live site untouched)"
echo
"$PY" scripts/seed_testbed_slate.py
RC=$?
echo
if [ $RC -eq 0 ]; then
  echo "DONE. Now double-click restart-testbed.command, then open"
  echo "  http://127.0.0.1:8090"
  echo "and go to any pool's Make Picks page."
else
  echo "SOMETHING FAILED (exit $RC). Copy the text above and show it to Claude."
fi
read -n1 -s -p "Press any key to close this window..."
echo
