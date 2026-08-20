#!/bin/bash
# Gridiron Pools -- local test site.
#
# Double-click this file in Finder, or run ./start-testbed.command in Terminal.
# It sets up a Python environment the first time, seeds a throwaway database
# and starts the site at http://127.0.0.1:8090.
#
# It never touches the live site or the live database. The only database it
# will open is testbed/pools.db inside this folder.

set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"

# ---------------------------------------------------------------------------
# Everything the app writes goes under testbed/, never instance/. The scripts
# that wipe and reseed data check for the word "testbed" in this path and
# refuse to run without it -- see testbed_guard.py.
# ---------------------------------------------------------------------------
mkdir -p "$REPO/testbed"
export GRIDIRON_DATABASE_URI="sqlite:///$REPO/testbed/pools.db"
export SEASON_YEAR="${SEASON_YEAR:-2026}"

printf '\n\033[1mGridiron Pools -- test site\033[0m\n'
printf '  folder:   %s\n' "$REPO"
printf '  database: testbed/pools.db  (live site untouched)\n\n'

# --- Python ---------------------------------------------------------------
PY=""
for candidate in python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    echo "No Python found. Install it with:  brew install python"
    echo "(or from https://www.python.org/downloads/macos/)"
    read -r -p "Press return to close."
    exit 1
fi

if [ ! -d "$REPO/venv" ]; then
    echo "First run -- creating a Python environment (about a minute)..."
    "$PY" -m venv "$REPO/venv"
    "$REPO/venv/bin/pip" install --quiet --upgrade pip
    "$REPO/venv/bin/pip" install --quiet -r "$REPO/requirements.txt" pytest
    echo "Environment ready."
fi
VENV_PY="$REPO/venv/bin/python"

# --- database -------------------------------------------------------------
if [ ! -f "$REPO/testbed/pools.db" ]; then
    echo "Seeding a fresh database: 32 NFL teams, Loser Pool point values, one admin."
    "$VENV_PY" "$REPO/seed.py"
    echo
fi

# --- go -------------------------------------------------------------------
cat <<BANNER

  Open:      http://127.0.0.1:8090
  Log in:    admin  /  changeme123

  The red "local test site" banner across the top is how you know you are
  not on gridironinvestment.com.

  From there:  Admin -> Week Manager -> create weeks and add games,
               then Admin -> Pool Manager to enter scores.

  Stop the site with Ctrl-C in this window.
  Start over from nothing:  ./reset-testbed.command

BANNER

exec "$VENV_PY" "$REPO/app.py"
