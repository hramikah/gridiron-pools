"""Mock an 18-week season for 4 named test players (david, brittanny,
hunter, jay) across all three pools, using the real scoring code paths
(score_game) so this doubles as an end-to-end correctness check.

Rebuilds the whole season's Week/Game schedule from scratch, so any
existing picks (real or mock) tied to the old weeks are cleared as part of
that -- but real accounts' Entry rows (pool membership) are left alone and
never included in the simulated picks/misses, so they can't be eliminated
or benched by this mock run.

Safe to re-run.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import get_setting
from models import Entry, Game, GridironMiss, Pick, Team, User, Week, db
from scoring import GRIDIRON_BENCH_AFTER_MISSES, gridiron_pick_limit, score_game

PASSWORD = "test1234"
NAMES = ["david", "brittanny", "hunter", "jay"]
NUM_WEEKS = 18

MISS_CHANCE = 0.08  # per entry, per week, chance they don't submit a pick

FICTIONAL_COLLEGE_TEAMS = [
    "Central State Wildcats", "Lakeside Tech Hawks",
    "Riverside University Bears", "Highland College Eagles",
    "Coastal State Sharks", "Mountain View Bulldogs",
    "Prairie A&M Bison", "Northgate Falcons",
]


def wipe_season(season_year):
    """Clear every Week/Game/Pick/GridironMiss for the season, and any
    existing entries belonging to the named mock players. Real accounts'
    entries are left in place (just orphaned of picks, same as any other
    entry once the weeks are rebuilt)."""
    week_ids = [w.id for w in Week.query.filter_by(season_year=season_year).all()]
    if week_ids:
        Pick.query.filter(Pick.week_id.in_(week_ids)).delete(synchronize_session=False)
        GridironMiss.query.filter(GridironMiss.week_id.in_(week_ids)).delete(synchronize_session=False)
        Game.query.filter(Game.week_id.in_(week_ids)).delete(synchronize_session=False)
        Week.query.filter(Week.id.in_(week_ids)).delete(synchronize_session=False)

    mock_user_ids = [u.id for u in User.query.filter(User.username.in_(NAMES)).all()]
    if mock_user_ids:
        mock_entry_ids = [e.id for e in Entry.query.filter(Entry.user_id.in_(mock_user_ids)).all()]
        if mock_entry_ids:
            Entry.query.filter(Entry.id.in_(mock_entry_ids)).delete(synchronize_session=False)
    db.session.commit()


def ensure_players():
    users = []
    for name in NAMES:
        user = User.query.filter_by(username=name).first()
        if not user:
            user = User(username=name, email=f"{name}@example.com")
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


def process_missed_picks_for(week, entries_by_pool):
    """Same rules as scoring.process_missed_picks, but scoped only to the
    given mock entries -- real accounts are never touched."""
    picked_dropdead = {p.entry_id for p in Pick.query.filter_by(pool="dropdead", week_id=week.id).all()}
    for entry in entries_by_pool["dropdead"]:
        if entry.is_active and entry.id not in picked_dropdead:
            entry.is_active = False
            entry.eliminated_week = week.number

    mnf_game = Game.query.filter_by(week_id=week.id, is_mnf=True).first()
    if mnf_game and mnf_game.away_team_id:
        picked_loser = {p.entry_id for p in Pick.query.filter_by(pool="loser", week_id=week.id).all()}
        for entry in entries_by_pool["loser"]:
            if entry.id not in picked_loser:
                db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="loser", team_id=mnf_game.away_team_id))

    picked_gridiron = {p.entry_id for p in Pick.query.filter_by(pool="gridiron", week_id=week.id).all()}
    for entry in entries_by_pool["gridiron"]:
        if not entry.is_active or entry.id in picked_gridiron:
            continue
        if GridironMiss.query.filter_by(entry_id=entry.id, week_id=week.id).first():
            continue
        db.session.add(GridironMiss(entry_id=entry.id, week_id=week.id))
        db.session.flush()
        total_misses = GridironMiss.query.filter_by(entry_id=entry.id).count()
        if total_misses > GRIDIRON_BENCH_AFTER_MISSES:
            entry.is_active = False
            entry.eliminated_week = week.number

    db.session.commit()


def run():
    with app.app_context():
        season_year = app.config["CURRENT_SEASON"]
        rng = random.Random(len(NAMES) * NUM_WEEKS)

        wipe_season(season_year)

        season_start = datetime.fromisoformat(get_setting("season_start_thursday")).date()
        users = ensure_players()

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
                db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=team_id))

            for entry in entries_by_pool["loser"]:
                if rng.random() < MISS_CHANCE:
                    continue
                team_id = rng.choice([g.home_team_id for g in nfl_games] + [g.away_team_id for g in nfl_games])
                db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="loser", team_id=team_id))

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
                        Pick(entry_id=entry.id, week_id=week.id, pool="gridiron", game_id=g.id, market="spread", side=side)
                    )
                    picks_made += 1
                ou_candidates = [g for g in nfl_games if g.over_under and g not in chosen_games]
                if ou_candidates and picks_made < limit:
                    g = rng.choice(ou_candidates)
                    side = rng.choice(["over", "under"])
                    db.session.add(
                        Pick(entry_id=entry.id, week_id=week.id, pool="gridiron", game_id=g.id, market="total", side=side)
                    )
            db.session.commit()

            for g in games:
                g.home_score = rng.randint(6, 38)
                g.away_score = rng.randint(6, 38)
                g.is_final = True
            db.session.commit()
            for g in games:
                score_game(g)

            process_missed_picks_for(week, entries_by_pool)

            alive = sum(1 for e in entries_by_pool["dropdead"] if e.is_active)
            print(f"Week {wn:>2}: {len(games)} games scored, {alive}/{len(NAMES)} Drop Dead entries still alive")

        print()
        print(f"Done: {len(NAMES)} players ({', '.join(NAMES)}), {NUM_WEEKS} weeks, "
              f"{sum(len(v) for v in entries_by_pool.values())} entries. Password for all: {PASSWORD}")


if __name__ == "__main__":
    run()
