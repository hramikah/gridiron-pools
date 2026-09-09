#!/bin/bash
# ---------------------------------------------------------------------------
# Publish this coming Thursday's week on the LIVE site now, instead of waiting
# for the droplet's Thursday-morning timer. NFL and college, all three pools.
#
# A week runs Thursday to Wednesday. The ordinary publisher run on a Wednesday
# would pick the week that is about to END -- deadline already gone, arriving
# locked. This aims at the coming Thursday, so you get the week people are
# about to play, with its deadline this Saturday at noon.
#
# It only ADDS games. A game already published keeps its line: a second run
# never moves a spread or total that someone may already have picked against.
# It takes a backup of the live database first anyway.
#
# It pulls from The Odds API and spends a few credits.
#
# Shows you exactly what it is aiming at, then waits before doing anything.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
STAMP="$(date +%Y%m%d-%H%M%S)"
chmod 600 "$KEY" 2>/dev/null

echo "=== Publish this week's lines on the live site ==="
echo

if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

# The URI systemd runs the site with, so a manual run cannot open the wrong file.
# systemctl prints one line, "Environment=GRIDIRON_DATABASE_URI=... OTHER=...",
# so the value has to be pulled out of the middle of it -- and made ABSOLUTE.
# A relative sqlite:/// path is resolved by Flask-SQLAlchemy against the app's
# instance folder, not the working directory, which lands on
# instance/instance/pools.db and fails with "unable to open database file".
REMOTE_PRELUDE='
  cd /root/gridiron-pools
  export GRIDIRON_DATABASE_URI="$(python3 -c "
import os, re, subprocess
out = subprocess.run([\"systemctl\", \"show\", \"gridiron-server\", \"-p\", \"Environment\"],
                     capture_output=True, text=True).stdout
m = re.search(r\"GRIDIRON_DATABASE_URI=(\\S+)\", out)
path = (m.group(1) if m else \"sqlite:///instance/pools.db\").replace(\"sqlite:///\", \"\")
if not os.path.isabs(path):
    path = os.path.join(\"/root/gridiron-pools\", path)
print(\"sqlite:///\" + path)
")"
'

echo "--- what it would do (nothing is written) ---"
DRY=$(ssh -i "$KEY" $SSHOPTS "$HOST" "$REMOTE_PRELUDE"'
  echo "database: $GRIDIRON_DATABASE_URI"
  venv/bin/python3 scripts/publish_this_weekend.py
' 2>&1) || { echo "$DRY"; echo; echo "The dry run failed -- nothing was written."; \
       read -n1 -s -p "Press any key to close..."; echo; exit 1; }
echo "$DRY"
echo

# The preseason weeks were deleted on 2026-09-08. If the season start date in
# Admin > Settings is wrong, week_window() hands back a PRESEASON week and this
# would quietly recreate one. Refuse rather than let that happen.
if printf '%s' "$DRY" | grep -q "Preseason Week"; then
  echo "REFUSING: that is a PRESEASON week, not a regular one."
  echo "The season start date in Admin > Settings is probably wrong -- the"
  echo "preseason weeks were deleted and this would recreate one."
  echo "Nothing was written. Send the text above to Claude."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

echo "If that week and deadline are not what you expect, close this window now."
read -n1 -s -p "Press RETURN to back up the database and pull the lines, or close to cancel..." _
echo
echo

echo "--- backing up first ---"
ssh -i "$KEY" $SSHOPTS "$HOST" "$REMOTE_PRELUDE"'
  python3 - "$GRIDIRON_DATABASE_URI" '"$STAMP"' <<PY
import os, sqlite3, sys
src = sys.argv[1].replace("sqlite:///", "")
if not os.path.isabs(src):
    src = os.path.join("/root/gridiron-pools", src)
bdir = "/root/gridiron-pools/instance/old_backups"
os.makedirs(bdir, exist_ok=True)
dst = os.path.join(bdir, "pools_pre_publish_%s.db" % sys.argv[2])
s = sqlite3.connect(src); d = sqlite3.connect(dst); s.backup(d); d.close(); s.close()
print("  backup:", dst, os.path.getsize(dst), "bytes")
PY
'
echo

echo "--- pulling the lines ---"
ssh -i "$KEY" $SSHOPTS "$HOST" "$REMOTE_PRELUDE"'
  venv/bin/python3 scripts/publish_this_weekend.py --apply
'
echo

echo "--- what is now in that week ---"
ssh -i "$KEY" $SSHOPTS "$HOST" "$REMOTE_PRELUDE"'
  venv/bin/python3 - <<PY
import sys
sys.path.insert(0, "/root/gridiron-pools")
from app import app
from models import Game, Week
import helpers
with app.app_context():
    season = app.config["CURRENT_SEASON"]
    weeks = sorted(Week.query.filter_by(season_year=season).all(), key=helpers.week_sort_key)
    weeks = [w for w in weeks if Game.query.filter_by(week_id=w.id).count()]
    for w in weeks:
        games = Game.query.filter_by(week_id=w.id).all()
        nfl = [g for g in games if g.sport == "nfl"]
        coll = [g for g in games if g.sport == "college"]
        mnf = [g for g in games if g.is_mnf]
        print("  %-9s %-18s %s NFL + %s college   deadline %s   MNF flagged: %s"
              % (w.pool, w.label, len(nfl), len(coll), w.pick_deadline,
                 (mnf[0].away_team + " @ " + mnf[0].home_team) if mnf else "none"))
    gw = next((w for w in weeks if w.pool == "gridiron"), None)
    if gw:
        print()
        print("  Gridiron slate, earliest kickoff first:")
        for g in sorted(Game.query.filter_by(week_id=gw.id).all(),
                        key=lambda g: (g.kickoff is None, g.kickoff)):
            line = "PK" if not g.favorite else ("%s -%s" % (g.home_team if g.favorite == "home" else g.away_team, g.spread))
            print("    %-6s %-19s %-28s @ %-28s  %-24s O/U %s"
                  % (g.sport, g.kickoff, g.away_team, g.home_team, line, g.over_under))
PY
'
echo
echo "DONE. Reload gridironinvestment.com and the games should be there."
echo "Nothing was restarted -- the site reads the database live."
echo
read -n1 -s -p "Press any key to close this window..."
echo
