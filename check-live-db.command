#!/bin/bash
# ---------------------------------------------------------------------------
# Is the real database ready to be the live site? Reads only -- changes
# nothing. Run it, fix what it flags, run it again.
#
# It looks at instance/pools.db, the file that becomes the live site.
# The demo database and the local test site are not involved.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3
"$PY" scripts/check_live_db.py instance/pools.db
echo
read -n1 -s -p "Press any key to close this window..."
echo
