#!/bin/bash
# ---------------------------------------------------------------------------
# READ-ONLY. Why is the live Data page erroring? Prints the site's recent
# error log, then rebuilds every week's Wrapped against the live database
# opened READ-ONLY (SQLite mode=ro) and prints any traceback. Changes nothing:
# no pull, no restart, no writes. Output also saved to _scratch/data-page-check.txt
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null
mkdir -p _scratch
OUT="_scratch/data-page-check.txt"

ssh -i "$KEY" $SSHOPTS "$HOST" '
  cd /root/gridiron-pools || exit 1
  echo "HEAD: $(git rev-parse --short HEAD)"
  echo "=== service env ==="
  systemctl show gridiron-server -p Environment -p ExecStart -p WorkingDirectory --value
  echo
  echo "=== last errors in the site log ==="
  journalctl -u gridiron-server --since "-90 min" --no-pager | grep -n -B2 -A40 "Traceback" | tail -150
  echo
  echo "=== rebuilding Wrapped read-only ==="
  ENVLINE=$(systemctl show gridiron-server -p Environment --value)
  for kv in $ENVLINE; do export "$kv"; done
  PY=venv/bin/python3; [ -x "$PY" ] || PY=python3
  $PY - <<PYEOF
import os, traceback
from flask import Flask
uri = os.environ.get("GRIDIRON_DATABASE_URI") or "sqlite:///" + os.path.abspath("instance/pools.db")
path = uri.replace("sqlite:///", "", 1)
print("database:", path)
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///file:" + path + "?mode=ro&uri=true"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["CURRENT_SEASON"] = int(os.environ.get("SEASON_YEAR", 2026))
from models import db, User
db.init_app(app)
import wrapped
with app.app_context():
    season = app.config["CURRENT_SEASON"]
    menu = wrapped.week_menu(season, include_preview=True)
    print("menu:", menu)
    for n, label, st in menu:
        try:
            league = wrapped.league_week(season, n, use_cache=False)
            print("OK league", label, st)
        except Exception:
            print("FAIL league", label); traceback.print_exc(); continue
        bad = 0
        for u in User.query.all():
            try:
                wrapped.your_week(season, league, u)
            except Exception:
                bad += 1
                if bad == 1:
                    print("FAIL your_week", label, "user", u.id); traceback.print_exc()
        print("your_week failures:", bad)
    db.session.rollback()
PYEOF
' 2>&1 | tee "$OUT"
echo
echo "Saved to $OUT"
read -n1 -s -p "Press any key to close this window..."
echo
