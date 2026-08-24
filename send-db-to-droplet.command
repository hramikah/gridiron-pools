#!/bin/bash
# Copy this Mac's live database up to the DigitalOcean droplet and restart it.
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"

chmod 600 "$KEY" 2>/dev/null

echo "=== Sending pools.db to the droplet ==="
ls -l instance/pools.db || { echo "No database found."; read -n1 -s -p "Press any key..."; exit 1; }
echo

scp -i "$KEY" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes \
    instance/pools.db "$HOST:/root/gridiron-pools/instance/pools.db.incoming" || {
  echo "COPY FAILED - the droplet did not accept the key yet."
  read -n1 -s -p "Press any key..."; echo; exit 1; }

echo "Copied. Swapping it in and restarting the droplet..."
$SSH "$HOST" 'cd /root/gridiron-pools/instance && cp pools.db pools.db.replaced-$(date +%Y%m%d-%H%M%S) 2>/dev/null; mv pools.db.incoming pools.db && systemctl restart gridiron-server && sleep 5 && systemctl is-active gridiron-server && curl -s -o /dev/null -w "droplet HTTP %{http_code}\n" http://127.0.0.1:8090/auth/login'

echo
echo "DONE."
read -n1 -s -p "Press any key to close this window..."
echo
