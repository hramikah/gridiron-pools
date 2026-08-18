"""Advance the local test scenario from Week 2 to Week 3.

Scores Week 2 (the Cowboys lose, so 'phone' -- who bought back and picked
them -- is eliminated a second time and has to buy back again), then opens
Week 3 with a slate that deliberately includes both Buffalo and Dallas, so
the already-used teams show as blocked on the pick page.

Everyone except 'phone' has their Week 3 picks in. Local use only.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import set_setting
from models import Entry, Game, Pick, Team, User, Week, db
from scoring import score_game

SEASON = 2026
ME = "phone"

# Week 2 results: (away, home, away_score, home_score). Dallas loses at
# Philadelphia, which is the pick 'phone' made after buying back.
WEEK2_RESULTS = {
    "Kansas City Chiefs":  ("Denver Broncos", 13, 27),
    "Philadelphia Eagles": ("Dallas Cowboys", 16, 31),   # Cowboys lose
    "San Francisco 49ers": ("Seattle Seahawks", 20, 24),
    "Detroit Lions":       ("Chicago Bears", 10, 28),
    "Baltimore Ravens":    ("Cincinnati Bengals", 21, 17),  # Ravens lose at home
    "Green Bay Packers":   ("Minnesota Vikings", 14, 30),
}

# Week 3 slate. Buffalo and Dallas are in it on purpose: 'phone' has used
# both, so they must render as unavailable.
WEEK3 = [
    ("Buffalo Bills",      "New England Patriots", "away", 5.5, 44.0, False),
    ("Dallas Cowboys",     "New York Giants",      "away", 3.0, 46.5, False),
    ("Kansas City Chiefs", "Las Vegas Raiders",    "away", 7.0, 45.0, False),
    ("Philadelphia Eagles", "Washington Commanders", "away", 4.5, 43.5, False),
    ("Detroit Lions",      "Tampa Bay Buccaneers", "away", 2.5, 48.5, False),
    ("Green Bay Packers",  "Arizona Cardinals",    "away", 3.5, 47.0, True),
]


def team_by_name(name):
    for t in Team.query.all():
        if f"{t.city} {t.name}".strip() == name:
            return t
    raise LookupError(f"No team {name!r}")


def run():
    if "/root/" in app.config["SQLALCHEMY_DATABASE_URI"]:
        raise SystemExit("Refusing to run against the live database.")
    random.seed(3)

    with app.app_context():
        now = datetime.now()
        w2 = {p: Week.query.filter_by(season_year=SEASON, number=2, pool=p).first()
              for p in ("dropdead", "loser", "gridiron")}
        w3 = {p: Week.query.filter_by(season_year=SEASON, number=3, pool=p).first()
              for p in ("dropdead", "loser", "gridiron")}

        # --- close out week 2 ------------------------------------------------
        # Its deadline moves into the past so the week reads as played.
        for pool, week in w2.items():
            week.pick_deadline = now - timedelta(days=1)
            # Penalties are skipped deliberately: 'phone' never entered Gridiron
            # picks for week 2, and a miss there would muddy the Drop Dead test.
            week.missed_processed = True
            for g in Game.query.filter_by(week_id=week.id, pool=pool).all():
                for home_name, (away_name, ascore, hscore) in WEEK2_RESULTS.items():
                    if g.home_team == home_name and g.away_team == away_name:
                        g.home_score, g.away_score, g.is_final = hscore, ascore, True
        db.session.commit()
        for pool, week in w2.items():
            for g in Game.query.filter_by(week_id=week.id, pool=pool).all():
                score_game(g)

        # --- open week 3 -----------------------------------------------------
        for pool, week in w3.items():
            week.pick_deadline = now + timedelta(days=2)
            week.missed_processed = False
            Game.query.filter_by(week_id=week.id, pool=pool).delete(synchronize_session=False)
        db.session.commit()

        games3 = {}
        for pool in ("dropdead", "loser", "gridiron"):
            rows = []
            for away, home, fav, spread, ou, mnf in WEEK3:
                at, ht = team_by_name(away), team_by_name(home)
                g = Game(week_id=w3[pool].id, pool=pool, sport="nfl",
                         away_team=away, home_team=home,
                         away_team_id=at.id, home_team_id=ht.id,
                         favorite=fav, spread=spread, over_under=ou,
                         kickoff=now + timedelta(days=3), is_mnf=mnf)
                db.session.add(g)
                rows.append(g)
            db.session.commit()
            games3[pool] = rows

        # --- week 3 picks for everyone but ME --------------------------------
        others = [u for u in User.query.all() if u.username != ME]

        for u in others:
            dd = Entry.query.filter_by(user_id=u.id, pool="dropdead", season_year=SEASON).first()
            if dd and dd.is_active:
                used = dd.used_team_ids()
                choices = [g.home_team_id for g in games3["dropdead"] if g.home_team_id not in used]
                if choices:
                    db.session.add(Pick(entry_id=dd.id, week_id=w3["dropdead"].id,
                                        pool="dropdead", team_id=random.choice(choices)))

            lp = Entry.query.filter_by(user_id=u.id, pool="loser", season_year=SEASON).first()
            if lp:
                g = random.choice(games3["loser"])
                db.session.add(Pick(entry_id=lp.id, week_id=w3["loser"].id,
                                    pool="loser", team_id=g.away_team_id))

            gi = Entry.query.filter_by(user_id=u.id, pool="gridiron", season_year=SEASON).first()
            if gi:
                slate = list(games3["gridiron"])
                random.shuffle(slate)
                for g in slate[:4]:
                    db.session.add(Pick(entry_id=gi.id, week_id=w3["gridiron"].id, pool="gridiron",
                                        game_id=g.id, market="spread",
                                        side=random.choice(["home", "away"])))
                db.session.add(Pick(entry_id=gi.id, week_id=w3["gridiron"].id, pool="gridiron",
                                    game_id=slate[4].id, market="total",
                                    side=random.choice(["over", "under"])))
        db.session.commit()

        set_setting("active_week", "3")

        # --- report -----------------------------------------------------------
        me = User.query.filter_by(username=ME).first()
        dd = Entry.query.filter_by(user_id=me.id, pool="dropdead", season_year=SEASON).first()
        used = sorted(str(Team.query.get(t)) for t in dd.used_team_ids())
        alive = Entry.query.filter_by(pool="dropdead", season_year=SEASON, is_active=True).count()
        print(f"Week 2 scored. Week 3 open (deadline {w3['dropdead'].pick_deadline:%a %b %d %I:%M %p}).")
        print(f"{ME}: active={dd.is_active} eliminated_week={dd.eliminated_week} "
              f"buy_backs_used={dd.buy_backs_used}")
        print(f"{ME} used teams (blocked from here on): {', '.join(used)}")
        print(f"Drop Dead alive: {alive}")
        for pool in ("dropdead", "loser", "gridiron"):
            mine = Pick.query.join(Entry).filter(
                Entry.user_id == me.id, Pick.week_id == w3[pool].id).count()
            total = Pick.query.filter_by(week_id=w3[pool].id).count()
            print(f"  {pool:9} week 3 -- {ME}: {mine}, others: {total - mine}")


if __name__ == "__main__":
    run()
