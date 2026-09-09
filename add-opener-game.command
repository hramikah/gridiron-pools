#!/bin/bash
# ---------------------------------------------------------------------------
# Add the Wednesday season opener to Week 1 on the LIVE site.
#
# A week runs Thursday to Wednesday, so 2026's Wednesday-night opener falls
# the day BEFORE Week 1's window and the publisher never sees it. This pulls
# that game's real line from The Odds API and adds it to Week 1 in all three
# pools, spelled exactly the way the feed spells it -- so it dedupes against
# the Thursday job and scores itself like every other game.
#
# Dry run first. Nothing is written until you press RETURN, and it backs up
# the live database before it does.
#
# Games already in Week 1 are left alone; this only adds what is missing.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
STAMP="$(date +%Y%m%d-%H%M%S)"
chmod 600 "$KEY" 2>/dev/null

echo "=== Add the Wednesday opener to Week 1 ==="
echo

if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

REMOTE_PRELUDE='
  cd /root/gridiron-pools
  export GRIDIRON_DATABASE_URI="$(python3 -c "
import os, re, subprocess
out = subprocess.run([\"systemctl\", \"show\", \"gridiron-server\", \"-p\", \"Environment\"],
                     capture_output=True, text=True).stdout
m = re.search(r\"GRIDIRON_DATABASE_URI=(\\\\S+)\", out)
path = (m.group(1) if m else \"sqlite:///instance/pools.db\").replace(\"sqlite:///\", \"\")
if not os.path.isabs(path):
    path = os.path.join(\"/root/gridiron-pools\", path)
print(\"sqlite:///\" + path)
")"
'

ssh -i "$KEY" $SSHOPTS "$HOST" "cat > /tmp/add_opener.py" <<'PYEOF'
"""Add any NFL game that kicks off just BEFORE Week 1's window to Week 1.

The publisher imports games whose kickoff falls inside the week's Thursday
00:00 - Wednesday 23:59 window. A Wednesday-night season opener sits one day
in front of that, so it is never imported. This looks in the three days before
the window opens, and adds what it finds.

Names come straight from the feed, so they match publisher.py's dedupe and
score_fetcher.py's result lookup exactly. Team ids are filled in too --
Drop Dead and Loser pick by team id, and a game without them offers no teams.
"""
import os, sys
from datetime import timedelta

sys.path.insert(0, "/root/gridiron-pools")
os.chdir("/root/gridiron-pools")

APPLY = "--apply" in sys.argv

from app import app
from helpers import get_setting
from models import Game, Week, db
import publisher

with app.app_context():
    season = app.config["CURRENT_SEASON"]
    api_key = get_setting("odds_api_key")
    season_start_str = get_setting("season_start_thursday")
    if not api_key or not season_start_str:
        print("Missing the Odds API key or the season start date (Admin > Settings).")
        raise SystemExit(1)

    from datetime import datetime
    season_start = datetime.fromisoformat(season_start_str).date()
    number, window_start, window_end, deadline, is_preseason = publisher.week_window(
        season_start, reference=datetime.combine(season_start, datetime.min.time())
    )
    if is_preseason:
        print("That resolves to a preseason week -- refusing.")
        raise SystemExit(1)

    look_from = window_start - timedelta(days=3)
    print("Week %s window starts %s" % (number, window_start))
    print("Looking for games kicking off between %s and %s\n" % (look_from, window_start))

    pool_weeks = {}
    for pool in ("gridiron", "dropdead", "loser"):
        w = Week.query.filter_by(season_year=season, number=number, pool=pool).first()
        if not w:
            print("No Week %s row for %s -- refusing." % (number, pool))
            raise SystemExit(1)
        pool_weeks[pool] = w

    team_lookup = publisher._team_lookup()
    events = publisher.fetch_odds("nfl", api_key, preseason=False)
    found = []
    for event in events:
        kickoff = publisher._parse_commence(event["commence_time"])
        if not (look_from <= kickoff < window_start):
            continue
        home_name, away_name = event["home_team"], event["away_team"]
        spread_by_team, total = publisher._extract_lines(event)
        favorite, spread_value = None, None
        if home_name in spread_by_team:
            hp = spread_by_team[home_name]
            if hp < 0:
                favorite, spread_value = "home", abs(hp)
            elif hp > 0:
                favorite, spread_value = "away", abs(hp)
            else:
                favorite, spread_value = None, 0
        found.append((away_name, home_name, kickoff, favorite, spread_value, total))

    if not found:
        print("No game found in that window. Nothing to add.")
        raise SystemExit(0)

    for away_name, home_name, kickoff, favorite, spread_value, total in found:
        line = "PK" if not favorite else "%s -%s" % (
            home_name if favorite == "home" else away_name, spread_value)
        already = Game.query.filter_by(
            week_id=pool_weeks["gridiron"].id, home_team=home_name, away_team=away_name).first()
        print("  %-26s @ %-26s  %s  %-26s O/U %s%s" %
              (away_name, home_name, kickoff, line, total,
               "   [ALREADY IN WEEK 1]" if already else ""))
        for side, name in (("away", away_name), ("home", home_name)):
            if team_lookup.get(name) is None:
                print("     !! no Team row matches %r -- Drop Dead and Loser could not offer it" % name)

    if not APPLY:
        print("\nDry run -- nothing written.")
        raise SystemExit(0)

    added = 0
    for away_name, home_name, kickoff, favorite, spread_value, total in found:
        home_obj = team_lookup.get(home_name)
        away_obj = team_lookup.get(away_name)
        for pool, w in pool_weeks.items():
            if Game.query.filter_by(week_id=w.id, home_team=home_name, away_team=away_name).first():
                continue
            db.session.add(Game(
                week_id=w.id, pool=pool, sport="nfl",
                home_team=home_name, away_team=away_name,
                home_team_id=home_obj.id if home_obj else None,
                away_team_id=away_obj.id if away_obj else None,
                favorite=favorite if pool == "gridiron" else None,
                spread=spread_value if pool == "gridiron" else None,
                over_under=total if pool == "gridiron" else None,
                kickoff=kickoff,
            ))
            added += 1
    db.session.commit()
    print("\n  %s game rows added across the three pools." % added)

    print("\n  Week 1 NFL counts now:")
    for pool, w in pool_weeks.items():
        n = Game.query.filter_by(week_id=w.id, sport="nfl").count()
        print("    %-10s %s NFL games" % (pool, n))
PYEOF

echo "--- what it would add (nothing is written) ---"
ssh -i "$KEY" $SSHOPTS "$HOST" "$REMOTE_PRELUDE"'
  venv/bin/python3 /tmp/add_opener.py
' || { echo; echo "Dry run failed -- nothing written."; \
       read -n1 -s -p "Press any key to close..."; echo; exit 1; }

echo
read -n1 -s -p "Press RETURN to back up and add it, or close this window to cancel..." _
echo; echo

ssh -i "$KEY" $SSHOPTS "$HOST" "$REMOTE_PRELUDE"'
  python3 - "$GRIDIRON_DATABASE_URI" '"$STAMP"' <<PY
import os, sqlite3, sys
src = sys.argv[1].replace("sqlite:///", "")
bdir = "/root/gridiron-pools/instance/old_backups"
os.makedirs(bdir, exist_ok=True)
dst = os.path.join(bdir, "pools_pre_opener_%s.db" % sys.argv[2])
s = sqlite3.connect(src); d = sqlite3.connect(dst); s.backup(d); d.close(); s.close()
print("  backup:", dst, os.path.getsize(dst), "bytes")
PY
  venv/bin/python3 /tmp/add_opener.py --apply
  rm -f /tmp/add_opener.py
'
echo
echo "DONE. Reload the site -- the game should be on the Week 1 board."
read -n1 -s -p "Press any key to close this window..."
echo
