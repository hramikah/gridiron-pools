"""Week 1 is over and it's Tuesday.

Twenty-five players, all three pools. Twenty turned their picks in, five
didn't. Every week-1 game has been played and scored, the missed-pick
penalties have been applied, and there is no week 2 yet -- which is where a
real Tuesday sits, two days before Thursday's lines drop.

The week is dated to the Saturday just gone, so every deadline and kickoff is
genuinely in the past no matter when this runs. That matters: the app reads
the wall clock, and a future-dated week leaves picks "still open" and hides
everyone's selections from the reports.

Wipes existing play data (weeks, games, entries, picks, misses) and the mock
players it created last time. Teams, Loser Pool point values and the admin
account survive. Local test databases only -- see testbed_guard.py.

    ./start-testbed.command      # once, to build the environment
    venv/bin/python seed_week1_tuesday.py
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import set_setting
from models import (Entry, Game, GridironMiss, LoserPoolPoints, Pick, Setting,
                    Team, User, Week, db, default_buyback_open)
from scoring import (enforce_dropdead_no_tie, process_missed_picks, score_game)
from testbed_guard import require_testbed_database

SEASON = 2026
POOLS = ("dropdead", "loser", "gridiron")

PICKERS = 20          # players who got their picks in
NO_SHOWS = 5          # players who didn't
GRIDIRON_PICKS = 5    # the normal weekly allowance
MOCK_PASSWORD = "test1234"

# Named so they're easy to tell apart in standings and the admin grid. The
# last five never submit anything.
NAMES = [
    "aaron", "bobby", "carla", "dion", "eddie",
    "frank", "gina", "hector", "iris", "jamal",
    "kelly", "louis", "maria", "nate", "omar",
    "paula", "quinn", "rosa", "steve", "tanya",
    # --- no-shows ---
    "ursula", "victor", "wendy", "xavier", "yolanda",
]
assert len(NAMES) == PICKERS + NO_SHOWS

COLLEGE_MATCHUPS = [
    ("Alabama Crimson Tide", "Georgia Bulldogs"),
    ("Ohio State Buckeyes", "Michigan Wolverines"),
    ("Texas Longhorns", "Oklahoma Sooners"),
    ("Notre Dame Fighting Irish", "USC Trojans"),
    ("Clemson Tigers", "Florida State Seminoles"),
    ("Penn State Nittany Lions", "Wisconsin Badgers"),
    ("LSU Tigers", "Auburn Tigers"),
    ("Oregon Ducks", "Washington Huskies"),
]


def last_saturday_noon(now):
    """Noon Eastern on the most recent Saturday strictly in the past."""
    # Monday is 0, Saturday is 5.
    days_since_saturday = (now.weekday() - 5) % 7
    saturday = (now - timedelta(days=days_since_saturday)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    if saturday >= now:
        saturday -= timedelta(days=7)
    return saturday


def wipe_play_data():
    """Everything that describes a season in progress. Accounts that aren't
    mocks, teams, point values and settings are left alone."""
    for model in (Pick, GridironMiss, Game, Entry, Week):
        model.query.delete(synchronize_session=False)
    User.query.filter(User.username.in_(NAMES)).delete(synchronize_session=False)
    db.session.commit()


def make_players():
    players = []
    for name in NAMES:
        user = User(username=name, email=f"{name}@example.com", max_teams=1)
        user.set_password(MOCK_PASSWORD)
        db.session.add(user)
        players.append(user)
    db.session.flush()
    return players


def make_weeks(deadline):
    weeks = {}
    for pool in POOLS:
        week = Week(
            season_year=SEASON,
            number=1,
            pool=pool,
            pick_deadline=deadline,
            is_preseason=False,
            # Whatever the printed rules say for this pool and week number --
            # Drop Dead weeks 1-4, Gridiron week 2. Hardcoding this closed
            # meant a legitimately eliminated Drop Dead entry was refused its
            # week-2 buy-back, because that check reads the flag on the week
            # the entry died in.
            buyback_open=default_buyback_open(pool, 1),
        )
        db.session.add(week)
        weeks[pool] = week
    db.session.flush()
    return weeks


def make_games(weeks, deadline, rng):
    """One full NFL slate plus a college card.

    The NFL matchups are mirrored into all three pools -- with the team
    foreign keys set, which is what Drop Dead and Loser score off. College is
    Gridiron-only and carries no team rows, matching the printed rules.
    """
    teams = Team.query.order_by(Team.id).all()
    assert len(teams) >= 32, "run seed.py first -- 32 NFL teams expected"
    shuffled = teams[:]
    rng.shuffle(shuffled)
    matchups = [(shuffled[i], shuffled[i + 1]) for i in range(0, 32, 2)]  # 16 games

    # Thursday night, the Sunday slate, then Monday night.
    kickoffs = (
        [deadline - timedelta(days=2, hours=-8, minutes=-15)]          # Thu 20:15
        + [deadline + timedelta(days=1, hours=1)] * 8                  # Sun 13:00
        + [deadline + timedelta(days=1, hours=4, minutes=25)] * 6      # Sun 16:25
        + [deadline + timedelta(days=2, hours=8, minutes=15)]          # Mon 20:15
    )

    nfl_games = []
    for idx, ((away, home), kickoff) in enumerate(zip(matchups, kickoffs)):
        is_mnf = idx == len(matchups) - 1
        favorite = rng.choice(["home", "away"])
        spread = rng.choice([1.0, 2.5, 3.0, 3.5, 4.5, 6.0, 6.5, 7.0, 9.5, 10.0])
        over_under = rng.choice([38.0, 41.5, 43.0, 44.5, 47.0, 48.5, 51.0])
        for pool in POOLS:
            game = Game(
                week_id=weeks[pool].id,
                pool=pool,
                sport="nfl",
                away_team=away.name,
                home_team=home.name,
                away_team_id=away.id,
                home_team_id=home.id,
                # Only Gridiron plays a line; the other two are straight up.
                favorite=favorite if pool == "gridiron" else None,
                spread=spread if pool == "gridiron" else None,
                over_under=over_under if pool == "gridiron" else None,
                kickoff=kickoff,
                is_mnf=is_mnf,
            )
            db.session.add(game)
            if pool == "gridiron":
                nfl_games.append(game)

    college_games = []
    for away_name, home_name in COLLEGE_MATCHUPS:
        game = Game(
            week_id=weeks["gridiron"].id,
            pool="gridiron",
            sport="college",
            away_team=away_name,
            home_team=home_name,
            favorite=rng.choice(["home", "away"]),
            spread=rng.choice([2.5, 3.0, 6.5, 7.0, 10.5, 14.0]),
            over_under=None,  # college carries no total, per the rules
            kickoff=deadline + timedelta(hours=3),  # Saturday afternoon
        )
        db.session.add(game)
        college_games.append(game)

    db.session.flush()
    return nfl_games, college_games


def play_the_games(rng):
    """Final scores for every game, generated the way football actually
    behaves rather than uniformly at random.

    The favorite's margin of victory is drawn around the spread with a
    standard deviation of about two touchdowns, which is roughly how NFL
    results scatter. That gives all three of the properties this scenario
    needs at once, without any of them being rigged: favorites win outright
    most of the time (so Drop Dead doesn't wipe out the field in week 1),
    the spread is covered about half the time, and an exact landing on a
    whole-number line produces a genuine push now and then.
    """
    MARGIN_SPREAD_SD = 13.0     # NFL results scatter about this much
    COLLEGE_TOTAL = 52.0        # college games run higher and carry no line

    for game in Game.query.filter_by(pool="gridiron").all():
        spread = game.spread or 0.0
        # Margin the favorite ends up winning by -- negative means they lost.
        margin = round(rng.gauss(spread, MARGIN_SPREAD_SD))
        mean_total = game.over_under if game.over_under is not None else COLLEGE_TOTAL
        total = max(6, round(rng.gauss(mean_total, 7.0)))

        # Derive the loser's score first and add the margin, so the margin
        # comes out exactly as drawn. Splitting the total and rounding would
        # shift it by a point whenever total + margin is odd -- which quietly
        # destroys every exact landing on a whole-number spread, i.e. every
        # push.
        dog_score = max(0, (total - margin) // 2)
        fav_score = max(0, dog_score + margin)
        if game.favorite == "away":
            away_score, home_score = fav_score, dog_score
        else:
            # 'home' favorite, and pick'em games (favorite is None) where the
            # home side just takes the positive margin.
            home_score, away_score = fav_score, dog_score

        game.home_score, game.away_score, game.is_final = home_score, away_score, True

    _guarantee_a_push(rng)

    # Copy each NFL result into the Drop Dead and Loser mirrors of that game,
    # matched on the team pair, so all three pools agree on what happened.
    gridiron_results = {
        (g.away_team_id, g.home_team_id): (g.away_score, g.home_score)
        for g in Game.query.filter_by(pool="gridiron", sport="nfl").all()
    }
    for game in Game.query.filter(Game.pool != "gridiron").all():
        key = (game.away_team_id, game.home_team_id)
        if key in gridiron_results:
            game.away_score, game.home_score = gridiron_results[key]
            game.is_final = True
    db.session.commit()


def _guarantee_a_push(rng):
    """Land one game exactly on its spread and one exactly on its total.

    Pushes are real -- a whole-number line hit on the number is neither a win
    nor a loss, and shows in the standings as a tie -- but they only turn up
    in about one game a week, so a random slate often produces none at all.
    A week with no ties in it can't exercise the tie column, the ranking
    logic, or the Most Ties Award data, so one of each is nudged into place.
    """
    def landed_on_spread(g):
        m = (g.home_score - g.away_score) if g.favorite != "away" else (g.away_score - g.home_score)
        return m == (g.spread or 0)

    games = Game.query.filter_by(pool="gridiron").all()

    if not any(landed_on_spread(g) for g in games):
        whole = [g for g in games if g.spread and g.spread == int(g.spread)]
        if whole:
            g = rng.choice(whole)
            margin = int(g.spread)
            # Keep the total roughly where it was; move the margin onto the
            # number. Derive the loser's score first so the margin is exact.
            total = g.home_score + g.away_score
            dog = max(0, (total - margin) // 2)
            fav = dog + margin
            if g.favorite == "away":
                g.away_score, g.home_score = fav, dog
            else:
                g.home_score, g.away_score = fav, dog

    if not any(g.over_under is not None and g.home_score + g.away_score == g.over_under
               for g in games):
        whole = [g for g in games
                 if g.over_under is not None and g.over_under == int(g.over_under)
                 and not landed_on_spread(g)]  # don't undo the spread push
        if whole:
            g = rng.choice(whole)
            total = int(g.over_under)
            # Preserve who won and by how much; scale the total onto the number.
            margin = g.home_score - g.away_score
            g.home_score = max(0, round((total + margin) / 2))
            g.away_score = max(0, total - g.home_score)


def submit_picks(players, weeks, nfl_games, college_games, rng):
    """The first PICKERS players turn in a full slate everywhere. The last
    NO_SHOWS submit nothing at all."""
    gridiron_pool = nfl_games + college_games

    # Real players don't pick at random. In Drop Dead they take a favorite to
    # win outright, and in the Loser Pool they take a heavy underdog to lose,
    # so a normal week 1 leaves most of the field alive. Picking uniformly
    # buries about half the pool in the first week, which makes for a poor
    # test of anything downstream.
    by_spread = sorted(nfl_games, key=lambda g: -(g.spread or 0))
    favorites, underdogs = [], []
    for g in by_spread:
        if g.favorite == "home":
            favorites.append(g.home_team_id)
            underdogs.append(g.away_team_id)
        else:
            favorites.append(g.away_team_id)
            underdogs.append(g.home_team_id)
    all_team_ids = favorites + underdogs
    HEAVY = 6  # how far down the board the obvious picks run

    def dropdead_choice():
        # Mostly one of the week's biggest favorites, occasionally a smaller
        # one, rarely something contrarian.
        roll = rng.random()
        if roll < 0.75:
            return rng.choice(favorites[:HEAVY])
        if roll < 0.95:
            return rng.choice(favorites)
        return rng.choice(all_team_ids)

    def loser_choice():
        roll = rng.random()
        if roll < 0.75:
            return rng.choice(underdogs[:HEAVY])
        if roll < 0.95:
            return rng.choice(underdogs)
        return rng.choice(all_team_ids)

    for player in players[:PICKERS]:
        entries = {e.pool: e for e in player.entries}

        # --- Gridiron: five selections, a mix of spreads and totals --------
        chosen = rng.sample(gridiron_pool, GRIDIRON_PICKS)
        for game in chosen:
            if game.over_under is not None and rng.random() < 0.3:
                market, side = "total", rng.choice(["over", "under"])
            else:
                market, side = "spread", rng.choice(["home", "away"])
            db.session.add(Pick(
                entry_id=entries["gridiron"].id, week_id=weeks["gridiron"].id,
                pool="gridiron", game_id=game.id, market=market, side=side,
                result="pending",
            ))

        # --- Drop Dead: one team to win outright --------------------------
        db.session.add(Pick(
            entry_id=entries["dropdead"].id, week_id=weeks["dropdead"].id,
            pool="dropdead", team_id=dropdead_choice(), result="pending",
        ))

        # --- Loser: one team to lose --------------------------------------
        db.session.add(Pick(
            entry_id=entries["loser"].id, week_id=weeks["loser"].id,
            pool="loser", team_id=loser_choice(), result="pending",
        ))

    db.session.commit()


def run():
    require_testbed_database(app, "seed_week1_tuesday.py")
    rng = random.Random(20260815)

    with app.app_context():
        wipe_play_data()

        if Team.query.count() < 32:
            raise SystemExit("Not enough teams -- run seed.py first.")
        if LoserPoolPoints.query.filter_by(season_year=SEASON).count() < 32:
            raise SystemExit("No Loser Pool point values -- run seed.py first.")

        deadline = last_saturday_noon(datetime.now())
        weeks = make_weeks(deadline)
        players = make_players()

        for player in players:
            for pool in POOLS:
                db.session.add(Entry(
                    user_id=player.id, pool=pool, season_year=SEASON,
                    label="Entry 1", paid=True,
                ))
        db.session.flush()

        nfl_games, college_games = make_games(weeks, deadline, rng)
        submit_picks(players, weeks, nfl_games, college_games, rng)
        play_the_games(rng)

        # Score everything, then apply the missed-pick consequences exactly
        # the way the site does once a deadline has passed.
        for game in Game.query.all():
            score_game(game)
        for pool in POOLS:
            week = weeks[pool]
            process_missed_picks(week)
            enforce_dropdead_no_tie(week)
            week.missed_processed = True
        db.session.commit()

        # Tuesday: week 1 is still the current week. Week 2's lines don't
        # publish until Thursday, so there is no week 2 to move on to.
        set_setting("active_week", "1")

        report(weeks, deadline)


def report(weeks, deadline):
    from scoring import standings_dropdead, standings_gridiron, standings_loser

    print()
    print("=" * 72)
    print(f"  Week 1 complete -- deadline was {deadline:%a %d %b %Y at %I:%M %p} Eastern")
    print(f"  {PICKERS} players submitted, {NO_SHOWS} did not")
    print("=" * 72)

    print(f"\n  GRIDIRON  ({Game.query.filter_by(pool='gridiron').count()} games)")
    print(f"  {'player':<12}{'W':>3}{'L':>4}{'T':>4}   note")
    for rank, entry, w, l, t in standings_gridiron(SEASON)[:8]:
        print(f"  {entry.user.username:<12}{w:>3}{l:>4}{t:>4}")
    missed = GridironMiss.query.count()
    print(f"  ... {missed} entries recorded a missed week (scored 0-5)")

    alive = Entry.query.filter_by(pool="dropdead", season_year=SEASON, is_active=True).count()
    out = Entry.query.filter_by(pool="dropdead", season_year=SEASON, is_active=False).count()
    print(f"\n  DROP DEAD  {alive} alive, {out} eliminated")

    print("\n  LOSER POOL  top of the table")
    for rank, (entry, total) in [(r[0], (r[1], r[2])) for r in standings_loser(SEASON)[:5]]:
        print(f"  {rank:>3}. {entry.user.username:<12}{total:>6}")
    auto = Pick.query.filter_by(pool="loser").count()
    print(f"  ... {auto} loser picks on record ({NO_SHOWS} auto-assigned the MNF team)")

    print(f"\n  Log in as any player: {MOCK_PASSWORD}")
    print(f"  No-shows: {', '.join(NAMES[PICKERS:])}")
    print(f"  Admin: admin / changeme123")
    print()


if __name__ == "__main__":
    run()
