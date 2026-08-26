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
PORT=8090

# The site has to be stopped first. Renaming the file out from under a running
# process does not disturb the handle it already holds: the site carries on
# serving the OLD database and the reset looks like it did nothing at all.
SITE_STOPPED=0
stop_site() {
    [ "$SITE_STOPPED" = "1" ] && return 0
    SITE_STOPPED=1
    listener() { lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null; }
    local pids; pids=$(listener)
    if [ -z "$pids" ]; then
        echo "The test site was not running."
        return 0
    fi
    for p in $pids; do
        local cmd; cmd=$(ps -p "$p" -o comm= 2>/dev/null)
        case "$cmd" in
            *[Pp]ython*) echo "Stopping the test site (pid $p)"; kill "$p" ;;
            *) echo "!! pid $p on port $PORT is '$cmd', not python -- stop it yourself and run this again."
               exit 1 ;;
        esac
    done
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(listener)" ] && break; sleep 1; done
    for p in $(listener); do echo "Forcing (pid $p)"; kill -9 "$p"; done
    sleep 1
}

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
    stop_site
    mv "$DB" "$DB.previous"
    echo "Moved the old database to testbed/pools.db.previous"
fi
stop_site

if [ ! -x "$REPO/venv/bin/python" ]; then
    echo "No Python environment yet -- run ./start-testbed.command first."
    exit 1
fi

"$REPO/venv/bin/python" "$REPO/seed.py"
echo
echo "Fresh database seeded. Log in as admin / changeme123"
echo
echo "Starting the test site..."
echo
exec "$REPO/start-testbed.command"
