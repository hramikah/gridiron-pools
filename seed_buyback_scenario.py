"""Local test scenario: mid-season, Week 2 is open and its deadline hasn't
passed yet.

Week 1 is played and scored in all three pools. Every player has their Week 2
picks in except 'phone', who is left with everything still to do:

  - Drop Dead: eliminated in Week 1 on a losing pick (not a no-show), so the
    buy-back is available and has to be bought manually.
  - Loser Pool: no Week 2 pick yet.
  - Gridiron: no Week 2 picks yet (needs 5).

Local use only -- refuses to run against the live database.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import set_setting
from models import Entry, Game, LoserPoolPoints, Pick, Team, User, Week, db
from scoring import score_game
from testbed_guard import require_testbed_database

SEASON = 2026
ME = "phone"
OTHERS = ["p1", "marcus", "dana", "ray", "kim", "terry", "jo", "sam", "chris"]

# Week 1, already played: (away, home, away_score, home_score, spread on home
# favourite unless negative, over/under)
WEEK1 = [
    ("Buffalo Bills",      "Kansas City Chiefs",   17, 24, "home", 3.0, 47.5),
    ("New York Jets",      "Philadelphia Eagles",  10, 27, "home", 6.5, 44.0),
    ("Miami Dolphins",     "San Francisco 49ers",  14, 21, "home", 4.5, 45.5),
    ("Chicago Bears",      "Detroit Lions",        13, 20, "home", 7.0, 48.0),
    ("Cleveland Browns",   "Baltimore Ravens",      9, 26, "home", 9.5, 41.0),
    ("Minnesota Vikings",  "Green Bay Packers",    16, 23, "home", 2.5, 46.5),
]

# Week 2, not yet played. Last one is the Monday nighter.
WEEK2 = [
    ("Denver Broncos",     "Kansas City Chiefs",  "home", 6.0, 44.5, False),
    ("Dallas Cowboys",     "Philadelphia Eagles", "home", 3.5, 47.0, False),
    ("Seattle Seahawks",   "San Francisco 49ers", "home", 5.0, 42.5, False),
    ("Chicago Bears",      "Detroit Lions",       "home", 7.5, 49.0, False),
    ("Cincinnati Bengals", "Baltimore Ravens",    "home", 2.0, 45.0, False),
    ("Minnesota Vikings",  "Green Bay Packers",   "home", 3.0, 43.5, True),
]

# Drop Dead week 1: who picked what, and did it win?
DROPDEAD_WEEK1 = {
    ME:        ("Buffalo Bills", False),      # eliminated -- a real pick that lost
    "p1":      ("Kansas City Chiefs", True),
    "marcus":  ("Philadelphia Eagles", True),
    "dana":    ("San Francisco 49ers", True),
    "ray":     ("Detroit Lions", True),
    "kim":     ("Buffalo Bills", False),      # eliminated
    "terry":   ("New York Jets", False),      # eliminated
    "jo":      ("Baltimore Ravens", True),
    "sam":     ("Green Bay Packers", True),
    "chris":   ("Miami Dolphins", False),     # eliminated, bought back below
}

# Drop Dead week 2 picks for everyone still alive (not ME)
DROPDEAD_WEEK2 = {
    "p1": "Kansas City Chiefs", "marcus": "Philadelphia Eagles",
    "dana": "San Francisco 49ers", "ray": "Detroit Lions",
    "jo": "Baltimore Ravens", "sam": "Green Bay Packers",
    "chris": "Detroit Lions",
}


def full_name(team):
    return f"{team.city} {team.name}".strip()


def team_by_name(name):
    for t in Team.query.all():
        if full_name(t) == name:
            return t
    raise LookupError(f"No team {name!r} -- run seed.py first.")


def make_games(week, spec, final):
    """One Game row per pool: each pool owns its own copy of the slate."""
    made = {}
    for pool in ("dropdead", "loser", "gridiron"):
        rows = []
        for entry in spec:
            if final:
                away, home, ascore, hscore, fav, spread, ou = entry
                mnf = False
            else:
                away, home, fav, spread, ou, mnf = entry
                ascore = hscore = None
            at, ht = team_by_name(away), team_by_name(home)
            g = Game(
                week_id=week[pool].id, pool=pool, sport="nfl",
                away_team=away, home_team=home,
                away_team_id=at.id, home_team_id=ht.id,
                favorite=fav, spread=spread, over_under=ou,
                kickoff=(datetime.now() + timedelta(days=-8 if final else 3)),
                is_mnf=mnf,
                home_score=hscore, away_score=ascore, is_final=final,
            )
            db.session.add(g)
            rows.append(g)
        db.session.commit()
        made[pool] = rows
    return made


def run():
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    require_testbed_database(app, "seed_buyback_scenario.py")

    random.seed(2026)  # same scenario every time it's rebuilt

    with app.app_context():
        # Clear this season's play, leaving teams/settings/loser points alone.
        Pick.query.delete(synchronize_session=False)
        for e in Entry.query.filter_by(season_year=SEASON).all():
            db.session.delete(e)
        for w in Week.query.filter_by(season_year=SEASON).all():
            db.session.delete(w)   # cascades to that week's games
        db.session.commit()

        now = datetime.now()
        week1, week2 = {}, {}
        for pool in ("dropdead", "loser", "gridiron"):
            w1 = Week(season_year=SEASON, number=1, pool=pool,
                      pick_deadline=now - timedelta(days=7),
                      buyback_open=True, missed_processed=True)
            w2 = Week(season_year=SEASON, number=2, pool=pool,
                      pick_deadline=now + timedelta(days=2),
                      buyback_open=True)
            db.session.add_all([w1, w2])
            db.session.commit()
            week1[pool], week2[pool] = w1, w2

        # Weeks 3-18 on the real calendar, so the season looks whole.
        season_start = datetime(2026, 9, 10).date()
        for number in range(3, 19):
            thu = season_start + timedelta(weeks=number - 1)
            dl = datetime.combine(thu + timedelta(days=2), datetime.min.time()) + timedelta(hours=12)
            for pool in ("dropdead", "loser", "gridiron"):
                db.session.add(Week(season_year=SEASON, number=number, pool=pool,
                                    pick_deadline=dl, buyback_open=(pool == "dropdead" and number <= 4)))
        db.session.commit()

        g1 = make_games(week1, WEEK1, final=True)
        g2 = make_games(week2, WEEK2, final=False)

        # --- players and entries -------------------------------------------
        users = {}
        for name in [ME] + OTHERS:
            u = User.query.filter(db.func.lower(User.username) == name).first()
            if not u:
                u = User(username=name, email=f"{name}@example.com")
                u.set_password("test1234")
                db.session.add(u)
                db.session.commit()
            users[name] = u

        entries = {}
        for name, u in users.items():
            entries[name] = {}
            for pool in ("dropdead", "loser", "gridiron"):
                e = Entry(user_id=u.id, pool=pool, season_year=SEASON, label="Entry 1", paid=True)
                db.session.add(e)
                db.session.commit()
                entries[name][pool] = e

        # --- Drop Dead ------------------------------------------------------
        for name, (team_name, _won) in DROPDEAD_WEEK1.items():
            db.session.add(Pick(entry_id=entries[name]["dropdead"].id, week_id=week1["dropdead"].id,
                                pool="dropdead", team_id=team_by_name(team_name).id))
        db.session.commit()
        for g in g1["dropdead"]:
            score_game(g)   # real scoring: sets results and eliminates losers

        for name, team_name in DROPDEAD_WEEK2.items():
            db.session.add(Pick(entry_id=entries[name]["dropdead"].id, week_id=week2["dropdead"].id,
                                pool="dropdead", team_id=team_by_name(team_name).id))
        db.session.commit()

        # chris already bought back, so the standings show that state
        ce = entries["chris"]["dropdead"]
        ce.is_active, ce.buy_backs_used, ce.buyback_week = True, 1, 1
        db.session.commit()

        # --- Loser Pool -----------------------------------------------------
        # Week 1: pick a team to lose. Half get it right.
        losers_w1 = ["Buffalo Bills", "New York Jets", "Miami Dolphins",
                     "Chicago Bears", "Cleveland Browns", "Minnesota Vikings"]
        winners_w1 = ["Kansas City Chiefs", "Philadelphia Eagles", "San Francisco 49ers"]
        for i, name in enumerate([ME] + OTHERS):
            choice = losers_w1[i % len(losers_w1)] if i % 3 else winners_w1[i % len(winners_w1)]
            db.session.add(Pick(entry_id=entries[name]["loser"].id, week_id=week1["loser"].id,
                                pool="loser", team_id=team_by_name(choice).id))
        db.session.commit()
        for g in g1["loser"]:
            score_game(g)

        # Week 2: everyone but ME has picked
        losers_w2 = ["Denver Broncos", "Dallas Cowboys", "Seattle Seahawks",
                     "Chicago Bears", "Cincinnati Bengals", "Minnesota Vikings"]
        for i, name in enumerate(OTHERS):
            db.session.add(Pick(entry_id=entries[name]["loser"].id, week_id=week2["loser"].id,
                                pool="loser", team_id=team_by_name(losers_w2[i % len(losers_w2)]).id))
        db.session.commit()

        # --- Gridiron (5 picks a week, against the spread / over-under) -----
        def gridiron_picks(name, week_games, week_row):
            picks = []
            games = list(week_games)
            random.shuffle(games)
            for g in games[:4]:
                picks.append(Pick(entry_id=entries[name]["gridiron"].id, week_id=week_row.id,
                                  pool="gridiron", game_id=g.id, market="spread",
                                  side=random.choice(["home", "away"])))
            total_game = games[4]
            picks.append(Pick(entry_id=entries[name]["gridiron"].id, week_id=week_row.id,
                              pool="gridiron", game_id=total_game.id, market="total",
                              side=random.choice(["over", "under"])))
            return picks

        for name in [ME] + OTHERS:          # week 1: everyone played
            db.session.add_all(gridiron_picks(name, g1["gridiron"], week1["gridiron"]))
        db.session.commit()
        for g in g1["gridiron"]:
            score_game(g)

        for name in OTHERS:                 # week 2: everyone but ME
            db.session.add_all(gridiron_picks(name, g2["gridiron"], week2["gridiron"]))
        db.session.commit()

        set_setting("active_week", "2")
        set_setting("season_start_thursday", "2026-09-10")

        # --- report ---------------------------------------------------------
        dd = entries[ME]["dropdead"]
        alive = Entry.query.filter_by(pool="dropdead", season_year=SEASON, is_active=True).count()
        dead = Entry.query.filter_by(pool="dropdead", season_year=SEASON, is_active=False).count()
        print(f"Week 1 scored, Week 2 open (deadline {week2['dropdead'].pick_deadline:%a %b %d %I:%M %p}).")
        print(f"Drop Dead: {alive} alive, {dead} eliminated.")
        print(f"{ME}: eliminated wk{dd.eliminated_week} on a losing pick -> buy-back available.")
        for pool in ("dropdead", "loser", "gridiron"):
            mine = Pick.query.filter_by(entry_id=entries[ME][pool].id, week_id=week2[pool].id).count()
            others = Pick.query.filter(Pick.week_id == week2[pool].id).count() - mine
            print(f"  {pool:9} week 2 -- {ME}: {mine} pick(s), everyone else: {others}")
        print(f"\nLog in as {ME} / orig123 (admin). Others: test1234")


if __name__ == "__main__":
    run()
