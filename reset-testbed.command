#!/bin/bash
# Wipe the test database and seed a fresh one. Live site is never involved --
# the only file this deletes is testbed/pools.db inside this folder.

set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"

mkdir -p "$REPO/testbed"
export GRIDIRON_DATABASE_URI="sqlite:///$REPO/testbed/pools.db"
export SEASON_YEAR="${SEASON_YEAR:-2026}"

DB="$REPO/testbed/pools.db"

printf '\n\033[1mReset the test site\033[0m\n'
printf '  This deletes: %s\n' "$DB"
printf '  Nothing else is touched.\n\n'

if [ ! -f "$DB" ]; then
    echo "No test database yet -- nothing to delete."
else
    read -r -p "Delete it and start over? [y/N] " reply
    case "$reply" in
        [yY]*) ;;
        *) echo "Left alone."; exit 0 ;;
    esac
    # Keep the last one, in case it had something worth going back to.
    mv "$DB" "$DB.previous"
    echo "Moved the old database to testbed/pools.db.previous"
fi

if [ ! -x "$REPO/venv/bin/python" ]; then
    echo "No Python environment yet -- run ./start-testbed.command first."
    exit 1
fi

"$REPO/venv/bin/python" "$REPO/seed.py"
echo
echo "Fresh database seeded. Start the site with ./start-testbed.command"
echo "Log in as admin / changeme123"
