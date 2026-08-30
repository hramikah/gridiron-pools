#!/bin/bash
# ---------------------------------------------------------------------------
# Why didn't the Loser Pool auto-pick fire?  READ-ONLY.
#
# The no-show auto-pick needs a game flagged MNF whose teams are LINKED to the
# real 32 NFL clubs -- a game carrying only typed-in names has no link, so
# there is no team to assign. This says which of those is missing.
#
# Double-click, then copy everything in this window to Claude.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null
[ -f "$KEY" ] || { echo "Missing $KEY -- cannot reach the droplet."; read -n1 -s -p "Press any key..."; echo; exit 1; }

ssh -i "$KEY" $SSHOPTS "$HOST" 'cat > /tmp/diag_loser.py' <<'PYEOF'
import os, re, sqlite3, subprocess, sys
REPO = "/root/gridiron-pools"; sys.path.insert(0, REPO); os.chdir(REPO)
unit = subprocess.run(["systemctl","show","gridiron-server","-p","Environment"],
                      capture_output=True, text=True).stdout
m = re.search(r"GRIDIRON_DATABASE_URI=(\S+)", unit or "")
src = (m.group(1) if m else "").replace("sqlite:///","") or os.path.join(REPO,"instance/pools.db")
COPY = "/tmp/gploser.db"
if os.path.exists(COPY): os.remove(COPY)
a=sqlite3.connect(src); b=sqlite3.connect(COPY); a.backup(b); b.close(); a.close()
os.environ["GRIDIRON_DATABASE_URI"] = "sqlite:///"+COPY
print("copy of:", src)
print("code has the loser-preseason fix:",
      "an entry that forgot still gets the MNF visitor assigned" in open(REPO+"/scoring.py").read())
print()

from app import create_app
import helpers, scoring
from models import Entry, Game, Pick, Week, db
app = create_app()
with app.app_context():
    season = app.config["CURRENT_SEASON"]
    weeks = sorted(Week.query.filter_by(season_year=season, pool="loser").all(),
                   key=helpers.week_sort_key)
    for w in weeks:
        games = Game.query.filter_by(week_id=w.id, pool="loser").all()
        if not games and not w.missed_processed:
            continue
        print("=== %s (number %s) deadline %s  passed=%s  missed_processed=%s" %
              (w.label, w.number, w.pick_deadline, helpers.deadline_passed(w), w.missed_processed))
        if not games:
            print("    no games\n"); continue
        print("    %-34s %-6s %-9s %-9s %s" % ("matchup","MNF","away_id","home_id","kickoff"))
        for g in games:
            print("    %-34s %-6s %-9s %-9s %s" % (
                "%s @ %s" % (g.away_team, g.home_team), bool(g.is_mnf),
                g.away_team_id, g.home_team_id, g.kickoff))
        mnf = (Game.query.filter_by(week_id=w.id, is_mnf=True)
               .order_by(Game.kickoff.desc()).first())
        entries = Entry.query.filter_by(pool="loser", season_year=season).all()
        picked = {p.entry_id for p in Pick.query.filter_by(pool="loser", week_id=w.id).all()}
        missing = [e for e in entries if e.id not in picked]
        print("    entries: %d, with a pick: %d, WITHOUT a pick: %d"
              % (len(entries), len(entries)-len(missing), len(missing)))
        if not mnf:
            print("    >>> no game is flagged MNF -- nothing to assign")
        elif not mnf.away_team_id:
            print("    >>> MNF game '%s @ %s' has NO away_team_id: it is not linked to a"
                  % (mnf.away_team, mnf.home_team))
            print("        real Team row, so there is no team to hand out. Re-add that game"
                  "\n        picking the teams from the dropdowns rather than typing names.")
        else:
            from models import Team
            t = db.session.get(Team, mnf.away_team_id)
            print("    >>> would assign: %s (visitor of '%s @ %s') to %d entries"
                  % (t.name if t else mnf.away_team_id, mnf.away_team, mnf.home_team, len(missing)))
        print()
    print("=== end of report ===")
PYEOF
ssh -i "$KEY" $SSHOPTS "$HOST" 'cd /root/gridiron-pools && venv/bin/python3 /tmp/diag_loser.py 2>&1; rm -f /tmp/gploser.db /tmp/diag_loser.py'
echo
echo "Copy everything above and paste it to Claude."
read -n1 -s -p "Press any key to close this window..."
echo
