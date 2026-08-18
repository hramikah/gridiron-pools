"""Advance the local test scenario by one week.

Scores the current week and opens the next one. By default the Drop Dead pick
belonging to 'phone' is made a winner; pass --lose to make it lose instead
(which eliminates the entry and puts the buy-back back on the page).

The next week's slate always includes every team 'phone' has already used, so
the "already used" blocking stays visible.

    venv/bin/python advance_week.py            # phone's pick wins
    venv/bin/python advance_week.py --lose     # phone's pick loses

Local use only.
"""

import random
import sys
from datetime import datetime, timedelta

from app import app
from helpers import set_setting, get_setting
from models import Entry, Game, Pick, Team, User, Week, db
from scoring import score_game

SEASON = 2026
ME = "phone"
POOLS = ("dropdead", "loser", "gridiron")


def build_slate(must_include_ids):
    """Six games. Teams 'phone' has used are guaranteed a slot so the pick
    page keeps showing them greyed out."""
    all_teams = Team.query.all()
    random.shuffle(all_teams)
    must = [t for t in all_teams if t.id in must_include_ids]
    rest = [t for t in all_teams if t.id not in must_include_ids]
    lineup = (must + rest)[:12]
    return [(lineup[i], lineup[i + 1]) for i in range(0, 12, 2)]


def run(make_loss):
    if "/root/" in app.config["SQLALCHEMY_DATABASE_URI"]:
        raise SystemExit("Refusing to run against the live database.")

    with app.app_context():
        current = int(get_setting("active_week") or 1)
        nxt = current + 1
        now = datetime.now()
        random.seed(current * 17)

        me = User.query.filter_by(username=ME).first()
        my_dd = Entry.query.filter_by(user_id=me.id, pool="dropdead", season_year=SEASON).first()
        my_pick = Pick.query.filter_by(entry_id=my_dd.id, pool="dropdead").join(Week).filter(
            Week.number == current).first()
        my_team_id = my_pick.team_id if my_pick else None

        cur = {p: Week.query.filter_by(season_year=SEASON, number=current, pool=p).first() for p in POOLS}
        nxt_w = {p: Week.query.filter_by(season_year=SEASON, number=nxt, pool=p).first() for p in POOLS}
        for pool in POOLS:
            if nxt_w[pool] is None:
                nxt_w[pool] = Week(season_year=SEASON, number=nxt, pool=pool,
                                   pick_deadline=now + timedelta(days=2),
                                   buyback_open=(pool == "dropdead" and nxt <= 4))
                db.session.add(nxt_w[pool])
        db.session.commit()

        # --- score the current week -----------------------------------------
        # Results are decided once on the Drop Dead copy of the slate, then
        # mirrored to the other pools so every pool tells the same story.
        results = {}
        for g in Game.query.filter_by(week_id=cur["dropdead"].id, pool="dropdead").all():
            home_wins = random.random() < 0.5
            if my_team_id in (g.home_team_id, g.away_team_id):
                my_side_home = my_team_id == g.home_team_id
                # --lose flips whichever side 'phone' is on.
                home_wins = (not my_side_home) if make_loss else my_side_home
            hs, as_ = (24, 17) if home_wins else (17, 24)
            results[(g.away_team, g.home_team)] = (as_, hs)

        for pool in POOLS:
            week = cur[pool]
            week.pick_deadline = now - timedelta(days=1)
            week.missed_processed = True   # skip penalties: this is a hand-built scenario
            for g in Game.query.filter_by(week_id=week.id, pool=pool).all():
                score = results.get((g.away_team, g.home_team))
                if score:
                    g.away_score, g.home_score, g.is_final = score[0], score[1], True
        db.session.commit()
        for pool in POOLS:
            for g in Game.query.filter_by(week_id=cur[pool].id, pool=pool).all():
                score_game(g)

        # --- open the next week ---------------------------------------------
        used = my_dd.used_team_ids()
        slate = build_slate(used)
        for pool in POOLS:
            nxt_w[pool].pick_deadline = now + timedelta(days=2)
            nxt_w[pool].missed_processed = False
            Game.query.filter_by(week_id=nxt_w[pool].id, pool=pool).delete(synchronize_session=False)
        db.session.commit()

        games = {}
        for pool in POOLS:
            rows = []
            for i, (away, home) in enumerate(slate):
                g = Game(week_id=nxt_w[pool].id, pool=pool, sport="nfl",
                         away_team=f"{away.city} {away.name}", home_team=f"{home.city} {home.name}",
                         away_team_id=away.id, home_team_id=home.id,
                         favorite="home", spread=random.choice([2.5, 3.0, 4.5, 6.0, 7.0]),
                         over_under=random.choice([41.5, 44.0, 45.5, 47.0, 48.5]),
                         kickoff=now + timedelta(days=3), is_mnf=(i == len(slate) - 1))
                db.session.add(g)
                rows.append(g)
            db.session.commit()
            games[pool] = rows

        # --- everyone but ME picks the new week ------------------------------
        for u in User.query.filter(User.username != ME).all():
            dd = Entry.query.filter_by(user_id=u.id, pool="dropdead", season_year=SEASON).first()
            if dd and dd.is_active:
                theirs = dd.used_team_ids()
                options = [g.home_team_id for g in games["dropdead"] if g.home_team_id not in theirs]
                options += [g.away_team_id for g in games["dropdead"] if g.away_team_id not in theirs]
                if options:
                    db.session.add(Pick(entry_id=dd.id, week_id=nxt_w["dropdead"].id,
                                        pool="dropdead", team_id=random.choice(options)))
            lp = Entry.query.filter_by(user_id=u.id, pool="loser", season_year=SEASON).first()
            if lp:
                db.session.add(Pick(entry_id=lp.id, week_id=nxt_w["loser"].id, pool="loser",
                                    team_id=random.choice(games["loser"]).away_team_id))
            gi = Entry.query.filter_by(user_id=u.id, pool="gridiron", season_year=SEASON).first()
            if gi:
                slate_g = list(games["gridiron"])
                random.shuffle(slate_g)
                for g in slate_g[:4]:
                    db.session.add(Pick(entry_id=gi.id, week_id=nxt_w["gridiron"].id, pool="gridiron",
                                        game_id=g.id, market="spread",
                                        side=random.choice(["home", "away"])))
                db.session.add(Pick(entry_id=gi.id, week_id=nxt_w["gridiron"].id, pool="gridiron",
                                    game_id=slate_g[4].id, market="total",
                                    side=random.choice(["over", "under"])))
        db.session.commit()

        set_setting("active_week", str(nxt))

        my_pick = Pick.query.filter_by(entry_id=my_dd.id, pool="dropdead").join(Week).filter(
            Week.number == current).first()
        used_names = sorted(str(Team.query.get(t)) for t in my_dd.used_team_ids())
        alive = Entry.query.filter_by(pool="dropdead", season_year=SEASON, is_active=True).count()
        print(f"Week {current} scored -> Week {nxt} open "
              f"(deadline {nxt_w['dropdead'].pick_deadline:%a %b %d %I:%M %p}).")
        if my_pick:
            print(f"{ME}'s week {current} pick: {my_pick.team} -> {my_pick.result}")
        print(f"{ME}: active={my_dd.is_active} eliminated_week={my_dd.eliminated_week} "
              f"buy_backs_used={my_dd.buy_backs_used}")
        print(f"{ME} used teams (blocked): {', '.join(used_names)}")
        print(f"Drop Dead alive: {alive}")
        print(f"Week {nxt} slate:")
        for g in games["dropdead"]:
            marks = []
            if g.away_team_id in my_dd.used_team_ids():
                marks.append(f"{g.away_team} used")
            if g.home_team_id in my_dd.used_team_ids():
                marks.append(f"{g.home_team} used")
            print(f"   {g.away_team} @ {g.home_team}" + (f"   <- {', '.join(marks)}" if marks else ""))


if __name__ == "__main__":
    run(make_loss="--lose" in sys.argv)
