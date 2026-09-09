#!/bin/bash
# ---------------------------------------------------------------------------
# What is in the preseason weeks, and what would happen if they went?
# READ-ONLY. Nothing on the live site is written, deleted or restarted.
#
# It copies the live database to a throwaway file on the droplet, reports
# every scrap of preseason data, then performs the purge ON THAT COPY and
# prints the standings before and after so you can see exactly what changes
# before deciding anything.
#
# Double-click, wait, then copy everything in this window to Claude.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== Preseason contents + dry-run purge (read-only) ==="
echo

if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" 'cat > /tmp/diag_preseason.py' <<'PYEOF'
"""Read-only: what the preseason weeks hold, and what removing them would do.

Everything below runs against a throwaway COPY of the live database. The purge
at the end is performed on that copy only, so the before/after standings are a
real simulation rather than a guess.
"""
import os, re, sqlite3, sys, subprocess
from collections import Counter

REPO = "/root/gridiron-pools"
sys.path.insert(0, REPO)
os.chdir(REPO)

print("=== WHAT CODE IS LIVE ===")
print(subprocess.run(["git", "log", "-1", "--pretty=%h %ad %s", "--date=short"],
                     capture_output=True, text=True, cwd=REPO).stdout.strip())
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
print("live database:", src)
print("size on disk:", os.path.getsize(src), "bytes")
print()

COPY = "/tmp/gppre.db"
if os.path.exists(COPY):
    os.remove(COPY)
s = sqlite3.connect(src); d = sqlite3.connect(COPY); s.backup(d); d.close(); s.close()
os.environ["GRIDIRON_DATABASE_URI"] = "sqlite:///" + COPY

from app import create_app
import helpers, scoring
from models import Entry, Game, GridironMiss, Pick, Setting, User, Week, db

app = create_app()
with app.app_context():
    season = app.config["CURRENT_SEASON"]
    POOLS = ("gridiron", "dropdead", "loser")

    print("=== EVERY WEEK, BY POOL ===")
    print("%-10s %-6s %-22s %-10s %-20s %-7s %-7s %s" %
          ("pool", "num", "label", "preseason", "deadline", "games", "picks", "missed_processed"))
    pre_ids, pre_weeks = [], []
    for pool in POOLS:
        weeks = sorted(Week.query.filter_by(season_year=season, pool=pool).all(),
                       key=helpers.week_sort_key)
        for w in weeks:
            ng = Game.query.filter_by(week_id=w.id).count()
            np = Pick.query.filter_by(week_id=w.id).count()
            print("%-10s %-6s %-22s %-10s %-20s %-7s %-7s %s" %
                  (pool, w.number, w.label, w.is_preseason, w.pick_deadline, ng, np,
                   w.missed_processed))
            if w.is_preseason or w.number >= 101:
                pre_ids.append(w.id); pre_weeks.append(w)
    print()
    print("preseason weeks found:", len(pre_ids))
    if not pre_ids:
        print("Nothing to remove. Stopping here.")
        raise SystemExit(0)
    print()

    print("=== PRESEASON PICK RESULTS ===")
    for w in pre_weeks:
        ps = Pick.query.filter_by(week_id=w.id).all()
        if ps:
            print("  %-10s %-22s %s" % (w.pool, w.label, dict(Counter((p.result or "ungraded") for p in ps))))
    print("  total preseason picks:", Pick.query.filter(Pick.week_id.in_(pre_ids)).count())
    print("  total preseason games:", Game.query.filter(Game.week_id.in_(pre_ids)).count())
    print()

    print("=== GRIDIRON MISS ROWS ===")
    all_misses = GridironMiss.query.all()
    pre_misses = [m for m in all_misses if m.week_id in pre_ids]
    print("  total:", len(all_misses), " pointing at a preseason week:", len(pre_misses))
    print("  (these are the rows that grant a forgiven miss + an 8-pick makeup")
    print("   week with 2 penalty losses -- in Week 1 if they survive)")
    print("  NOTE: GridironMiss has no cascade from Week, so deleting a week")
    print("        without deleting these first leaves orphan rows.")
    print()

    print("=== ENTRY STATE THAT PRESEASON COULD HAVE TOUCHED ===")
    odd, by_pool = [], Counter()
    for e in Entry.query.filter_by(season_year=season).all():
        if (not e.is_active) or e.eliminated_week or e.buyback_week or (e.buy_backs_used or 0):
            by_pool[e.pool] += 1
            u = db.session.get(User, e.user_id)
            odd.append("  %-10s %-20s active=%s elim_wk=%s bb_wk=%s bb_used=%s bb_PAID=%s entry_paid=%s" %
                       (e.pool, (u.username if u else "?")[:20], e.is_active,
                        e.eliminated_week, e.buyback_week, e.buy_backs_used,
                        e.buy_backs_paid, e.paid))
    if odd:
        print("  entries carrying elimination / buy-back / bench state:", dict(by_pool))
        print("  (deleting weeks does NOT reset these -- if any of it came from a")
        print("   preseason week it has to be cleared deliberately)")
        for line in odd[:25]:
            print(line)
        if len(odd) > 25:
            print("  ... and %s more" % (len(odd) - 25))
    else:
        print("  none -- no eliminations, buy-backs or benchings on record")
    print()

    print("=== HAS ANY MONEY BEEN RECORDED AGAINST A BUY-BACK? ===")
    paid_bb = [e for e in Entry.query.filter_by(season_year=season).all() if (e.buy_backs_paid or 0)]
    print("  entries with buy_backs_paid > 0:", len(paid_bb))
    for e in paid_bb[:20]:
        u = db.session.get(User, e.user_id)
        print("    %-10s %-20s used=%s paid=%s at=%s" %
              (e.pool, (u.username if u else "?")[:20], e.buy_backs_used, e.buy_backs_paid,
               e.buy_backs_paid_at))
    print("  (resetting buy-backs would wipe these -- check nobody has actually handed over $30)")
    print()

    print("=== HAS ANY NON-PRESEASON WEEK BEEN PLAYED? ===")
    for pool in POOLS:
        played = [w for w in Week.query.filter_by(season_year=season, pool=pool).all()
                  if not (w.is_preseason or w.number >= 101)
                  and Pick.query.filter_by(week_id=w.id).count()]
        print("  %-10s regular weeks with picks in them: %s" %
              (pool, [w.number for w in played] or "none"))
    print()

    print("=== SETTINGS ===")
    aw = helpers.get_setting("active_week")
    print("  active_week:", repr(aw), "(pinned to a preseason number?" ,
          bool(aw and str(aw).isdigit() and int(aw) >= 101), ")")
    print()

    print("=== LOSER POINTS COMING FROM PRESEASON PICKS ===")
    tot = 0
    for e in Entry.query.filter_by(season_year=season, pool="loser").all():
        pts = sum((p.points or 0) for p in e.picks if p.week_id in pre_ids)
        tot += pts
    print("  total points across all entries that came from preseason weeks:", tot)
    print()

    print("=== STANDINGS BEFORE (top 15) ===")
    def show(rows, kind):
        for r in rows[:15]:
            if kind == "gridiron":
                rank, e, w, l, t = r
                u = db.session.get(User, e.user_id)
                print("   %-4s %-20s %s-%s-%s" % (rank, (u.username if u else "?")[:20], w, l, t))
            elif kind == "loser":
                rank, e, total = r
                u = db.session.get(User, e.user_id)
                print("   %-4s %-20s %s" % (rank, (u.username if u else "?")[:20], total))
    print(" GRIDIRON:"); show(scoring.standings_gridiron(season), "gridiron")
    print(" LOSER:");    show(scoring.standings_loser(season), "loser")
    print()

    # ---- the purge, ON THE COPY ONLY -------------------------------------
    print("=== SIMULATING THE PURGE ON THE THROWAWAY COPY ===")
    n_miss = GridironMiss.query.filter(GridironMiss.week_id.in_(pre_ids)).delete(synchronize_session=False)
    n_pick = Pick.query.filter(Pick.week_id.in_(pre_ids)).delete(synchronize_session=False)
    n_game = Game.query.filter(Game.week_id.in_(pre_ids)).delete(synchronize_session=False)
    n_week = Week.query.filter(Week.id.in_(pre_ids)).delete(synchronize_session=False)
    row = db.session.get(Setting, "active_week")
    if row and str(row.value).isdigit() and int(row.value) >= 101:
        row.value = ""
        print("  active_week was pinned to a preseason week -- cleared")
    db.session.commit()
    print("  deleted: %s miss rows, %s picks, %s games, %s weeks" % (n_miss, n_pick, n_game, n_week))
    print()

    print("=== ORPHAN CHECK AFTER THE PURGE ===")
    orphan_misses = [m for m in GridironMiss.query.all() if db.session.get(Week, m.week_id) is None]
    orphan_picks = [p for p in Pick.query.all() if db.session.get(Week, p.week_id) is None]
    orphan_games = [g for g in Game.query.all() if db.session.get(Week, g.week_id) is None]
    print("  orphan miss rows:", len(orphan_misses),
          " orphan picks:", len(orphan_picks), " orphan games:", len(orphan_games))
    print()

    print("=== STANDINGS AFTER (top 15) ===")
    print(" GRIDIRON:"); show(scoring.standings_gridiron(season), "gridiron")
    print(" LOSER:");    show(scoring.standings_loser(season), "loser")
    print()

    print("=== PAGES STILL RENDER AFTER THE PURGE? ===")
    c = app.test_client()
    for path in ("/", "/standings", "/reports", "/gridiron/standings",
                 "/loser/standings", "/dropdead/standings", "/scores"):
        try:
            print("   %-24s HTTP %s" % (path, c.get(path, follow_redirects=True).status_code))
        except Exception as exc:
            print("   %-24s EXCEPTION %r" % (path, exc))
    print()
    print("=== end of report -- the live database was not touched ===")
PYEOF

ssh -i "$KEY" $SSHOPTS "$HOST" 'cd /root/gridiron-pools && venv/bin/python3 /tmp/diag_preseason.py 2>&1; rm -f /tmp/gppre.db /tmp/diag_preseason.py'
echo
echo "Copy everything above and paste it to Claude."
read -n1 -s -p "Press any key to close this window..."
echo
