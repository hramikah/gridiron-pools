"""One-off test-data generator: 4 test users, entries in all three pools,
4 mock weeks of NFL + fictional college games, simulated picks, and
finalized results -- so the admin can review standings, coloring, and the
weekly PDF reports without waiting for the real season.

Safe to re-run: it wipes any existing Week/Game/Entry/Pick data for the
current season before rebuilding (see helpers.wipe_pool_data below), and
leaves Team/User/Setting/LoserPoolPoints alone except for adding the 4 test
users if they don't already exist.

IMPORTANT: this is scratch/test data. Run this again (or ask Claude to
clear it) before the real season starts, so it doesn't collide with the
real Week 1 the Thursday auto-publish job will create.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import now_eastern
from models import Entry, Game, Pick, Team, User, Week, db
from scoring import score_game

TEST_PASSWORD = "test1234"
TEST_USERNAMES = ["1", "2", "3", "4"]

FICTIONAL_COLLEGE_TEAMS = [
    "Central State Wildcats", "Lakeside Tech Hawks",
    "Riverside University Bears", "Highland College Eagles",
    "Coastal State Sharks", "Mountain View Bulldogs",
    "Prairie A&M Bison", "Northgate Falcons",
]


def ensure_test_users():
    users = []
    for name in TEST_USERNAMES:
        user = User.query.filter_by(username=name).first()
        if not user:
            user = User(username=name, email=f"testuser{name}@example.com")
            user.set_password(TEST_PASSWORD)
            db.session.add(user)
        else:
            user.set_password(TEST_PASSWORD)
        users.append(user)
    db.session.commit()
    return users


def wipe_test_weeks(season_year, week_numbers):
    weeks = Week.query.filter(Week.season_year == season_year, Week.number.in_(week_numbers)).all()
    for w in weeks:
        db.session.delete(w)  # cascades to games and picks
    db.session.commit()


def wipe_test_entries(season_year, user_ids):
    Entry.query.filter(Entry.season_year == season_year, Entry.user_id.in_(user_ids)).delete(
        synchronize_session=False
    )
    db.session.commit()


def make_week_games(week, rng):
    teams = Team.query.all()
    rng.shuffle(teams)
    games = []
    for i in range(0, len(teams), 2):
        home, away = teams[i], teams[i + 1]
        favorite = rng.choice(["home", "away", None])
        spread = 0 if favorite is None else round(rng.uniform(1, 13.5) * 2) / 2
        over_under = round(rng.uniform(38, 52) * 2) / 2
        game = Game(
            week_id=week.id,
            sport="nfl",
            home_team=f"{home.city} {home.name}",
            away_team=f"{away.city} {away.name}",
            home_team_id=home.id,
            away_team_id=away.id,
            favorite=favorite,
            spread=spread,
            over_under=over_under,
            kickoff=week.pick_deadline + timedelta(days=1),
        )
        db.session.add(game)
        games.append(game)

    college_pool = FICTIONAL_COLLEGE_TEAMS[:]
    rng.shuffle(college_pool)
    for i in range(0, len(college_pool), 2):
        home, away = college_pool[i], college_pool[i + 1]
        favorite = rng.choice(["home", "away", None])
        spread = 0 if favorite is None else round(rng.uniform(1, 20) * 2) / 2
        game = Game(
            week_id=week.id,
            sport="college",
            home_team=home,
            away_team=away,
            favorite=favorite,
            spread=spread,
            kickoff=week.pick_deadline + timedelta(days=1),
        )
        db.session.add(game)
        games.append(game)

    db.session.commit()
    games[-1].is_mnf = True  # last NFL-ish game stands in for the MNF game
    db.session.commit()
    return games


def make_picks(entries_by_pool, week, games, rng, used_teams_by_entry):
    nfl_games = [g for g in games if g.sport == "nfl"]

    for entry in entries_by_pool["dropdead"]:
        if not entry.is_active:
            continue
        candidates = [t for g in nfl_games for t in (g.home_team_id, g.away_team_id) if t not in used_teams_by_entry[entry.id]]
        if not candidates:
            continue
        team_id = rng.choice(candidates)
        used_teams_by_entry[entry.id].add(team_id)
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=team_id))

    for entry in entries_by_pool["loser"]:
        team_id = rng.choice([g.home_team_id for g in nfl_games] + [g.away_team_id for g in nfl_games])
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="loser", team_id=team_id))

    for entry in entries_by_pool["gridiron"]:
        picks_made = 0
        chosen_games = rng.sample(games, k=min(4, len(games)))
        for g in chosen_games:
            side = rng.choice(["home", "away"])
            db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="gridiron", game_id=g.id, market="spread", side=side))
            picks_made += 1
        ou_candidates = [g for g in nfl_games if g.over_under and g not in chosen_games]
        if ou_candidates and picks_made < 5:
            g = rng.choice(ou_candidates)
            side = rng.choice(["over", "under"])
            db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="gridiron", game_id=g.id, market="total", side=side))

    db.session.commit()


def finalize_games(games, rng):
    for g in games:
        g.home_score = rng.randint(6, 38)
        g.away_score = rng.randint(6, 38)
        g.is_final = True
    db.session.commit()
    for g in games:
        score_game(g)


def run():
    with app.app_context():
        season_year = app.config["CURRENT_SEASON"]
        rng = random.Random(2026)

        users = ensure_test_users()
        user_ids = [u.id for u in users]

        week_numbers = [1, 2, 3, 4]
        wipe_test_weeks(season_year, week_numbers)
        wipe_test_entries(season_year, user_ids)

        entries_by_pool = {"dropdead": [], "loser": [], "gridiron": []}
        for user in users:
            for pool in entries_by_pool:
                entry = Entry(user_id=user.id, pool=pool, season_year=season_year, label="Entry 1")
                db.session.add(entry)
                entries_by_pool[pool].append(entry)
        db.session.commit()

        used_teams_by_entry = {e.id: set() for e in entries_by_pool["dropdead"]}

        now = now_eastern()
        for i, number in enumerate(week_numbers):
            deadline = now - timedelta(days=(len(week_numbers) - i) * 7 - 2)
            week = Week(season_year=season_year, number=number, pick_deadline=deadline)
            db.session.add(week)
            db.session.commit()

            games = make_week_games(week, rng)
            make_picks(entries_by_pool, week, games, rng, used_teams_by_entry)
            finalize_games(games, rng)

            print(f"Week {number}: {len(games)} games, deadline {deadline:%Y-%m-%d %H:%M}")

        print()
        print("Test users (all password: %s):" % TEST_PASSWORD)
        for name in TEST_USERNAMES:
            print(f"  username={name}")
        print()
        print("Drop Dead status after 4 weeks:")
        for entry in entries_by_pool["dropdead"]:
            status = "alive" if entry.is_active else f"eliminated wk{entry.eliminated_week}"
            print(f"  {entry.user.username}: {status}")


if __name__ == "__main__":
    run()
