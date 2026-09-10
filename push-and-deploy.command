#!/bin/bash
# Push the committed work on main to GitHub, then pull it onto the droplet
# and restart the live site. Code only -- no database is copied or touched.
# Double-click to run.
set -u
cd "$(dirname "$0")" || exit 1
export GIT_PAGER=cat PAGER=cat

echo "=== Push + deploy gridironinvestment.com ==="
echo
echo "Local HEAD: $(git rev-parse --short HEAD)"
echo
echo "Pushing to GitHub..."
if ! git push origin main; then
  echo "PUSH FAILED. Nothing was deployed. Tell Claude."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
chmod 600 "$KEY" 2>/dev/null
echo
echo "Deploying to the droplet..."
ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes "$HOST" '
  set -e
  cd /root/gridiron-pools
  echo "Live now at: $(git rev-parse --short HEAD)"
  git pull --ff-only
  echo "Live after pull: $(git rev-parse --short HEAD)"
  systemctl restart gridiron-server
  sleep 5
  systemctl is-active gridiron-server
  curl -s -o /dev/null -w "site responded HTTP %{http_code}\n" http://127.0.0.1:8090/auth/login
'
RC=$?
echo
if [ $RC -eq 0 ]; then
  echo "DONE. Give Cloudflare a few seconds, then reload gridironinvestment.com."
else
  echo "DEPLOY FAILED (exit $RC). Copy the text above and show it to Claude."
fi
read -n1 -s -p "Press any key to close this window..."
echo
