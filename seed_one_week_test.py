"""One-off test-data generator: a single mock Week 1 (NFL + a couple
fictional college games) with a near-future deadline, so real accounts can
exercise the pick flow (incremental picks, per-game kickoff cutoffs, etc.)
without waiting on the real season's schedule.

No fake players are created -- pick with your existing real accounts.
No picks or results are pre-filled; games are left open to pick against.

Safe to re-run: wipes any existing Week/Game/Pick/GridironMiss data for the
current season before rebuilding. Real users' Entry rows are left alone.
"""

import random
from datetime import timedelta

from app import app
from helpers import now_eastern
from models import Entry, Game, GridironMiss, Pick, Team, Week, db

FICTIONAL_COLLEGE_TEAMS = [
    "Central State Wildcats", "Lakeside Tech Hawks",
    "Riverside University Bears", "Highland College Eagles",
]


def wipe_current_season(season_year):
    week_ids = [w.id for w in Week.query.filter_by(season_year=season_year).all()]
    if week_ids:
        Pick.query.filter(Pick.week_id.in_(week_ids)).delete(synchronize_session=False)
        GridironMiss.query.filter(GridironMiss.week_id.in_(week_ids)).delete(synchronize_session=False)
        Game.query.filter(Game.week_id.in_(week_ids)).delete(synchronize_session=False)
        Week.query.filter(Week.id.in_(week_ids)).delete(synchronize_session=False)
    db.session.commit()


def make_week(season_year, rng):
    now = now_eastern()
    # Next Saturday at noon, at least 2 days out.
    days_ahead = (5 - now.weekday()) % 7  # Saturday = 5
    if days_ahead < 2:
        days_ahead += 7
    deadline = (now + timedelta(days=days_ahead)).replace(hour=12, minute=0, second=0, microsecond=0)

    week = Week(season_year=season_year, number=1, pick_deadline=deadline)
    db.session.add(week)
    db.session.commit()

    teams = Team.query.all()
    rng.shuffle(teams)
    games = []
    # Thursday night (before the deadline -- exercises the per-game 1hr-early
    # cutoff), most games Sunday, one Monday nighter -- like a real NFL week.
    kickoff_offsets_hours = [-42, -41] + [24 + h for h in (13, 13, 16, 16, 16, 16, 16, 16, 16, 20)] + [48, 48, 48]
    for i in range(0, len(teams), 2):
        home, away = teams[i], teams[i + 1]
        favorite = rng.choice(["home", "away", None])
        spread = 0 if favorite is None else round(rng.uniform(1, 13.5) * 2) / 2
        over_under = round(rng.uniform(38, 52) * 2) / 2
        idx = (i // 2) % len(kickoff_offsets_hours)
        g = Game(
            week_id=week.id, sport="nfl",
            home_team=f"{home.city} {home.name}", away_team=f"{away.city} {away.name}",
            home_team_id=home.id, away_team_id=away.id,
            favorite=favorite, spread=spread, over_under=over_under,
            kickoff=deadline + timedelta(hours=kickoff_offsets_hours[idx]),
        )
        db.session.add(g)
        games.append(g)

    pool = FICTIONAL_COLLEGE_TEAMS[:]
    rng.shuffle(pool)
    for i in range(0, len(pool), 2):
        home, away = pool[i], pool[i + 1]
        favorite = rng.choice(["home", "away", None])
        spread = 0 if favorite is None else round(rng.uniform(1, 20) * 2) / 2
        g = Game(
            week_id=week.id, sport="college",
            home_team=home, away_team=away,
            favorite=favorite, spread=spread,
            kickoff=deadline - timedelta(hours=1),
        )
        db.session.add(g)
        games.append(g)

    db.session.commit()
    games[-1].is_mnf = True
    db.session.commit()
    return week, games


def run():
    with app.app_context():
        season_year = app.config["CURRENT_SEASON"]
        rng = random.Random(42)
        wipe_current_season(season_year)
        week, games = make_week(season_year, rng)
        print(f"Created Week {week.number} ({season_year}), deadline {week.pick_deadline}")
        print(f"  {len(games)} games created, open for picking now.")
        entry_count = Entry.query.filter_by(season_year=season_year).count()
        print(f"  {entry_count} existing real entries can pick against this week.")


if __name__ == "__main__":
    run()
