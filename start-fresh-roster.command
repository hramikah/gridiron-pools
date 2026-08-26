#!/bin/bash
# ---------------------------------------------------------------------------
# Empty the roster on instance/pools.db and create the first account -- yours.
#
# Everyone else arrives by invitation, so the live site starts with exactly one
# account, an admin, so there is someone to send those invitations from.
#
# Deletes every user, plus the activity log, password-reset tokens, message
# threads and unused invitations. Teams, weeks, the Loser Pool points and the
# settings are untouched -- they describe the season, not the people.
#
# It REFUSES if any entry or pick exists, so it can never be run by accident
# once members have joined and paid.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3

echo "=== Starting the live roster fresh ==="
echo
echo "This deletes EVERY account in instance/pools.db and creates one admin."
echo

read -p "Username for your account: " GP_USERNAME
read -p "E-mail address: " GP_EMAIL
read -p "How many teams may this address hold? [3] " GP_MAX_TEAMS
GP_MAX_TEAMS=${GP_MAX_TEAMS:-3}
read -s -p "Password (at least 8 characters, not shown): " GP_PASSWORD; echo
read -s -p "Type it again: " GP_PASSWORD2; echo
if [ "$GP_PASSWORD" != "$GP_PASSWORD2" ]; then
  echo; echo "Those don't match. Nothing was changed."
  read -n1 -s -p "Press any key..."; echo; exit 1
fi
export GP_USERNAME GP_EMAIL GP_PASSWORD GP_MAX_TEAMS

"$PY" scripts/fresh_roster.py instance/pools.db || {
  echo; read -n1 -s -p "Press any key..."; echo; exit 1; }
echo
read -n1 -s -p "Press RETURN to wipe and create, or close this window to cancel..." _; echo
echo
"$PY" scripts/fresh_roster.py instance/pools.db --apply || {
  echo; read -n1 -s -p "Press any key..."; echo; exit 1; }
unset GP_PASSWORD GP_PASSWORD2
echo
"$PY" scripts/check_live_db.py instance/pools.db
echo
read -n1 -s -p "Press any key to close this window..."
echo
