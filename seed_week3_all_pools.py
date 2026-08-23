"""All three pools, 150 players, frozen on the Tuesday after week 3.

All three weeks are played, finalised and scored everywhere. Monday night is
in, so nothing is outstanding: every pick has a result, the Loser Pool's
no-show auto-picks included. There is no week 4 yet, which is where a real
Tuesday sits -- two days before Thursday's lines drop.

Some games are built to land exactly on the number so that Gridiron produces
ties: a push needs the margin to equal the spread, or the two scores to add
up to the total, and a slate of half-point lines can never do either.

Every player is in all three pools, so the same 150 names run down the Drop
Dead, Loser and Gridiron tables and the Master Standings has something to say
in each tab.

Drop Dead runs properly: one team a week to win outright, each team usable
once all season, a losing pick or a no-show ends you. **When a buy-back is
available, 40% of the entries take it** -- which is only the ones eliminated
by a losing pick, since the printed rules deny a buy-back to anyone who
simply failed to submit.

The admin account plays all three too, and its Gridiron entry sat out week 2,
so logging in as admin lands on a week-3 makeup week: 8 picks plus the 2
automatic penalty losses.

Every mock player's password is `test1234`.

Local test databases only -- see testbed_guard.py.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import TESTBED_CLOCK_SETTING, set_setting
from models import Entry, Game, GridironMiss, LoserPoolPoints, Pick, Team, User, Week, db
from scoring import (
    GRIDIRON_BUYBACK_WEEK,
    dropdead_eliminated_for_no_pick,
    enforce_dropdead_no_tie,
    process_missed_picks,
    score_game,
)
from testbed_guard import require_testbed_database

SEASON = 2026
PLAYER_COUNT = 150
CURRENT_WEEK = 3
# Tuesday: Monday night is over, so every game in the week has a result.
GAMES_PLAYED_SO_FAR = 16
# Days past the week's Thursday, and the hour, that the clock is frozen at.
NOW_OFFSET = timedelta(days=5, hours=-6)  # the Tuesday, mid-morning
MOCK_PASSWORD = "test1234"

# What the request asked for: when a Drop Dead buy-back is on the table, this
# many entries take it.
DROPDEAD_BUYBACK_RATE = 0.40
# How often the Gridiron $100 was taken in week 2, for variety in the tables.
GRIDIRON_BUYBACK_COUNT = 15

# Fixed, so a re-run reproduces the same pool and a bug stays discussable.
RNG = random.Random(20260822)

FIRST_NAMES = [
    "james", "mary", "robert", "patricia", "john", "jennifer", "michael",
    "linda", "david", "elizabeth", "william", "barbara", "richard", "susan",
    "joseph", "jessica", "thomas", "sarah", "chris", "karen", "daniel",
    "nancy", "matt", "lisa", "tony", "betty", "mark", "margaret", "don",
    "sandra", "steve", "ashley", "paul", "kim", "andrew", "emily", "josh",
    "donna", "ken", "michelle", "kevin", "carol", "brian", "amanda", "george",
    "dorothy", "tim", "melissa", "ron", "deborah", "eddie", "stephanie",
    "jason", "rebecca", "jeff", "laura", "ryan", "sharon", "jacob", "cynthia",
    "gary", "kathleen", "nick", "amy", "eric", "angela", "stephen", "shirley",
    "jon", "anna", "larry", "ruth", "justin", "brenda", "scott", "pam",
    "brandon", "nicole", "ben", "katherine", "sam", "virginia", "greg",
    "catherine", "alex", "christine", "pat", "samantha", "jack", "debra",
    "dennis", "janet", "jerry", "rachel", "tyler", "carolyn", "aaron", "marie",
]
LAST_INITIALS = list("abcdefghijklmnopqrstuvwxyz")

POOLS = ("gridiron", "dropdead", "loser")

# Kickoff offsets from a week's Thursday, in hours: Thursday night, the Sunday
# slate, then Monday night last so the Loser Pool has an MNF game to fall back
# on for no-shows.
KICKOFF_HOURS = (
    [4 + 20.25]
    + [3 * 24 + 13.0] * 9
    + [3 * 24 + 16.42] * 4
    + [3 * 24 + 20.33, 4 * 24 + 20.25]
)
FAVOURITE_WINS = 0.68      # how often the favourite covers the moneyline
PICK_THE_FAVOURITE = 0.85  # how often a Drop Dead entry takes the favourite


def week_thursday(reference):
    """The Thursday of the week `reference` falls in (Monday-based)."""
    if reference.weekday() >= 3:
        return reference - timedelta(days=reference.weekday() - 3)
    return reference - timedelta(days=reference.weekday() + 4)


def wipe():
    """Everything but the teams, their Loser Pool point values, and any real
    account. Mock players are recognised by their email domain."""
    Pick.query.delete(synchronize_session=False)
    GridironMiss.query.delete(synchronize_session=False)
    Entry.query.delete(synchronize_session=False)
    Game.query.delete(synchronize_session=False)
    Week.query.delete(synchronize_session=False)
    User.query.filter(User.email.like("%@mockpool.test")).delete(
        synchronize_session=False
    )
    db.session.commit()


def build_slate(teams):
    """Pair all 32 clubs into 16 games, with a line and a result decided up
    front so the three pools agree about who won."""
    shuffled = teams[:]
    RNG.shuffle(shuffled)
    pairs = [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled) - 1, 2)]

    # Ties in Gridiron are pushes, and a push needs the result to land exactly
    # on the number. Half-point spreads and totals make that impossible, so a
    # slate of nothing but .5 lines produces a season with no ties in it at
    # all. These games are built to land on the number: the spread ones are a
    # whole number with the margin to match, the total ones an integer the two
    # scores add up to.
    push_spread = set(RNG.sample(range(len(pairs)), 3))
    push_total = set(RNG.sample(range(len(pairs)), 3))

    slate = []
    for index, (away, home) in enumerate(pairs):
        favourite = RNG.choice(["home", "away"])
        fav_wins = RNG.random() < FAVOURITE_WINS
        winner = favourite if fav_wins else ("away" if favourite == "home" else "home")
        loser_score = RNG.choice([10, 13, 14, 17, 20, 21, 23, 24])

        if index in push_spread:
            # The favourite wins by exactly the number: every spread pick on
            # this game is a push, whichever side it took.
            spread = float(RNG.choice([3, 4, 6, 7, 10]))
            winner = favourite
            margin = int(spread)
        else:
            spread = RNG.choice([1.5, 2.5, 3.5, 4.5, 6.5, 7.5, 9.5])
            margin = RNG.choice([1, 3, 3, 4, 6, 7, 7, 10, 13, 17])

        home_score = loser_score + margin if winner == "home" else loser_score
        away_score = loser_score + margin if winner == "away" else loser_score

        if index in push_total:
            total = float(home_score + away_score)
        else:
            total = RNG.choice([38.5, 40.5, 41.5, 43.5, 44.5, 45.5, 47.5, 49.5])

        slate.append({
            "away": away, "home": home, "favourite": favourite, "spread": spread,
            "total": total, "away_score": away_score, "home_score": home_score,
        })
    return slate


def build_week(number, slate, thursday, final_through):
    """One week in each of the three pools, off the same slate of games.

    That mirrors the Week Manager, where a game added once populates all
    three pools. Drop Dead and the Loser Pool need the team foreign keys to
    score at all; Gridiron scores off the line.

    `final_through` is how many games have a result yet, in kickoff order --
    all of them for a finished week, or everything up to Sunday afternoon for
    the week in progress.
    """
    deadline = datetime.combine(
        (thursday + timedelta(days=2)).date(), datetime.min.time()
    ) + timedelta(hours=12)
    midnight = thursday.replace(hour=0, minute=0, second=0, microsecond=0)

    weeks, games = {}, {}
    for pool in POOLS:
        week = Week(
            season_year=SEASON, number=number, pool=pool,
            pick_deadline=deadline,
            # Gridiron's $100 is week 2 only; Drop Dead's $30 covers weeks 1-4.
            buyback_open=(number == GRIDIRON_BUYBACK_WEEK) if pool == "gridiron" else number <= 4,
            missed_processed=True,
        )
        db.session.add(week)
        db.session.flush()
        weeks[pool] = week

        pool_games = []
        for index, (entry, hours) in enumerate(zip(slate, KICKOFF_HOURS)):
            game = Game(
                week_id=week.id, pool=pool, sport="nfl",
                away_team=entry["away"].name, home_team=entry["home"].name,
                away_team_id=entry["away"].id, home_team_id=entry["home"].id,
                kickoff=midnight + timedelta(hours=hours),
                is_mnf=hours > 4 * 24,
            )
            if pool == "gridiron":
                game.favorite = entry["favourite"]
                game.spread = entry["spread"]
                game.over_under = entry["total"]
            if index < final_through:
                game.away_score = entry["away_score"]
                game.home_score = entry["home_score"]
                game.is_final = True
            db.session.add(game)
            pool_games.append(game)
        db.session.flush()
        games[pool] = pool_games
    return weeks, games


def make_players():
    names, used = [], set()
    while len(names) < PLAYER_COUNT:
        candidate = f"{RNG.choice(FIRST_NAMES)}_{RNG.choice(LAST_INITIALS)}"
        if candidate not in used:
            used.add(candidate)
            names.append(candidate)

    # One hash for all of them: they share a throwaway password, and hashing
    # it 150 times properly costs half a minute for nothing.
    from werkzeug.security import generate_password_hash

    shared = generate_password_hash(MOCK_PASSWORD, method="pbkdf2:sha256")

    players = []
    for name in names:
        user = User(username=name, email=f"{name}@mockpool.test",
                    max_teams=1, password_hash=shared)
        db.session.add(user)
        db.session.flush()
        entries = {}
        for pool in POOLS:
            entry = Entry(user_id=user.id, pool=pool, season_year=SEASON,
                          label="Entry 1")
            db.session.add(entry)
            entries[pool] = entry
        players.append({"user": user, "entries": entries, "used_teams": set()})
    db.session.flush()
    return players


def gridiron_picks(entry, week, games, count):
    slots = []
    for game in games:
        slots.append((game, "spread", RNG.choice(["home", "away"])))
        slots.append((game, "total", RNG.choice(["over", "under"])))
    for game, market, side in RNG.sample(slots, min(count, len(slots))):
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="gridiron",
                            game_id=game.id, market=market, side=side))


def dropdead_pick(player, week, games):
    """One team to win outright, never reusing a team, usually the favourite."""
    entry = player["entries"]["dropdead"]
    options = []
    for game in games:
        for side in ("home", "away"):
            team_id = game.home_team_id if side == "home" else game.away_team_id
            if team_id not in player["used_teams"]:
                options.append((game, side, team_id))
    if not options:
        return
    # Mostly take the side the Gridiron line favours, which is how a real
    # entrant plays a survivor pool.
    favourites = [o for o in options if o[0].favourite_side == o[1]]
    if favourites and RNG.random() < PICK_THE_FAVOURITE:
        game, side, team_id = RNG.choice(favourites)
    else:
        game, side, team_id = RNG.choice(options)
    player["used_teams"].add(team_id)
    db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead",
                        team_id=team_id))


def loser_pick(entry, week, games):
    """One team you think will lose. Teams are reusable in this pool."""
    game = RNG.choice(games)
    side = RNG.choice(["home", "away"])
    team_id = game.home_team_id if side == "home" else game.away_team_id
    db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="loser",
                        team_id=team_id))


def take_dropdead_buybacks(week_number, players):
    """40% of the entries entitled to a buy-back take it.

    Entitled means eliminated in this week by a losing pick: the printed
    rules deny a buy-back to anyone eliminated for not turning a pick in, and
    891743a made the site enforce that. Writes exactly what the route writes
    -- is_active back on, buy_backs_used up, buyback_week set to the week they
    died in, eliminated_week deliberately left alone, which is how the
    standings tell a revived entry from a live one.
    """
    offered = []
    for player in players:
        entry = player["entries"]["dropdead"]
        if entry.is_active or entry.eliminated_week != week_number:
            continue
        if dropdead_eliminated_for_no_pick(entry):
            continue
        offered.append(entry)

    # Exactly 40%, drawn at random, rather than a coin weighted at 40% -- on
    # a few dozen eliminations a weighted coin drifts far enough to make the
    # fixture hard to reason about.
    wanted = round(DROPDEAD_BUYBACK_RATE * len(offered))
    for entry in RNG.sample(offered, wanted):
        entry.is_active = True
        entry.buy_backs_used = (entry.buy_backs_used or 0) + 1
        entry.buyback_week = entry.eliminated_week
    db.session.commit()
    return len(offered), wanted


def main():
    with app.app_context():
        uri = require_testbed_database(app, "seed_week3_all_pools.py")
        print(f"Seeding {uri}")
        wipe()

        teams = Team.query.order_by(Team.name).all()
        if len(teams) < 32:
            raise SystemExit("Run seed.py first -- the 32 NFL teams are missing.")

        thursday_3 = week_thursday(datetime.now()).replace(
            hour=16, minute=0, second=0, microsecond=0
        )
        thursdays = {
            1: thursday_3 - timedelta(days=14),
            2: thursday_3 - timedelta(days=7),
            3: thursday_3,
        }
        # The Tuesday after week 3: the week is done and week 4 has not been
        # published, which is exactly where a real Tuesday sits.
        now = thursday_3 + NOW_OFFSET
        set_setting(TESTBED_CLOCK_SETTING, now.isoformat())
        print(f"Clock frozen at {now:%A %d %B %Y, %I:%M %p} Eastern")

        slates = {n: build_slate(teams) for n in (1, 2, 3)}
        weeks, games = {}, {}
        for n in (1, 2, 3):
            # Everything is played in weeks 1 and 2. In week 3 the Thursday
            # game and both Sunday windows are in; the Sunday and Monday night
            # games are not.
            final_through = len(KICKOFF_HOURS) if n < CURRENT_WEEK else GAMES_PLAYED_SO_FAR
            weeks[n], games[n] = build_week(n, slates[n], thursdays[n], final_through)
        db.session.commit()

        players = make_players()
        admin = User.query.filter_by(username="admin").first()
        admin_player = None
        if admin:
            entries = {}
            for pool in POOLS:
                entry = Entry(user_id=admin.id, pool=pool, season_year=SEASON,
                              label="Entry 1")
                db.session.add(entry)
                entries[pool] = entry
            db.session.flush()
            admin_player = {"user": admin, "entries": entries, "used_teams": set()}
            players.append(admin_player)
        db.session.commit()

        # Which Gridiron line favoured which side, so Drop Dead picks can lean
        # on it the way a real entrant would.
        for n in (1, 2, 3):
            fav_by_matchup = {
                (g.away_team, g.home_team): g.favorite for g in games[n]["gridiron"]
            }
            for pool in ("dropdead", "loser"):
                for game in games[n][pool]:
                    game.favourite_side = fav_by_matchup.get(
                        (game.away_team, game.home_team)
                    )

        for n in (1, 2, 3):
            gridiron_week = weeks[n]["gridiron"]
            for player in players:
                # --- Gridiron ---------------------------------------------
                entry = player["entries"]["gridiron"]
                # The admin entry sits week 2 out, so week 3 is its makeup.
                if admin_player is not None and player is admin_player and n == 2:
                    pass
                else:
                    roll = RNG.random()
                    if roll < 0.72:
                        gridiron_picks(entry, gridiron_week, games[n]["gridiron"], 5)
                    elif roll < 0.88:
                        gridiron_picks(entry, gridiron_week, games[n]["gridiron"],
                                       RNG.randint(1, 4))
                    # the rest submit nothing

                # --- Drop Dead --------------------------------------------
                dd_entry = player["entries"]["dropdead"]
                if dd_entry.is_active and RNG.random() < 0.97:  # a few forget
                    dropdead_pick(player, weeks[n]["dropdead"],
                                  games[n]["dropdead"])

                # --- Loser Pool -------------------------------------------
                if RNG.random() < 0.95:
                    loser_pick(player["entries"]["loser"],
                               weeks[n]["loser"], games[n]["loser"])
            db.session.commit()

            for pool in POOLS:
                for game in games[n][pool]:
                    if game.is_final:
                        score_game(game)
                # The deadline has passed in every week here, including the
                # one in progress, so the no-shows are dealt with in all of
                # them: Drop Dead eliminates, the Loser Pool auto-assigns the
                # MNF visitor, Gridiron records the miss.
                process_missed_picks(weeks[n][pool])
            enforce_dropdead_no_tie(weeks[n]["dropdead"])

            if n < CURRENT_WEEK:
                # A buy-back is only offered in the week after the
                # elimination, so week 3's casualties have nothing to take
                # yet -- theirs opens when week 4 does.
                offered, taken = take_dropdead_buybacks(n, players)
                print(f"  week {n}: Drop Dead buy-back offered to {offered}, "
                      f"taken by {taken}")

        # --- the Gridiron $100, taken during week 2 -----------------------
        eligible = [
            p for p in players
            if not GridironMiss.query.filter_by(
                entry_id=p["entries"]["gridiron"].id
            ).count()
        ]
        for player in RNG.sample(eligible, min(GRIDIRON_BUYBACK_COUNT, len(eligible))):
            entry = player["entries"]["gridiron"]
            entry.buyback_week = GRIDIRON_BUYBACK_WEEK - 1
            entry.buy_backs_used = (entry.buy_backs_used or 0) + 1
        db.session.commit()

        # --- what we ended up with ----------------------------------------
        from scoring import standings_dropdead, standings_gridiron, standings_loser

        alive = Entry.query.filter_by(pool="dropdead", season_year=SEASON,
                                      is_active=True).count()
        total = Entry.query.filter_by(pool="dropdead", season_year=SEASON).count()
        revived = Entry.query.filter(Entry.pool == "dropdead",
                                     Entry.buy_backs_used > 0).count()
        print(f"\n{len(players)} players, all three pools, week {CURRENT_WEEK} open.")
        print(f"  Drop Dead: {alive} of {total} alive, {revived} bought back")
        print("  Gridiron top of the table:")
        for rank, entry, w, l, t in standings_gridiron(SEASON)[:3]:
            print(f"    {rank}. {entry.user.username:<16} {w}-{l}-{t}")
        print("  Loser Pool top of the table:")
        for rank, entry, points in standings_loser(SEASON)[:3]:
            print(f"    {rank}. {entry.user.username:<16} {points}")
        print(f"\n  Log in as admin, or any player with password {MOCK_PASSWORD!r}.")


if __name__ == "__main__":
    main()
