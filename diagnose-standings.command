#!/bin/bash
# ---------------------------------------------------------------------------
# Why did the Gridiron standings disagree with the pick page?  READ-ONLY.
#
# Copies the live database to a throwaway file on the droplet and asks the
# site's own code to build the standings, then compares that against a raw
# count of every pick result. Nothing real is written or deleted.
#
# Double-click, wait, then copy everything in this window to Claude.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== Gridiron standings diagnosis (read-only) ==="
echo

if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" 'cat > /tmp/diag_standings.py' <<'PYEOF'
"""Read-only: why the Gridiron standings disagreed with the pick page.

Works on a throwaway COPY of the live database. Nothing real is touched.
"""
import os, re, sqlite3, sys, subprocess

REPO = "/root/gridiron-pools"
sys.path.insert(0, REPO)
os.chdir(REPO)

print("=== WHAT CODE IS LIVE ===")
try:
    print(subprocess.run(["git", "log", "-1", "--pretty=%h %ad %s", "--date=short"],
                         capture_output=True, text=True, cwd=REPO).stdout.strip())
    print("dirty files:", len(subprocess.run(["git", "status", "--porcelain"],
                         capture_output=True, text=True, cwd=REPO).stdout.split("\n")) - 1)
except Exception as e:
    print("git unavailable:", e)
try:
    src_txt = open(os.path.join(REPO, "scoring.py")).read()
    print("scoring.py has the 2026-08-29 preseason-counts fix:",
          "commissioner's call, 2026-08-29" in src_txt)
    print("standings route calls process_due_weeks:",
          "process_due_weeks" in open(os.path.join(REPO, "blueprints/main.py")).read())
except Exception as e:
    print("could not read source:", e)
try:
    unit = subprocess.run(["systemctl", "show", "gridiron-server", "-p", "Environment",
                           "-p", "ActiveEnterTimestamp"], capture_output=True, text=True).stdout
    print(unit.strip())
except Exception as e:
    print("could not read unit:", e)
live_uri = None
for line in (unit or "").splitlines():
    m = re.search(r"GRIDIRON_DATABASE_URI=(\S+)", line)
    if m:
        live_uri = m.group(1)
print()

src = (live_uri or "").replace("sqlite:///", "") or os.path.join(REPO, "instance/pools.db")
if not os.path.isabs(src):
    src = os.path.join(REPO, src)
COPY = "/tmp/gpstand.db"
if os.path.exists(COPY):
    os.remove(COPY)
s = sqlite3.connect(src); d = sqlite3.connect(COPY); s.backup(d); d.close(); s.close()
print("Diagnosing a copy of:", src)
print()
os.environ["GRIDIRON_DATABASE_URI"] = "sqlite:///" + COPY

from app import create_app
import helpers, scoring
from models import Entry, Game, GridironMiss, Pick, User, Week, db

app = create_app()
with app.app_context():
    season = app.config["CURRENT_SEASON"]
    print("=== CLOCK ===")
    print("app now (ET):", helpers.now_eastern())
    print("active_week setting:", helpers.get_setting("active_week"))
    cur = helpers.get_current_week(season, "gridiron")
    print("get_current_week(gridiron):", (cur.number, cur.label, cur.pick_deadline) if cur else None)
    print()

    print("=== GRIDIRON WEEKS ===")
    print("%-5s %-22s %-10s %-20s %-9s %-9s %s" %
          ("num", "label", "preseason", "deadline", "passed", "missed_pr", "games final/total"))
    weeks = sorted(Week.query.filter_by(season_year=season, pool="gridiron").all(),
                   key=lambda w: w.number)
    for w in weeks:
        g = Game.query.filter_by(week_id=w.id, pool="gridiron").all()
        print("%-5s %-22s %-10s %-20s %-9s %-9s %s/%s" %
              (w.number, w.label, w.is_preseason, w.pick_deadline,
               helpers.deadline_passed(w), w.missed_processed,
               sum(1 for x in g if x.is_final), len(g)))
    print()

    print("=== PICK RESULTS BY WEEK (all gridiron picks) ===")
    for w in weeks:
        ps = Pick.query.filter_by(week_id=w.id, pool="gridiron").all()
        if not ps:
            continue
        from collections import Counter
        c = Counter((p.result or "NULL") for p in ps)
        print("week %-5s %s  ->  %s" % (w.number, w.label, dict(c)))
    print()

    print("=== STANDINGS AS THE SITE BUILDS THEM, vs a raw count ===")
    rows = scoring.standings_gridiron(season)
    print("%-5s %-22s %-10s %-14s %-10s %-8s %s" %
          ("rank", "player", "shown W-L-T", "raw all W-L-T", "empty", "penalty", "picks counted/total"))
    mismatches = []
    for rank, e, wins, losses, ties in rows:
        allp = list(e.picks)
        rw = sum(1 for p in allp if p.result == "win")
        rl = sum(1 for p in allp if p.result == "loss")
        rt = sum(1 for p in allp if p.result == "push")
        counted = scoring.gridiron_counted_picks(e)
        empty = scoring._gridiron_empty_losses(e)
        pen = scoring.gridiron_penalty_losses(e)
        u = User.query.get(e.user_id)
        line = ("%-5s %-22s %-10s %-14s %-10s %-8s %s/%s" %
                (rank, (u.username if u else "?")[:22], "%s-%s-%s" % (wins, losses, ties),
                 "%s-%s-%s" % (rw, rl, rt), empty, pen, len(counted), len(allp)))
        print(line)
        if (wins, losses, ties) != (rw, rl + empty + pen, rt):
            mismatches.append(line)
    print()
    print("rows where the shown record does not equal the raw pick count:", len(mismatches))
    for m in mismatches:
        print("   ", m)
    print()

    print("=== GRIDIRON MISS ROWS ===")
    misses = GridironMiss.query.all()
    print("total GridironMiss rows:", len(misses))
    for m in misses[:20]:
        e = Entry.query.get(m.entry_id); u = User.query.get(e.user_id) if e else None
        w = Week.query.get(m.week_id)
        print("   %s  week %s" % (u.username if u else "?", w.number if w else "?"))
    print()

    print("=== WEEKS OWED PROCESSING RIGHT NOW ===")
    due = scoring.due_weeks(season)
    for w in due:
        print("   %s week %s (%s) deadline %s" % (w.pool, w.number, w.label, w.pick_deadline))
    if not due:
        print("   none")
    print()
    print("=== end of report ===")
PYEOF

ssh -i "$KEY" $SSHOPTS "$HOST" 'cd /root/gridiron-pools && venv/bin/python3 /tmp/diag_standings.py 2>&1; rm -f /tmp/gpstand.db /tmp/diag_standings.py'
echo
echo "Copy everything above and paste it to Claude."
read -n1 -s -p "Press any key to close this window..."
echo
