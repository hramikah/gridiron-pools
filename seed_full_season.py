"""Mock a full 18-week season with 120 players across all three pools, using
the real scoring/miss-penalty code paths (score_game, process_missed_picks)
so this doubles as an end-to-end correctness check at scale.

Wipes existing season data first (see the wipe step this was run alongside)
-- this script itself does NOT wipe; run the wipe separately, then this.

Safe to re-run against an already-wiped season.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import get_setting
from models import Entry, Game, Pick, Team, User, Week, db
from scoring import gridiron_pick_limit, process_missed_picks, score_game

PASSWORD = "test1234"
NUM_PLAYERS = 120
NUM_WEEKS = 18

MISS_CHANCE = 0.08  # per entry, per week, chance they don't submit a pick

FICTIONAL_COLLEGE_TEAMS = [
    "Central State Wildcats", "Lakeside Tech Hawks",
    "Riverside University Bears", "Highland College Eagles",
    "Coastal State Sharks", "Mountain View Bulldogs",
    "Prairie A&M Bison", "Northgate Falcons",
    "Summit Ridge Cougars", "Union City Miners",
    "Eastwood Crusaders", "Pinecrest Rattlers",
    "Cedar Valley Knights", "Redwood Rangers",
    "Silverton Storm", "Brookhaven Vikings",
]


def ensure_players(n):
    users = []
    for i in range(1, n + 1):
        name = str(i)
        user = User.query.filter_by(username=name).first()
        if not user:
            user = User(username=name, email=f"player{name}@example.com")
            user.set_password(PASSWORD)
            db.session.add(user)
        users.append(user)
    db.session.commit()
    return users


def make_week_games(week, rng):
    teams = Team.query.all()
    rng.shuffle(teams)
    games = []
    for i in range(0, len(teams), 2):
        home, away = teams[i], teams[i + 1]
        favorite = rng.choice(["home", "away", None])
        spread = 0 if favorite is None else round(rng.uniform(1, 13.5) * 2) / 2
        over_under = round(rng.uniform(38, 52) * 2) / 2
        g = Game(
            week_id=week.id, sport="nfl",
            home_team=f"{home.city} {home.name}", away_team=f"{away.city} {away.name}",
            home_team_id=home.id, away_team_id=away.id,
            favorite=favorite, spread=spread, over_under=over_under,
            kickoff=week.pick_deadline + timedelta(days=1),
        )
        db.session.add(g)
        games.append(g)

    pool = FICTIONAL_COLLEGE_TEAMS[:]
    rng.shuffle(pool)
    for i in range(0, min(len(pool), 8), 2):
        home, away = pool[i], pool[i + 1]
        favorite = rng.choice(["home", "away", None])
        spread = 0 if favorite is None else round(rng.uniform(1, 20) * 2) / 2
        g = Game(
            week_id=week.id, sport="college",
            home_team=home, away_team=away,
            favorite=favorite, spread=spread,
            kickoff=week.pick_deadline + timedelta(days=1),
        )
        db.session.add(g)
        games.append(g)

    db.session.commit()
    games[-1].is_mnf = True
    db.session.commit()
    return games


def run():
    with app.app_context():
        season_year = app.config["CURRENT_SEASON"]
        rng = random.Random(120 * 18)

        season_start = datetime.fromisoformat(get_setting("season_start_thursday")).date()

        users = ensure_players(NUM_PLAYERS)

        entries_by_pool = {"dropdead": [], "loser": [], "gridiron": []}
        for user in users:
            for pool in entries_by_pool:
                entry = Entry(user_id=user.id, pool=pool, season_year=season_year, label="Entry 1")
                db.session.add(entry)
                entries_by_pool[pool].append(entry)
        db.session.commit()

        used_teams_by_entry = {e.id: set() for e in entries_by_pool["dropdead"]}

        for wn in range(1, NUM_WEEKS + 1):
            week_thursday = season_start + timedelta(weeks=wn - 1)
            deadline = datetime.combine(week_thursday + timedelta(days=2), datetime.min.time()) + timedelta(hours=12)
            week = Week(season_year=season_year, number=wn, pick_deadline=deadline)
            db.session.add(week)
            db.session.commit()

            games = make_week_games(week, rng)
            nfl_games = [g for g in games if g.sport == "nfl"]

            # Drop Dead picks
            for entry in entries_by_pool["dropdead"]:
                if not entry.is_active or rng.random() < MISS_CHANCE:
                    continue
                candidates = [
                    t for g in nfl_games for t in (g.home_team_id, g.away_team_id)
                    if t not in used_teams_by_entry[entry.id]
                ]
                if not candidates:
                    continue
                team_id = rng.choice(candidates)
                used_teams_by_entry[entry.id].add(team_id)
                db.session.add(
                    Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=team_id)
                )

            # Loser Pool picks
            for entry in entries_by_pool["loser"]:
                if rng.random() < MISS_CHANCE:
                    continue
                team_id = rng.choice([g.home_team_id for g in nfl_games] + [g.away_team_id for g in nfl_games])
                db.session.add(
                    Pick(entry_id=entry.id, week_id=week.id, pool="loser", team_id=team_id)
                )

            # Gridiron picks
            for entry in entries_by_pool["gridiron"]:
                if not entry.is_active or rng.random() < MISS_CHANCE:
                    continue
                limit = gridiron_pick_limit(entry, week)
                n_spread = min(limit, len(games))
                chosen_games = rng.sample(games, k=n_spread)
                picks_made = 0
                for g in chosen_games:
                    if picks_made >= limit:
                        break
                    side = rng.choice(["home", "away"])
                    db.session.add(
                        Pick(
                            entry_id=entry.id, week_id=week.id, pool="gridiron",
                            game_id=g.id, market="spread", side=side,
                        )
                    )
                    picks_made += 1
                ou_candidates = [g for g in nfl_games if g.over_under and g not in chosen_games]
                if ou_candidates and picks_made < limit:
                    g = rng.choice(ou_candidates)
                    side = rng.choice(["over", "under"])
                    db.session.add(
                        Pick(
                            entry_id=entry.id, week_id=week.id, pool="gridiron",
                            game_id=g.id, market="total", side=side,
                        )
                    )
            db.session.commit()

            # Finalize + score
            for g in games:
                g.home_score = rng.randint(6, 38)
                g.away_score = rng.randint(6, 38)
                g.is_final = True
            db.session.commit()
            for g in games:
                score_game(g)

            # Handle no-shows the same way admin would
            process_missed_picks(week)

            alive = sum(1 for e in entries_by_pool["dropdead"] if e.is_active)
            print(f"Week {wn:>2}: {len(games)} games scored, {alive} Drop Dead entries still alive")

        print()
        print(f"Done: {NUM_PLAYERS} players, {NUM_WEEKS} weeks, {sum(len(v) for v in entries_by_pool.values())} entries.")


if __name__ == "__main__":
    run()
