#!/bin/bash
# ---------------------------------------------------------------------------
# POINT THE PUBLIC SITE AT THE REAL DATABASE.
#
# gridironinvestment.com currently serves instance/testbed-demo.db -- the
# 149-player simulation with the frozen clock. This sends instance/pools.db
# from this Mac and switches the live service over to it.
#
# After this, what the members see is the real thing: real accounts, real
# weeks, no simulation banner. The demo file is left on the droplet untouched,
# so send-demo-to-droplet.command can switch back.
#
# Run check-live-db.command first and make sure it says Ready.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3

chmod 600 "$KEY" 2>/dev/null
[ -f "$KEY" ] || { echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key..."; echo; exit 1; }
[ -f instance/pools.db ] || { echo "No instance/pools.db on this Mac."
  read -n1 -s -p "Press any key..."; echo; exit 1; }

echo "=== Going live with the real database ==="
echo
"$PY" scripts/check_live_db.py instance/pools.db
echo
echo "This replaces what the public site serves. Members will see this data."
read -n1 -s -p "Press RETURN to go live, or close this window to cancel..." _; echo
echo

scp -i "$KEY" $SSHOPTS instance/pools.db "$HOST:/root/gridiron-pools/instance/pools.db.incoming" || {
  echo "COPY FAILED."; read -n1 -s -p "Press any key..."; echo; exit 1; }

ssh -i "$KEY" $SSHOPTS "$HOST" '
set -e
cd /root/gridiron-pools/instance
# Keep whatever was there before, named so it is obvious what it was.
[ -f pools.db ] && cp pools.db pools.db.replaced-$(date +%Y%m%d-%H%M%S)
mv pools.db.incoming pools.db
mkdir -p /etc/systemd/system/gridiron-server.service.d
cat > /etc/systemd/system/gridiron-server.service.d/override.conf <<CONF
[Service]
Environment=GRIDIRON_DATABASE_URI=sqlite:////root/gridiron-pools/instance/pools.db
CONF
systemctl daemon-reload && systemctl restart gridiron-server && sleep 6
echo "service: $(systemctl is-active gridiron-server)"
cd /root/gridiron-pools
venv/bin/python3 -c "import sqlite3;c=sqlite3.connect(\"instance/pools.db\");print(\"live database: users\",c.execute(\"select count(*) from user\").fetchone()[0],\"| entries\",c.execute(\"select count(*) from entry\").fetchone()[0],\"| games\",c.execute(\"select count(*) from game\").fetchone()[0])"
curl -s -o /dev/null -w "app: HTTP %{http_code}\n" http://127.0.0.1:8090/auth/login
if curl -s http://127.0.0.1:8090/auth/login | grep -qi "simulation"; then
  echo "!! the sign-in page still mentions a simulation -- it did not switch"
else
  echo "sign-in page is clean (no simulation banner)"
fi
'
echo
echo "DONE. Check https://gridironinvestment.com"
read -n1 -s -p "Press any key to close this window..."
echo
