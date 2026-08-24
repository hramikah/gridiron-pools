#!/bin/bash
# Send the DEMO database (149 users, mid-week-3 simulation) to the droplet
# and point the live site at it -- the same thing use-demo-db.command did
# on the Mac, done on the droplet instead.
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
DB="instance/testbed-demo.db"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"

chmod 600 "$KEY" 2>/dev/null
[ -f "$DB" ] || { echo "No $DB on this Mac."; read -n1 -s -p "Press any key..."; exit 1; }

echo "=== Sending the demo database to the droplet ==="
ls -l "$DB"; echo

scp -i "$KEY" $SSHOPTS "$DB" "$HOST:/root/gridiron-pools/instance/testbed-demo.db" || {
  echo "COPY FAILED."; read -n1 -s -p "Press any key..."; echo; exit 1; }

echo "Copied. Pointing the live service at it..."
ssh -i "$KEY" $SSHOPTS "$HOST" '
mkdir -p /etc/systemd/system/gridiron-server.service.d
cat > /etc/systemd/system/gridiron-server.service.d/override.conf <<CONF
[Service]
Environment=GRIDIRON_DATABASE_URI=sqlite:////root/gridiron-pools/instance/testbed-demo.db
CONF
systemctl daemon-reload && systemctl restart gridiron-server && sleep 6
echo "service: $(systemctl is-active gridiron-server)"
cd /root/gridiron-pools && venv/bin/python3 -c "import sqlite3;c=sqlite3.connect(\"instance/testbed-demo.db\");print(\"users:\",c.execute(\"select count(*) from user\").fetchone()[0],\" picks:\",c.execute(\"select count(*) from pick\").fetchone()[0])"
curl -s -o /dev/null -w "app: HTTP %{http_code}\n" http://127.0.0.1:8090/auth/login
'
echo
echo "DONE. Check https://gridironinvestment.com"
read -n1 -s -p "Press any key to close this window..."
echo
