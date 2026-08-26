#!/bin/bash
# ---------------------------------------------------------------------------
# Publish this coming Thursday's week now, instead of waiting for the
# Thursday-morning job.
#
# A week runs Thursday to Wednesday. Run the ordinary publisher on a
# Wednesday and it picks the week that is about to END -- deadline already
# gone, arriving locked. This aims at the coming Thursday, so you get the week
# people are about to play, with its deadline this Saturday at noon.
#
# It writes into instance/pools.db -- the database that becomes the live site.
# Do this BEFORE go-live-droplet.command and the games travel up with it.
# Once the site is live, the droplet's own Thursday timer takes over.
#
# It pulls from The Odds API and spends a few credits. Games already published
# keep their lines; a second run only adds what is new.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3
export GRIDIRON_DATABASE_URI="sqlite:///$(pwd)/instance/pools.db"

echo "=== Publishing this weekend's games ==="
echo "into $(pwd)/instance/pools.db"
echo
"$PY" scripts/publish_this_weekend.py || {
  echo; read -n1 -s -p "Press any key..."; echo; exit 1; }
echo
read -n1 -s -p "Press RETURN to pull the lines and create it, or close this window to cancel..." _; echo
echo
"$PY" scripts/publish_this_weekend.py --apply
echo
read -n1 -s -p "Press any key to close this window..."
echo
