"""A full week-3 Tuesday, 150 players, for the live site to show off.

Same shape as seed_week3_all_pools.py, with three differences that matter:

1. **Fourteen of the 150 are real people.** Their usernames, emails and admin
   flags are the ones from the pre-loss roster. Where an email runs more than
   one account, the first one listed is the primary and the whole group's
   ``max_teams`` is raised to the number of accounts on it -- which is how
   this app does multi-team: separate accounts sharing an email, capped by
   ``max(max_teams)`` across the siblings. They play exactly like the mocks:
   picks, misses, eliminations and buy-backs all simulated.

2. **Buy-backs run at 25%** in both Drop Dead and Gridiron, of the entries
   actually entitled to one.

3. **The Gridiron $100 is taken before week 2's picks are made**, not after.
   Buying back grants a 10-pick catch-up slate for week 2; deciding it
   afterwards leaves the entry with 5 picks against a 10-slot allowance and
   the site charges the 5 empty slots as losses. Order is the fix.

The clock is frozen at **Tuesday 4:00 PM** of week 3 -- Monday night is over,
every game has a result, and week 4 has not been published. That is where a
real Tuesday sits.

Testbed databases only (``testbed_guard``). On the live box that means
pointing GRIDIRON_DATABASE_URI at a demo database whose path says testbed --
the real instance/pools.db is never opened.

    GRIDIRON_DATABASE_URI="sqlite:////Users/davidhramika/gridiron-pools/instance/testbed-demo.db" \
        venv/bin/python seed_live_demo_week3.py
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
    gridiron_pick_limit,
    process_missed_picks,
    score_game,
)
from testbed_guard import require_testbed_database

SEASON = 2026
PLAYER_COUNT = 150
CURRENT_WEEK = 3
PASSWORD = "Password"

# Both buy-backs, as a share of the entries actually entitled to one.
DROPDEAD_BUYBACK_RATE = 0.25
GRIDIRON_BUYBACK_RATE = 0.25

# Fixed, so a re-run reproduces the same pool and a bug stays discussable.
RNG = random.Random(20260823)

# (username, email, is_admin) -- order matters: the first account on an email
# is that group's primary.
REAL_ACCOUNTS = [
    ("Crusher",        "dhramika@gmail.com",             True),
    ("Foxglove",       "bitti302000@gmail.com",          False),
    ("GentlemanJack",  "awardsltd@icloud.com",           True),
    ("GentlemanJack2", "awardsltd@icloud.com",           False),
    ("GentlemanJack3", "awardsltd@icloud.com",           False),
    ("Pigfoot",        "fgh0@verizon.net",               True),
    ("Lord Abbott",    "fgh0@verizon.net",               False),
    ("Moneyline",      "adamwaynebroyles@yahoo.com",     False),
    ("TOPHAT",         "cedrichay@hotmail.com",          True),
    ("Pinball Wizard", "cedrichay@hotmail.com",          False),
    ("admin",          "hramikah@gmail.com",             True),
    ("admin2",         "hramikah@gmail.com",             False),
    ("Bofa",           "msdleif@icloud.com",             False),
    ("Bopper",         "bopper021@gmail.com",            False),
]

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
    """Every week, game, entry, pick and miss, plus the accounts this script
    owns. Teams and their Loser Pool point values survive."""
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
    # on the number. Half-point lines make that impossible, so a slate of
    # nothing but .5 lines produces a season with no ties in it at all. These
    # games are built to land on the number.
    push_spread = set(RNG.sample(range(len(pairs)), 3))
    push_total = set(RNG.sample(range(len(pairs)), 3))

    slate = []
    for index, (away, home) in enumerate(pairs):
        favourite = RNG.choice(["home", "away"])
        fav_wins = RNG.random() < FAVOURITE_WINS
        winner = favourite if fav_wins else ("away" if favourite == "home" else "home")
        loser_score = RNG.choice([10, 13, 14, 17, 20, 21, 23, 24])

        if index in push_spread:
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


def build_week(number, slate, thursday):
    """One week in each of the three pools, off the same slate of games --
    which mirrors the Week Manager, where a game added once populates all
    three. Every game is final: it is Tuesday, Monday night is over."""
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
        for entry, hours in zip(slate, KICKOFF_HOURS):
            game = Game(
                week_id=week.id, pool=pool, sport="nfl",
                away_team=entry["away"].name, home_team=entry["home"].name,
                away_team_id=entry["away"].id, home_team_id=entry["home"].id,
                kickoff=midnight + timedelta(hours=hours),
                is_mnf=hours > 4 * 24,
                away_score=entry["away_score"], home_score=entry["home_score"],
                is_final=True,
            )
            if pool == "gridiron":
                game.favorite = entry["favourite"]
                game.spread = entry["spread"]
                game.over_under = entry["total"]
            db.session.add(game)
            pool_games.append(game)
        db.session.flush()
        games[pool] = pool_games
    return weeks, games


def _entries_for(user):
    entries = {}
    for pool in POOLS:
        entry = Entry(user_id=user.id, pool=pool, season_year=SEASON,
                      label="Entry 1", paid=RNG.random() < 0.85)
        db.session.add(entry)
        entries[pool] = entry
    db.session.flush()
    return entries


def make_players():
    """The 14 real accounts plus enough mocks to reach PLAYER_COUNT.

    Real accounts are matched by username and updated in place if they already
    exist, so re-running never leaves a duplicate or a stranded old row.
    """
    from werkzeug.security import generate_password_hash

    # One hash for all of them: they share a password, and hashing it 150
    # times properly costs half a minute for nothing.
    shared = generate_password_hash(PASSWORD, method="pbkdf2:sha256")

    # Multi-account emails: the cap is max(max_teams) across the siblings, so
    # every account in a group carries the group's size.
    group_size = {}
    for _, email, _ in REAL_ACCOUNTS:
        group_size[email] = group_size.get(email, 0) + 1

    players, real_users = [], []
    for username, email, is_admin in REAL_ACCOUNTS:
        user = User.query.filter(
            db.func.lower(User.username) == username.lower()
        ).first()
        if user is None:
            user = User(username=username)
            db.session.add(user)
        user.email = email
        user.is_admin = is_admin
        user.max_teams = group_size[email]
        user.password_hash = shared
        db.session.flush()
        players.append({"user": user, "entries": _entries_for(user),
                        "used_teams": set()})
        real_users.append(user)

    taken = {u.username.lower() for u in User.query.all()}
    while len(players) < PLAYER_COUNT:
        candidate = f"{RNG.choice(FIRST_NAMES)}_{RNG.choice(LAST_INITIALS)}"
        if candidate in taken:
            continue
        taken.add(candidate)
        user = User(username=candidate, email=f"{candidate}@mockpool.test",
                    max_teams=1, password_hash=shared)
        db.session.add(user)
        db.session.flush()
        players.append({"user": user, "entries": _entries_for(user),
                        "used_teams": set()})

    db.session.commit()
    return players, real_users


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


def take_gridiron_buybacks(players):
    """The Gridiron $100, taken during week 2, by 25% of the field.

    Run *before* week 2's picks are written. The fee voids week 1 and grants a
    10-pick catch-up slate; deciding it afterwards would leave the entry
    holding 5 picks against a 10-slot allowance, and the site charges every
    empty slot as a loss.

    Writes what the route writes: buyback_week is the week voided (week 2
    minus 1), and buy_backs_used goes up. Week 1's rows stay in place and
    simply stop counting.
    """
    eligible = [p for p in players if p["entries"]["gridiron"].buyback_week is None]
    wanted = round(GRIDIRON_BUYBACK_RATE * len(eligible))
    bought = RNG.sample(eligible, wanted)
    for player in bought:
        entry = player["entries"]["gridiron"]
        entry.buyback_week = GRIDIRON_BUYBACK_WEEK - 1
        entry.buy_backs_used = (entry.buy_backs_used or 0) + 1
    db.session.commit()
    return len(eligible), len(bought)


def take_dropdead_buybacks(week_number, players):
    """25% of the entries entitled to a Drop Dead buy-back take it.

    Entitled means eliminated in this week by a losing pick: the printed rules
    deny a buy-back to anyone eliminated for not turning a pick in, and
    891743a made the site enforce that. Writes exactly what the route writes --
    is_active back on, buy_backs_used up, buyback_week set to the week they
    died in, eliminated_week deliberately left alone, which is how the
    standings tell a revived entry from one that never died.
    """
    offered = []
    for player in players:
        entry = player["entries"]["dropdead"]
        if entry.is_active or entry.eliminated_week != week_number:
            continue
        if dropdead_eliminated_for_no_pick(entry):
            continue
        offered.append(entry)

    # Exactly 25%, drawn at random, rather than a coin weighted at 25% -- on a
    # few dozen eliminations a weighted coin drifts far enough to make the
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
        uri = require_testbed_database(app, "seed_live_demo_week3.py")
        print(f"Seeding {uri}")
        wipe()

        teams = Team.query.order_by(Team.name).all()
        if len(teams) < 32:
            raise SystemExit("Run seed.py first -- the 32 NFL teams are missing.")
        if LoserPoolPoints.query.filter_by(season_year=SEASON).count() < 32:
            raise SystemExit("Run seed.py first -- the Loser Pool point values are missing.")

        thursday_3 = week_thursday(datetime.now()).replace(
            hour=16, minute=0, second=0, microsecond=0
        )
        thursdays = {
            1: thursday_3 - timedelta(days=14),
            2: thursday_3 - timedelta(days=7),
            3: thursday_3,
        }
        # Tuesday 4:00 PM: five days past that week's Thursday.
        now = thursday_3 + timedelta(days=5)
        set_setting(TESTBED_CLOCK_SETTING, now.isoformat())
        print(f"Clock frozen at {now:%A %d %B %Y, %I:%M %p} Eastern")

        weeks, games = {}, {}
        for n in (1, 2, 3):
            weeks[n], games[n] = build_week(n, build_slate(teams), thursdays[n])
        db.session.commit()

        players, real_users = make_players()

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
            if n == GRIDIRON_BUYBACK_WEEK:
                offered, taken = take_gridiron_buybacks(players)
                print(f"  week {n}: Gridiron buy-back offered to {offered}, "
                      f"taken by {taken}")

            gridiron_week = weeks[n]["gridiron"]
            for player in players:
                # --- Gridiron ---------------------------------------------
                entry = player["entries"]["gridiron"]
                # 5 normally; 8 in a makeup week after a first miss; 10 in the
                # week a buy-back was taken. Ask the site, don't assume.
                limit = gridiron_pick_limit(entry, gridiron_week)
                bought_in = entry.buyback_week is not None and n == entry.buyback_week + 1
                roll = RNG.random()
                if bought_in or roll < 0.72:
                    # An entry that just paid $100 turns up for its slate.
                    gridiron_picks(entry, gridiron_week, games[n]["gridiron"], limit)
                elif roll < 0.88:
                    gridiron_picks(entry, gridiron_week, games[n]["gridiron"],
                                   RNG.randint(1, max(1, limit - 1)))
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

            # The deadline has passed in every one of these weeks, so the
            # no-shows are dealt with in all of them -- Drop Dead eliminates,
            # the Loser Pool auto-assigns the MNF visitor, Gridiron records the
            # miss. Missed picks first, then results, or the auto-assigned
            # Loser picks are created after scoring and never get graded.
            for pool in POOLS:
                process_missed_picks(weeks[n][pool])
            for pool in POOLS:
                for game in games[n][pool]:
                    score_game(game)
            enforce_dropdead_no_tie(weeks[n]["dropdead"])

            if n < CURRENT_WEEK:
                # A buy-back is only offered in the week after the
                # elimination, so week 3's casualties have nothing to take
                # yet -- theirs opens when week 4 does.
                offered, taken = take_dropdead_buybacks(n, players)
                print(f"  week {n}: Drop Dead buy-back offered to {offered}, "
                      f"taken by {taken}")

        report(players, real_users)


def report(players, real_users):
    from scoring import standings_dropdead, standings_gridiron, standings_loser

    alive = Entry.query.filter_by(pool="dropdead", season_year=SEASON,
                                  is_active=True).count()
    total = Entry.query.filter_by(pool="dropdead", season_year=SEASON).count()
    revived = Entry.query.filter(Entry.pool == "dropdead",
                                 Entry.buy_backs_used > 0).count()
    gi_bought = Entry.query.filter(Entry.pool == "gridiron",
                                   Entry.buy_backs_used > 0).count()

    print(f"\n{len(players)} players, all three pools, week {CURRENT_WEEK} complete.")
    print(f"  Drop Dead: {alive} of {total} alive, {revived} bought back")
    print(f"  Gridiron:  {gi_bought} took the week-2 buy-back, "
          f"{GridironMiss.query.count()} missed weeks recorded")
    print("  Gridiron top of the table:")
    for rank, entry, w, l, t in standings_gridiron(SEASON)[:3]:
        print(f"    {rank}. {entry.user.username:<16} {w}-{l}-{t}")
    print("  Loser Pool top of the table:")
    for rank, entry, points in standings_loser(SEASON)[:3]:
        print(f"    {rank}. {entry.user.username:<16} {points}")

    print("\n  Real accounts (password 'Password'):")
    print(f"  {'username':<16}{'email':<30}{'role':<8}max accts")
    for user in real_users:
        print(f"  {user.username:<16}{user.email:<30}"
              f"{'admin' if user.is_admin else 'player':<8}{user.max_teams}")
    print()


if __name__ == "__main__":
    main()
