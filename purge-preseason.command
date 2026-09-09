#!/bin/bash
# ---------------------------------------------------------------------------
# Remove the preseason weeks from the LIVE site, ready for the regular season.
#
# Deletes, for every preseason week in all three pools:
#   the Gridiron miss rows first (they have no cascade from Week, so deleting
#   a week without them leaves orphans that break the standings), then the
#   picks, then the games, then the weeks themselves.
#
# It takes a proper SQLite backup of the live database FIRST -- on the droplet
# and again on this Mac -- and it re-checks its assumptions before deleting.
# If anything has changed since the diagnosis it stops and changes nothing.
#
# Run diagnose-preseason.command first. Double-click this only when you have
# read that report and are happy with the "STANDINGS AFTER" it showed.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
STAMP="$(date +%Y%m%d-%H%M%S)"
chmod 600 "$KEY" 2>/dev/null

echo "=== Purge the preseason from gridironinvestment.com ==="
echo
echo "This DELETES live data: every preseason week, its games, its picks and"
echo "the Gridiron miss rows that hang off it. Standings go back to 0-0-0 and"
echo "Loser points go back to 0."
echo
echo "A backup is taken first, on the droplet and on this Mac."
echo

if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

read -p 'Type PURGE (all capitals) to go ahead, anything else to cancel: ' CONFIRM
if [ "$CONFIRM" != "PURGE" ]; then
  echo "Cancelled. Nothing was touched."
  read -n1 -s -p "Press any key to close..."; echo; exit 0
fi
echo

ssh -i "$KEY" $SSHOPTS "$HOST" "cat > /tmp/purge_preseason.py" <<'PYEOF'
"""Delete the preseason weeks from the live database. Backs up first."""
import os, re, shutil, sqlite3, subprocess, sys

REPO = "/root/gridiron-pools"
STAMP = os.environ["PURGE_STAMP"]
sys.path.insert(0, REPO)
os.chdir(REPO)

unit = subprocess.run(["systemctl", "show", "gridiron-server", "-p", "Environment"],
                      capture_output=True, text=True).stdout
live_uri = None
for line in (unit or "").splitlines():
    m = re.search(r"GRIDIRON_DATABASE_URI=(\S+)", line)
    if m:
        live_uri = m.group(1)
src = (live_uri or "").replace("sqlite:///", "") or os.path.join(REPO, "instance/pools.db")
if not os.path.isabs(src):
    src = os.path.join(REPO, src)

# ---- backup, via SQLite's own backup API ---------------------------------
# NOT a file copy: the site is running and holds the file open, and copying a
# live SQLite database is exactly how the August database got corrupted.
bdir = os.path.join(REPO, "instance", "old_backups")
os.makedirs(bdir, exist_ok=True)
BACKUP = os.path.join(bdir, "pools_pre_purge_%s.db" % STAMP)
s = sqlite3.connect(src); d = sqlite3.connect(BACKUP); s.backup(d); d.close(); s.close()
print("live database:", src)
print("BACKUP WRITTEN:", BACKUP, os.path.getsize(BACKUP), "bytes")
print()

os.environ["GRIDIRON_DATABASE_URI"] = "sqlite:///" + src
from app import create_app
import helpers, scoring
from models import Entry, Game, GridironMiss, Pick, Setting, User, Week, db

app = create_app()
with app.app_context():
    season = app.config["CURRENT_SEASON"]
    POOLS = ("gridiron", "dropdead", "loser")

    pre = [w for w in Week.query.filter_by(season_year=season).all()
           if w.is_preseason or w.number >= 101]
    pre_ids = [w.id for w in pre]

    # ---- preconditions. Refuse if the world moved since the diagnosis. ----
    print("=== PRE-FLIGHT CHECKS ===")
    stop = []
    if not pre_ids:
        stop.append("there are no preseason weeks left -- nothing to do")
    played = []
    for pool in POOLS:
        for w in Week.query.filter_by(season_year=season, pool=pool).all():
            if not (w.is_preseason or w.number >= 101) and Pick.query.filter_by(week_id=w.id).count():
                played.append("%s week %s" % (pool, w.number))
    if played:
        stop.append("a REGULAR week already has picks in it: %s -- refusing, this "
                    "is no longer a clean preseason purge" % ", ".join(played))
    stray = [m for m in GridironMiss.query.all() if m.week_id not in pre_ids]
    if stray:
        stop.append("%s GridironMiss rows point at a NON-preseason week -- refusing "
                    "rather than guessing which to keep" % len(stray))
    paid = [e for e in Entry.query.filter_by(season_year=season).all() if (e.buy_backs_paid or 0)]
    if paid:
        stop.append("%s entries have a buy-back recorded as PAID -- money has changed "
                    "hands, refusing until that is sorted" % len(paid))
    if stop:
        print("REFUSING TO PURGE:")
        for line in stop:
            print("  -", line)
        print("\nNothing was deleted. The backup above is still valid.")
        raise SystemExit(1)
    print("  no regular week has picks in it")
    print("  every GridironMiss row points at a preseason week")
    print("  no buy-back has been recorded as paid")
    print("  preseason weeks to remove:", ", ".join("%s %s" % (w.pool, w.label) for w in pre))
    print()

    # ---- the delete. Children before parents. ----------------------------
    print("=== DELETING ===")
    n_miss = GridironMiss.query.filter(GridironMiss.week_id.in_(pre_ids)).delete(synchronize_session=False)
    n_pick = Pick.query.filter(Pick.week_id.in_(pre_ids)).delete(synchronize_session=False)
    n_game = Game.query.filter(Game.week_id.in_(pre_ids)).delete(synchronize_session=False)
    n_week = Week.query.filter(Week.id.in_(pre_ids)).delete(synchronize_session=False)

    # Nothing should be carrying elimination or buy-back state, but if a
    # preseason week somehow set some, this is where it goes -- there are no
    # regular weeks played, so any of it can only have come from preseason.
    n_entry = 0
    for e in Entry.query.filter_by(season_year=season).all():
        if (not e.is_active) or e.eliminated_week or e.buyback_week or (e.buy_backs_used or 0):
            e.is_active = True
            e.eliminated_week = None
            e.buyback_week = None
            e.buy_backs_used = 0
            n_entry += 1

    row = db.session.get(Setting, "active_week")
    if row and str(row.value).strip().isdigit() and int(row.value) >= 101:
        row.value = ""
        print("  active_week was pinned to a preseason week -- cleared")

    db.session.commit()
    print("  %s miss rows, %s picks, %s games, %s weeks deleted" % (n_miss, n_pick, n_game, n_week))
    print("  %s entries reset to alive / 0 buy-backs" % n_entry)
    print()

    # ---- verify ----------------------------------------------------------
    print("=== AFTER ===")
    left = [w for w in Week.query.filter_by(season_year=season).all()
            if w.is_preseason or w.number >= 101]
    print("  preseason weeks remaining:", len(left))
    print("  orphan miss rows:", len([m for m in GridironMiss.query.all()
                                      if db.session.get(Week, m.week_id) is None]))
    print("  orphan picks:", len([p for p in Pick.query.all()
                                  if db.session.get(Week, p.week_id) is None]))
    print("  orphan games:", len([g for g in Game.query.all()
                                  if db.session.get(Week, g.week_id) is None]))
    print("  total picks left in the database:", Pick.query.count())
    print("  GridironMiss rows left:", GridironMiss.query.count())
    cur = helpers.get_current_week(season, "gridiron")
    print("  get_current_week(gridiron) is now:", (cur.label, cur.pick_deadline) if cur else None)
    print()
    print("  Gridiron standings, first 5 rows:")
    for rank, e, w, l, t in scoring.standings_gridiron(season)[:5]:
        u = db.session.get(User, e.user_id)
        print("     %-4s %-22s %s-%s-%s" % (rank, (u.username if u else "?")[:22], w, l, t))
    print("  Loser standings, first 5 rows:")
    for rank, e, total in scoring.standings_loser(season)[:5]:
        u = db.session.get(User, e.user_id)
        print("     %-4s %-22s %s" % (rank, (u.username if u else "?")[:22], total))
    print()
    print("BACKUP IS AT:", BACKUP)
PYEOF

echo "--- running on the droplet ---"
ssh -i "$KEY" $SSHOPTS "$HOST" "cd /root/gridiron-pools && PURGE_STAMP='$STAMP' venv/bin/python3 /tmp/purge_preseason.py 2>&1; rc=\$?; rm -f /tmp/purge_preseason.py; exit \$rc"
RC=$?
echo

if [ $RC -ne 0 ]; then
  echo "The purge did NOT run (see the reason above). Nothing was deleted."
  read -n1 -s -p "Press any key to close this window..."; echo; exit 1
fi

echo "--- restarting the site ---"
ssh -i "$KEY" $SSHOPTS "$HOST" '
  systemctl restart gridiron-server
  sleep 5
  systemctl is-active gridiron-server
  # -L: every one of these is behind a login, so a signed-out request is a
  # 302 to /auth/login and that is the healthy answer. Follow it and report
  # where it landed, so the number means something.
  for p in /auth/login / /standings /reports /scores; do
    curl -sL -o /dev/null -w "  $p -> HTTP %{http_code} (final: %{url_effective})\n" "http://127.0.0.1:8090$p"
  done
'
echo

echo "--- copying the backup down to this Mac ---"
mkdir -p instance
if scp -i "$KEY" $SSHOPTS \
      "$HOST:/root/gridiron-pools/instance/old_backups/pools_pre_purge_$STAMP.db" \
      "instance/pools_pre_purge_$STAMP.db"; then
  echo "  saved to instance/pools_pre_purge_$STAMP.db on this Mac"
else
  echo "  !! could not copy the backup down. It is still on the droplet at"
  echo "     /root/gridiron-pools/instance/old_backups/pools_pre_purge_$STAMP.db"
fi
echo
echo "DONE."
echo
echo "To put it all back, the backup is the whole database as it was 60 seconds"
echo "ago. On the droplet:"
echo "    systemctl stop gridiron-server"
echo "    cp instance/old_backups/pools_pre_purge_$STAMP.db instance/pools.db"
echo "    systemctl start gridiron-server"
echo
read -n1 -s -p "Press any key to close this window..."
echo
