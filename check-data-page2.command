#!/bin/bash
# ---------------------------------------------------------------------------
# READ-ONLY for the live site. Copies the live database to /tmp on the droplet
# (SQLite backup API, original untouched), then loads the Data page against
# that COPY as several players and prints any traceback. No pull, no restart.
# Output also saved to _scratch/data-page-check2.txt
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null
mkdir -p _scratch
OUT="_scratch/data-page-check2.txt"

ssh -i "$KEY" $SSHOPTS "$HOST" '
  cd /root/gridiron-pools || exit 1
  echo "=== site log mentions of /data ==="
  journalctl -u gridiron-server --since "-3 hours" --no-pager | grep -n -i -A30 "exception on\|error\|/data" | tail -80
  echo
  PY=venv/bin/python3
  $PY - <<PYEOF
import os, sqlite3, sys, traceback, random
src = sqlite3.connect("file:/root/gridiron-pools/instance/pools.db?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/wrapped-check.db")
src.backup(dst); src.close(); dst.close()
os.environ["GRIDIRON_DATABASE_URI"] = "sqlite:////tmp/wrapped-check.db"
sys.path.insert(0, "/root/gridiron-pools")
import app as appmod
a = appmod.app
a.config["TESTING"] = True
a.config["PROPAGATE_EXCEPTIONS"] = True
a.config["WTF_CSRF_ENABLED"] = False
from models import User
with a.app_context():
    users = User.query.order_by(User.id).all()
    picks = [u.id for u in users if u.is_admin][:1] + [u.id for u in random.Random(1).sample(users, min(6, len(users)))]
for uid in picks:
    c = a.test_client()
    with c.session_transaction(base_url="https://localhost") as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    for method, path in (("GET", "/data"), ("POST", "/data"), ("GET", "/data"), ("GET", "/data/6"), ("GET", "/data/5"), ("GET", "/data/1")):
        try:
            if method == "POST":
                r = c.post(path, data={"passcode": "2222"}, base_url="https://localhost")
            else:
                r = c.get(path, base_url="https://localhost")
            body = r.get_data(as_text=True)
            print(uid, method, path, r.status_code, "cards=%d" % body.count("<section class=\"wr-card"), "locked" if "name=\"passcode\"" in body else "")
        except Exception:
            print(uid, method, path, "EXCEPTION"); traceback.print_exc()
PYEOF
  rm -f /tmp/wrapped-check.db
' 2>&1 | tee "$OUT"
echo
echo "Saved to $OUT"
read -n1 -s -p "Press any key to close this window..."
echo
