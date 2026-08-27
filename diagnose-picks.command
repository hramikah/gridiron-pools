#!/bin/bash
# ---------------------------------------------------------------------------
# Why can't players change their Gridiron picks?  READ-ONLY.
#
# Copies the live database to a throwaway file on the droplet and asks the
# site's own code what it thinks the time, the week, the deadline and the
# pick page look like. Nothing real is written or deleted.
#
# Double-click, wait, then copy everything in this window to Claude.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== Gridiron pick-page diagnosis (read-only) ==="
echo

if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" 'cat > /tmp/diag_picks.py' <<'PYEOF'
"""Read-only diagnosis of why Gridiron picks can't be changed on the live site.

Works on a throwaway COPY of the live database, so nothing real is touched.
"""
import os, re, sqlite3, sys, subprocess

REPO = "/root/gridiron-pools"
sys.path.insert(0, REPO)
os.chdir(REPO)

# --- what the live process is actually configured with -------------------
print("=== LIVE SERVICE CONFIG ===")
try:
    unit = subprocess.run(["systemctl", "show", "gridiron-server",
                           "-p", "Environment", "-p", "ExecStart"],
                          capture_output=True, text=True).stdout
    print(unit.strip() or "(no output)")
except Exception as e:
    print("could not read unit:", e)
live_uri = None
for line in (unit or "").splitlines():
    m = re.search(r"GRIDIRON_DATABASE_URI=(\S+)", line)
    if m:
        live_uri = m.group(1)
print("GRIDIRON_DATABASE_URI seen in unit:", live_uri)
print()

# --- copy the database the live site uses --------------------------------
src = (live_uri or "").replace("sqlite:///", "") or os.path.join(REPO, "instance/pools.db")
if not os.path.isabs(src):
    src = os.path.join(REPO, src)
if not os.path.exists(src):
    alt = os.path.join(REPO, "instance/testbed-demo.db")
    print("!! %s not found; falling back to %s" % (src, alt))
    src = alt
COPY = "/tmp/gpdiag.db"
if os.path.exists(COPY):
    os.remove(COPY)
s = sqlite3.connect(src)
d = sqlite3.connect(COPY)
s.backup(d)
d.close(); s.close()
print("Diagnosing a copy of:", src)
print()

os.environ["GRIDIRON_DATABASE_URI"] = "sqlite:///" + COPY

from app import create_app
import helpers, scoring
from models import Entry, Game, Pick, User, db

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

with app.app_context():
    season = app.config["CURRENT_SEASON"]
    print("=== CLOCK AND WEEK ===")
    print("SEASON   :", season)
    print("real now (ET):", helpers.datetime.now(helpers.EASTERN).replace(tzinfo=None))
    print("app now  :", helpers.now_eastern())
    print("testbed clock setting in the live DB:", helpers.get_setting("testbed_clock"))
    print("  (only applied when the database path contains 'testbed')")
    print("live path contains 'testbed':", "testbed" in (live_uri or "").lower())
    print("active_week setting:", helpers.get_setting("active_week"))
    print()

    week = helpers.get_current_week(season, "gridiron")
    if week is None:
        print("NO GRIDIRON WEEK FOUND -- that alone would break the pick page.")
        sys.exit(0)
    print("WEEK     : #%s  %s  (id=%s)" % (week.number, week.label, week.id))
    print("DEADLINE :", week.pick_deadline)
    print("deadline_passed:", helpers.deadline_passed(week))
    print()

    games = Game.query.filter_by(week_id=week.id, pool="gridiron").order_by(
        Game.kickoff.asc().nullslast()).all()
    npick = sum(1 for g in games if helpers.game_pickable(g))
    print("=== GAMES ===")
    print("%d gridiron games this week: %d pickable, %d past the 1-hour kickoff lock"
          % (len(games), npick, len(games) - npick))
    for g in games[:8]:
        print("   %-40s kickoff=%s pickable=%s"
              % ("%s @ %s" % (g.away_team, g.home_team), g.kickoff, helpers.game_pickable(g)))
    print()

    entries = Entry.query.filter_by(pool="gridiron", season_year=season).all()
    withpicks = [e for e in entries
                 if Pick.query.filter_by(entry_id=e.id, week_id=week.id, pool="gridiron").count()]
    print("=== ENTRIES ===")
    print("%d gridiron entries, %d with at least one pick this week"
          % (len(entries), len(withpicks)))
    target = withpicks[0] if withpicks else (entries[0] if entries else None)
    if target is None:
        print("No gridiron entries at all -- nothing to render.")
        sys.exit(0)
    user = User.query.get(target.user_id)
    picks = Pick.query.filter_by(entry_id=target.id, week_id=week.id, pool="gridiron").all()
    print("rendering as %s (entry %s), %d picks, limit %s, active=%s"
          % (user.username, target.id, len(picks),
             scoring.gridiron_pick_limit(target, week), target.is_active))
    print()

    client = app.test_client()
    # base_url matters: the session cookie is set for that host, and a
    # later request to a different host would not send it -- the page would
    # come back as the logged-out login redirect.
    with client.session_transaction(base_url="https://gridironinvestment.com") as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    resp = client.get("/gridiron/pick", base_url="https://gridironinvestment.com",
                      follow_redirects=True)
    html = resp.get_data(as_text=True)
    print("=== RENDERED PICK PAGE ===")
    print("HTTP %s, %d bytes" % (resp.status_code, len(html)))
    print("  hidden remove forms (id=rmpick-*)   :", len(re.findall(r'id="rmpick-\d+"', html)))
    print("  X buttons (form=rmpick-*)           :", len(re.findall(r'form="rmpick-\d+"', html)))
    print("  <form> tags on the page             :", len(re.findall(r"<form\b", html)))
    print("  disabled attributes                 :", len(re.findall(r"\bdisabled\b", html)))
    print("  'Kickoff too soon' notes            :", html.count("Kickoff too soon"))
    print("  'PICKS ARE LOCKED' present          :", "PICKS ARE LOCKED" in html)
    print("  'deadline for this week has passed' :", "deadline for this week has passed" in html)
    print("  data-max-picks values               :", re.findall(r'data-max-picks="(\d+)"', html))
    print("  'Locked in so far' present          :", "Locked in so far" in html)
    print("  submit button present               :", "gridiron-submit" in html)
    print()

    if picks:
        p = picks[0]
        print("=== SIMULATED REMOVE (on the copy only) ===")
        print("removing pick id=%s  %s  %s" % (p.id, p.game.label if p.game else "?", p.market))
        before = Pick.query.filter_by(entry_id=target.id, week_id=week.id).count()
        r2 = client.post("/gridiron/picks/%s/remove" % p.id,
                         base_url="https://gridironinvestment.com", follow_redirects=True)
        after = Pick.query.filter_by(entry_id=target.id, week_id=week.id).count()
        print("HTTP %s   picks before=%s after=%s   %s"
              % (r2.status_code, before, after,
                 "REMOVED OK" if after < before else "*** SERVER REFUSED ***"))
        body = r2.get_data(as_text=True)
        for msg in ("Pick removed", "deadline for this week has passed",
                    "Too late to remove", "Not your pick"):
            if msg in body:
                print("  message:", msg)
    print()
    print("=== end of report ===")
PYEOF

ssh -i "$KEY" $SSHOPTS "$HOST" 'cd /root/gridiron-pools && venv/bin/python3 /tmp/diag_picks.py 2>&1; rm -f /tmp/gpdiag.db /tmp/diag_picks.py'
echo
echo "Copy everything above and paste it to Claude."
read -n1 -s -p "Press any key to close this window..."
echo
