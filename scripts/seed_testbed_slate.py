"""Put a full week of games on the LOCAL TEST SITE, so the pick pages have
something to pick.

Refuses to run against anything but a testbed database -- the guard is the
word "testbed" in GRIDIRON_DATABASE_URI, the same marker testbed_guard.py and
helpers.py use.

What it makes, for the coming week, in real time (no frozen clock):

  * one Week per pool, deadline Saturday 12:00 noon Eastern
  * 16 NFL games in each of the three pools -- all 32 teams, each playing once
  * 8 college games in Gridiron only, spread but no over/under
  * every kickoff is AFTER that Saturday deadline, so the whole slate stays
    pickable and nothing is greyed out by the 1-hour kickoff lock
  * an entry in all three pools for every account, if it does not have one

It deletes this season's existing weeks for the three pools first (which
cascades to their games and picks) so it can be run again and again.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

URI = os.environ.get("GRIDIRON_DATABASE_URI", "")
if "testbed" not in URI.lower():
    print("REFUSING TO RUN.")
    print("GRIDIRON_DATABASE_URI is %r, which is not a testbed database." % URI)
    print("This script only ever writes to the local test site.")
    raise SystemExit(1)

from app import create_app  # noqa: E402
from models import Entry, Game, LoserPoolPoints, Team, User, Week, db  # noqa: E402

POOLS = ["gridiron", "loser", "dropdead"]

# 8 college matchups, names spelled out the way the API would give them so a
# later score fetch could match them.
COLLEGE = [
    ("Michigan Wolverines", "Ohio State Buckeyes", "home", 3.5),
    ("Alabama Crimson Tide", "Georgia Bulldogs", "home", 2.5),
    ("Texas Longhorns", "Oklahoma Sooners", "away", 6.5),
    ("USC Trojans", "Notre Dame Fighting Irish", "home", 1.5),
    ("Penn State Nittany Lions", "Wisconsin Badgers", "away", 7.5),
    ("Oregon Ducks", "Washington Huskies", "away", 4.5),
    ("LSU Tigers", "Florida Gators", "away", 3.0),
    ("Clemson Tigers", "Florida State Seminoles", "away", 10.5),
]

SPREADS = [1.5, 2.5, 3.0, 3.5, 4.5, 6.0, 6.5, 7.0, 7.5, 9.5, 10.0, 1.0, 2.0, 5.5, 13.5, 3.5]
TOTALS = [41.5, 43.0, 44.5, 45.5, 47.0, 47.5, 48.5, 49.5, 51.0, 42.5, 46.0, 50.5, 44.0, 40.5, 53.5, 46.5]


def coming_saturday_noon(now):
    """Noon on the next Saturday that has not happened yet."""
    days = (5 - now.weekday()) % 7          # Monday=0 ... Saturday=5
    sat = (now + timedelta(days=days)).replace(hour=12, minute=0, second=0, microsecond=0)
    if sat <= now:
        sat += timedelta(days=7)
    return sat


def main():
    app = create_app()
    with app.app_context():
        season = app.config["CURRENT_SEASON"]
        now = datetime.now()
        deadline = coming_saturday_noon(now)
        sunday = (deadline + timedelta(days=1)).replace(hour=13, minute=0)
        late = sunday.replace(hour=16, minute=25)
        monday = (deadline + timedelta(days=2)).replace(hour=20, minute=15)
        saturday_pm = deadline.replace(hour=15, minute=30)

        teams = Team.query.order_by(Team.name).all()
        if len(teams) < 32:
            print("Only %d teams in this database -- expected 32. Stopping." % len(teams))
            return 1

        # Start clean so this can be re-run. Cascades to games and picks.
        old = Week.query.filter(Week.season_year == season, Week.pool.in_(POOLS)).all()
        for w in old:
            db.session.delete(w)
        db.session.commit()
        print("cleared %d old week(s)" % len(old))

        pairs = [(teams[i], teams[i + 1]) for i in range(0, 32, 2)]

        for pool in POOLS:
            week = Week(season_year=season, number=1, pool=pool,
                        pick_deadline=deadline, buyback_open=(pool == "dropdead"))
            db.session.add(week)
            db.session.flush()

            for i, (away, home) in enumerate(pairs):
                if i < 12:
                    kick, mnf = sunday, False
                elif i < 15:
                    kick, mnf = late, False
                else:
                    kick, mnf = monday, True
                db.session.add(Game(
                    week_id=week.id, pool=pool, sport="nfl",
                    away_team=str(away), home_team=str(home),
                    away_team_id=away.id, home_team_id=home.id,
                    favorite="home" if i % 2 == 0 else "away",
                    spread=SPREADS[i % len(SPREADS)],
                    # Only Gridiron plays totals; the other two pools pick a
                    # team, so an over/under there would be meaningless.
                    over_under=TOTALS[i % len(TOTALS)] if pool == "gridiron" else None,
                    kickoff=kick, is_mnf=mnf,
                ))

            if pool == "gridiron":
                for away, home, fav, spread in COLLEGE:
                    db.session.add(Game(
                        week_id=week.id, pool="gridiron", sport="college",
                        away_team=away, home_team=home,
                        favorite=fav, spread=spread, over_under=None,
                        kickoff=saturday_pm,
                    ))
            db.session.commit()
            n = Game.query.filter_by(week_id=week.id).count()
            print("%-9s week 1, deadline %s, %d games" % (pool, deadline, n))

        # The Loser Pool page needs a points value per team to show anything.
        if not LoserPoolPoints.query.filter_by(season_year=season).count():
            for i, t in enumerate(teams):
                db.session.add(LoserPoolPoints(season_year=season, team_id=t.id, points=10 + i))
            db.session.commit()
            print("seeded loser-pool points for %d teams" % len(teams))

        added = 0
        for u in User.query.all():
            for pool in POOLS:
                if not Entry.query.filter_by(user_id=u.id, pool=pool, season_year=season).first():
                    db.session.add(Entry(user_id=u.id, pool=pool,
                                         season_year=season, label="Entry 1"))
                    added += 1
        db.session.commit()
        print("added %d missing entr%s" % (added, "y" if added == 1 else "ies"))
        print()
        print("Picks are due %s Eastern." % deadline.strftime("%a %b %d at %I:%M %p"))
        print("Every kickoff is after that, so the whole slate is pickable.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
