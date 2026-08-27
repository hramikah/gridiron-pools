#!/bin/bash
# ---------------------------------------------------------------------------
# Ship ONLY the admin Payments page change, and nothing else.
#
# Why this exists: the floating save bar on the pick pages is also sitting
# uncommitted in this folder (base.html, style.css and the three pick
# templates). commit-work.command would commit everything and put that bar on
# the live site before you have decided about it. This commits the single file
# templates/admin/payments.html, pushes it, and deploys.
#
# The pick-bar files stay uncommitted, exactly as they are now, for the
# testbed. Nothing is deleted and no database is touched.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
export GIT_PAGER=cat
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

FILE="templates/admin/payments.html"

echo "=== Shipping just $FILE ==="
echo
git --no-pager status --porcelain
echo
if git --no-pager diff --quiet -- "$FILE" && git --no-pager diff --cached --quiet -- "$FILE"; then
  echo "No changes in $FILE. Nothing to do."
  read -n1 -s -p "Press any key to close..."; echo; exit 0
fi

git add -- "$FILE" || { echo "git add failed"; read -n1 -s -p "Press any key..."; echo; exit 1; }
git --no-pager commit -m "Payments: Who Owes What badges update in place, no jump to top" -- "$FILE" \
  || { echo "git commit failed"; read -n1 -s -p "Press any key..."; echo; exit 1; }
echo
echo "Committed:"
git --no-pager log --oneline -1
echo
echo "Still uncommitted (the pick-bar work, left alone on purpose):"
git --no-pager status --porcelain
echo

git push || { echo "git push failed"; read -n1 -s -p "Press any key..."; echo; exit 1; }
echo

[ -f "$KEY" ] || { echo "Missing $KEY -- cannot reach the droplet."; read -n1 -s -p "Press any key..."; echo; exit 1; }
ssh -i "$KEY" $SSHOPTS "$HOST" '
  set -e
  cd /root/gridiron-pools
  echo "Live now at: $(git rev-parse --short HEAD)"
  git pull --ff-only
  echo "Live after pull: $(git rev-parse --short HEAD)"
  echo
  echo "Restarting the site..."
  systemctl restart gridiron-server
  sleep 5
  systemctl is-active gridiron-server
  curl -s -o /dev/null -w "site responded HTTP %{http_code}\n" http://127.0.0.1:8090/auth/login
'
RC=$?
echo
if [ $RC -eq 0 ]; then
  echo "DONE. Reload the Payments page with Ctrl+Shift+R, and tell the other"
  echo "admin to do the same -- an old tab still runs the old page."
else
  echo "SOMETHING FAILED (exit $RC). Copy the text above and show it to Claude."
fi
read -n1 -s -p "Press any key to close this window..."
echo
