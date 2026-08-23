"""Week 3, Thursday 7:30 PM, 150 players, for the live site to show off.

Successor to seed_live_demo_week3.py, which froze the site on the *Tuesday*
after week 3 -- everything played, nothing to do. This one stops the clock in
the middle of a live pick window, which is the interesting moment:

* **Weeks 1 and 2 are played, scored and final.**
* **Week 3 is open.** No scores, no results, picks still being taken. The
  overall deadline is Saturday at noon.
* **It is Thursday, 7:30 PM.** Thursday night kicks off at 8:00, and games
  lock one hour before their own kickoff, so the TNF game is already shut
  while the other fifteen are still open. That is the state that shows a
  visitor what a locked game looks like next to a live one.
* **College games are on the board.** Eight a week, Gridiron only, spread but
  no over/under, kicking off Saturday afternoon -- the way
  publish_next_week.py builds a real week and the way the printed Gridiron
  rules describe it. The previous seeder built an NFL-only card, which made
  the demo's Gridiron board narrower than a real one.

The fourteen real accounts are all given the same deliberate story, so every
login Hunter hands out lands on the two situations worth demonstrating:

* **They missed week 2's Gridiron picks** -- their first miss of the season.
  Rule 8 grants one makeup week, so week 3 is 8 picks instead of 5, with the
  2-game penalty riding alongside: ten slots, eight fillable.
* **They lost their week 2 Drop Dead pick** and are out. The pick was made
  and lost (not a no-show), so the buy-back is on the table, and week 3 is
  the week it is offered in. The offer is live and untaken.

Neither is left half-done by an earlier week: all fourteen made their week 1
Gridiron picks and survived week 1 in Drop Dead, so week 2 really is the
first thing that went wrong for them. None of them took the Gridiron week-2
buy-back, which would have voided the miss and cancelled the makeup week.

The other 136 are mocks and behave the way they did in the previous seeder.

Testbed databases only (``testbed_guard``). On the live box that means
pointing GRIDIRON_DATABASE_URI at a demo database whose path says testbed --
the real instance/pools.db is never opened.

    GRIDIRON_DATABASE_URI="sqlite:////Users/davidhramika/gridiron-pools/instance/testbed-demo.db" \
        venv/bin/python3 seed_live_demo_thursday.py
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import (
    TESTBED_CLOCK_SETTING,
    deadline_passed,
    get_current_week,
    set_setting,
)
from models import Entry, Game, GridironMiss, LoserPoolPoints, Pick, Team, User, Week, db
from scoring import (
    GRIDIRON_BUYBACK_WEEK,
    dropdead_buyback_available,
    dropdead_eliminated_for_no_pick,
    enforce_dropdead_no_tie,
    gridiron_makeup_week,
    gridiron_penalty_slots,
    gridiron_pick_limit,
    process_missed_picks,
    score_game,
)
from testbed_guard import require_testbed_database

SEASON = 2026
PLAYER_COUNT = 150
CURRENT_WEEK = 3          # open: no results, picks still being taken
PLAYED_WEEKS = (1, 2)     # final and scored
PASSWORD = "Password"

# Both buy-backs, as a share of the entries actually entitled to one.
DROPDEAD_BUYBACK_RATE = 0.25
GRIDIRON_BUYBACK_RATE = 0.25

# How many buy-back fees the commissioners have actually collected. The
# payments page bills a buy-back the same way it bills an entry fee, so a
# fixture where every one of them is unpaid makes that page look broken
# rather than populated.
BUYBACK_PAID_RATE = 0.6

# How many of the mock field have already got their week 3 picks in by
# Thursday evening. The rest are still sitting on them, which is realistic
# two days out from a Saturday-noon deadline.
WEEK3_SUBMITTED_RATE = 0.45

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

# College is Gridiron-only and carries no Team rows -- the pool's teams are
# the 32 NFL clubs, and Drop Dead and the Loser Pool score off team ids. Names
# are stored as plain strings on the Game, which is exactly what the Week
# Manager does when an admin adds a college game by hand.
COLLEGE_TEAMS = [
    "Alabama Crimson Tide", "Georgia Bulldogs", "Ohio State Buckeyes",
    "Michigan Wolverines", "Texas Longhorns", "Oklahoma Sooners",
    "Notre Dame Fighting Irish", "USC Trojans", "Clemson Tigers",
    "Florida State Seminoles", "Penn State Nittany Lions", "Wisconsin Badgers",
    "LSU Tigers", "Auburn Tigers", "Oregon Ducks", "Washington Huskies",
    "Tennessee Volunteers", "Florida Gators", "Miami Hurricanes",
    "Texas A&M Aggies", "Ole Miss Rebels", "Utah Utes", "Iowa Hawkeyes",
    "Nebraska Cornhuskers", "Baylor Bears", "TCU Horned Frogs",
    "Michigan State Spartans", "Oklahoma State Cowboys", "Kansas State Wildcats",
    "North Carolina Tar Heels", "Louisville Cardinals", "Arizona State Sun Devils",
]
COLLEGE_GAMES_PER_WEEK = 8

# Kickoff offsets from a week's Thursday midnight, in hours.
#
# TNF is 20.0 exactly -- 8:00 PM -- because the frozen clock sits at 7:30 and
# games lock an hour before kickoff. That is the whole point of this fixture:
# at 7:30 the Thursday game is shut and everything else is open. Do not nudge
# it without moving the clock too.
TNF_HOUR = 20.0
KICKOFF_HOURS = (
    [TNF_HOUR]                      # Thu 8:00 PM  -- locked at the frozen clock
    + [3 * 24 + 13.0] * 9           # Sun 1:00 PM
    + [3 * 24 + 16.4167] * 4        # Sun 4:25 PM
    + [3 * 24 + 20.3333]            # Sun 8:20 PM
    + [4 * 24 + 20.25]              # Mon 8:15 PM  -- MNF
)
# Saturday afternoon and evening, after the Saturday-noon pick deadline.
COLLEGE_KICKOFF_HOURS = [2 * 24 + 15.5] * 4 + [2 * 24 + 19.0] * 4

FAVOURITE_WINS = 0.68      # how often the favourite wins outright
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


def _result(favourite, force_push=False, force_total_push=False):
    """A final score for one game, decided up front so every pool agrees.

    Ties in Gridiron are pushes, and a push needs the result to land exactly
    on the number. Half-point lines make that impossible, so a slate of
    nothing but .5 lines produces a season with no ties in it at all. The
    forced games are built to land on the number.
    """
    fav_wins = RNG.random() < FAVOURITE_WINS
    winner = favourite if fav_wins else ("away" if favourite == "home" else "home")
    loser_score = RNG.choice([10, 13, 14, 17, 20, 21, 23, 24])

    if force_push:
        spread = float(RNG.choice([3, 4, 6, 7, 10]))
        winner = favourite
        margin = int(spread)
    else:
        spread = RNG.choice([1.5, 2.5, 3.5, 4.5, 6.5, 7.5, 9.5])
        margin = RNG.choice([1, 3, 3, 4, 6, 7, 7, 10, 13, 17])

    home_score = loser_score + margin if winner == "home" else loser_score
    away_score = loser_score + margin if winner == "away" else loser_score

    if force_total_push:
        total = float(home_score + away_score)
    else:
        total = RNG.choice([38.5, 40.5, 41.5, 43.5, 44.5, 45.5, 47.5, 49.5])

    return spread, total, home_score, away_score


def build_nfl_slate(teams):
    """All 32 clubs paired into 16 games, with a line and a result."""
    shuffled = teams[:]
    RNG.shuffle(shuffled)
    pairs = [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled) - 1, 2)]

    push_spread = set(RNG.sample(range(len(pairs)), 3))
    push_total = set(RNG.sample(range(len(pairs)), 3))

    slate = []
    for index, (away, home) in enumerate(pairs):
        favourite = RNG.choice(["home", "away"])
        spread, total, home_score, away_score = _result(
            favourite, index in push_spread, index in push_total
        )
        slate.append({
            "away": away, "home": home, "favourite": favourite, "spread": spread,
            "total": total, "away_score": away_score, "home_score": home_score,
        })
    return slate


def build_college_slate():
    """Eight college games: names only, a spread, and no total.

    The printed rules put the over/under on NFL games only, so college games
    carry a line to pick against and nothing else. No Team rows, no team
    foreign keys -- college never reaches Drop Dead or the Loser Pool.
    """
    names = COLLEGE_TEAMS[:]
    RNG.shuffle(names)
    pairs = [
        (names[i * 2], names[i * 2 + 1]) for i in range(COLLEGE_GAMES_PER_WEEK)
    ]
    push_spread = set(RNG.sample(range(len(pairs)), 2))

    slate = []
    for index, (away, home) in enumerate(pairs):
        favourite = RNG.choice(["home", "away"])
        spread, _total, home_score, away_score = _result(
            favourite, index in push_spread, False
        )
        slate.append({
            "away": away, "home": home, "favourite": favourite, "spread": spread,
            "away_score": away_score, "home_score": home_score,
        })
    return slate


def build_week(number, nfl_slate, college_slate, thursday, played):
    """One week in each of the three pools off the same NFL slate -- which
    mirrors the Week Manager, where a game added once populates all three --
    plus a college card that only Gridiron sees.

    ``played`` is the difference between a week that is over and the week
    that is happening: a played week's games are final with scores on them,
    the open week's are scoreless and its no-shows have not been processed,
    because its deadline has not passed.
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
            missed_processed=played,
        )
        db.session.add(week)
        db.session.flush()
        weeks[pool] = week

        pool_games = []
        for entry, hours in zip(nfl_slate, KICKOFF_HOURS):
            game = Game(
                week_id=week.id, pool=pool, sport="nfl",
                away_team=entry["away"].name, home_team=entry["home"].name,
                away_team_id=entry["away"].id, home_team_id=entry["home"].id,
                kickoff=midnight + timedelta(hours=hours),
                is_mnf=hours > 4 * 24,
                away_score=entry["away_score"] if played else None,
                home_score=entry["home_score"] if played else None,
                is_final=played,
            )
            if pool == "gridiron":
                game.favorite = entry["favourite"]
                game.spread = entry["spread"]
                game.over_under = entry["total"]
            db.session.add(game)
            pool_games.append(game)

        # College rides along on the Gridiron week only.
        if pool == "gridiron":
            for entry, hours in zip(college_slate, COLLEGE_KICKOFF_HOURS):
                game = Game(
                    week_id=week.id, pool="gridiron", sport="college",
                    away_team=entry["away"], home_team=entry["home"],
                    away_team_id=None, home_team_id=None,
                    favorite=entry["favourite"], spread=entry["spread"],
                    over_under=None,  # college carries no total, per the rules
                    kickoff=midnight + timedelta(hours=hours),
                    is_mnf=False,
                    away_score=entry["away_score"] if played else None,
                    home_score=entry["home_score"] if played else None,
                    is_final=played,
                )
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

    players = []
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
                        "used_teams": set(), "real": True})

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
                        "used_teams": set(), "real": False})

    db.session.commit()
    return players


def gridiron_picks(entry, week, games, count):
    """`count` picks drawn from every market on the board.

    NFL games offer two slots each -- the spread and the total. College games
    offer the spread only, which is why the slate is built without an
    over/under on them.
    """
    slots = []
    for game in games:
        slots.append((game, "spread", RNG.choice(["home", "away"])))
        if game.over_under is not None:
            slots.append((game, "total", RNG.choice(["over", "under"])))
    for game, market, side in RNG.sample(slots, min(count, len(slots))):
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="gridiron",
                            game_id=game.id, market=market, side=side))


def _dropdead_options(player, games):
    options = []
    for game in games:
        for side in ("home", "away"):
            team_id = game.home_team_id if side == "home" else game.away_team_id
            if team_id and team_id not in player["used_teams"]:
                options.append((game, side, team_id))
    return options


def dropdead_pick(player, week, games):
    """One team to win outright, never reusing a team, usually the favourite."""
    entry = player["entries"]["dropdead"]
    options = _dropdead_options(player, games)
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


def dropdead_forced_pick(player, week, games, want_win):
    """A Drop Dead pick whose outcome is decided in advance.

    The fourteen real accounts need a specific story -- survive week 1, lose
    week 2 -- and the only way to guarantee it is to pick from the teams that
    are already known to win (or lose) this week. Falls back to a normal pick
    if nothing unused fits, which cannot happen on a 16-game slate this early
    in the season but is not worth crashing over.
    """
    entry = player["entries"]["dropdead"]
    wanted = []
    for game, side, team_id in _dropdead_options(player, games):
        if game.winner is None:
            continue
        won = game.winner == side
        if won == want_win:
            wanted.append((game, side, team_id))
    if not wanted:
        dropdead_pick(player, week, games)
        return
    game, side, team_id = RNG.choice(wanted)
    player["used_teams"].add(team_id)
    db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead",
                        team_id=team_id))


def loser_pick(entry, week, games):
    """One team you think will lose. Teams are reusable in this pool."""
    nfl = [g for g in games if g.home_team_id and g.away_team_id]
    game = RNG.choice(nfl)
    side = RNG.choice(["home", "away"])
    team_id = game.home_team_id if side == "home" else game.away_team_id
    db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="loser",
                        team_id=team_id))


def take_gridiron_buybacks(players):
    """The Gridiron $100, taken during week 2, by 25% of the mock field.

    Run *before* week 2's picks are written. The fee voids week 1 and grants a
    10-pick catch-up slate; deciding it afterwards would leave the entry
    holding 5 picks against a 10-slot allowance, and the site charges every
    empty slot as a loss.

    The fourteen real accounts are held out. A buy-back voids every week up to
    and including the one it erases, so it would swallow their week 2 miss and
    with it the makeup week that is the whole point of this fixture.
    """
    eligible = [
        p for p in players
        if not p["real"] and p["entries"]["gridiron"].buyback_week is None
    ]
    wanted = round(GRIDIRON_BUYBACK_RATE * len(eligible))
    bought = RNG.sample(eligible, wanted)
    for player in bought:
        entry = player["entries"]["gridiron"]
        entry.buyback_week = GRIDIRON_BUYBACK_WEEK - 1
        entry.buy_backs_used = (entry.buy_backs_used or 0) + 1
        if RNG.random() < BUYBACK_PAID_RATE:
            entry.buy_backs_paid = (entry.buy_backs_paid or 0) + 1
    db.session.commit()
    return len(eligible), len(bought)


def take_dropdead_buybacks(week_number, players, include_real):
    """25% of the entries entitled to a Drop Dead buy-back take it.

    Entitled means eliminated in this week by a losing pick: the printed rules
    deny a buy-back to anyone eliminated for not turning a pick in, and
    891743a made the site enforce that. Writes exactly what the route writes --
    is_active back on, buy_backs_used up, buyback_week set to the week they
    died in, eliminated_week deliberately left alone, which is how the
    standings tell a revived entry from one that never died.

    ``include_real`` is False for week 2, whose buy-back window is open right
    now: the fourteen real accounts have to arrive at the site with the offer
    still sitting there untaken.
    """
    offered = []
    for player in players:
        if player["real"] and not include_real:
            continue
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
        if RNG.random() < BUYBACK_PAID_RATE:
            entry.buy_backs_paid = (entry.buy_backs_paid or 0) + 1
    db.session.commit()
    return len(offered), wanted


def play_week(number, weeks, games, players):
    """One finished week: picks, no-shows, results."""
    if number == GRIDIRON_BUYBACK_WEEK:
        offered, taken = take_gridiron_buybacks(players)
        print(f"  week {number}: Gridiron buy-back offered to {offered}, "
              f"taken by {taken}")

    gridiron_week = weeks["gridiron"]
    for player in players:
        real = player["real"]

        # --- Gridiron -------------------------------------------------
        entry = player["entries"]["gridiron"]
        limit = gridiron_pick_limit(entry, gridiron_week)
        if real:
            # Week 1 they turn up; week 2 is the miss the whole fixture
            # is built around, so nothing is written for it at all.
            if number == 1:
                gridiron_picks(entry, gridiron_week, games["gridiron"], limit)
        else:
            bought_in = entry.buyback_week is not None and number == entry.buyback_week + 1
            roll = RNG.random()
            if bought_in or roll < 0.72:
                # An entry that just paid $100 turns up for its slate.
                gridiron_picks(entry, gridiron_week, games["gridiron"], limit)
            elif roll < 0.88:
                gridiron_picks(entry, gridiron_week, games["gridiron"],
                               RNG.randint(1, max(1, limit - 1)))
            # the rest submit nothing

        # --- Drop Dead ------------------------------------------------
        dd_entry = player["entries"]["dropdead"]
        if dd_entry.is_active:
            if real:
                # Survive week 1, lose week 2. A pick is written either way,
                # so the week 2 elimination is a loss and not a no-show --
                # the rules deny the buy-back to no-shows.
                dropdead_forced_pick(player, weeks["dropdead"],
                                     games["dropdead"], want_win=(number == 1))
            elif RNG.random() < 0.97:  # a few forget
                dropdead_pick(player, weeks["dropdead"], games["dropdead"])

        # --- Loser Pool -----------------------------------------------
        if real or RNG.random() < 0.95:
            loser_pick(player["entries"]["loser"], weeks["loser"],
                       games["loser"])
    db.session.commit()

    # The deadline has passed in this week, so the no-shows are dealt with --
    # Drop Dead eliminates, the Loser Pool auto-assigns the MNF visitor,
    # Gridiron records the miss. Missed picks first, then results, or the
    # auto-assigned Loser picks are created after scoring and never graded.
    for pool in POOLS:
        process_missed_picks(weeks[pool])
    for pool in POOLS:
        for game in games[pool]:
            score_game(game)
    enforce_dropdead_no_tie(weeks["dropdead"])


def open_week(weeks, games, players):
    """Week 3 as it stands on Thursday evening: picks are being taken.

    No process_missed_picks and no scoring -- the deadline is Saturday noon
    and not one game has kicked off yet. Roughly half the mock field is in
    already; the fourteen real accounts have submitted nothing, because
    submitting is what Hunter wants to demonstrate.
    """
    submitted = 0
    for player in players:
        if player["real"] or RNG.random() >= WEEK3_SUBMITTED_RATE:
            continue
        submitted += 1

        entry = player["entries"]["gridiron"]
        limit = gridiron_pick_limit(entry, weeks["gridiron"])
        gridiron_picks(entry, weeks["gridiron"], games["gridiron"], limit)

        dd_entry = player["entries"]["dropdead"]
        if dd_entry.is_active:
            dropdead_pick(player, weeks["dropdead"], games["dropdead"])

        loser_pick(player["entries"]["loser"], weeks["loser"], games["loser"])
    db.session.commit()
    return submitted


def main():
    with app.app_context():
        uri = require_testbed_database(app, "seed_live_demo_thursday.py")
        print(f"Seeding {uri}")
        wipe()

        teams = Team.query.order_by(Team.name).all()
        if len(teams) < 32:
            raise SystemExit("Run seed.py first -- the 32 NFL teams are missing.")
        if LoserPoolPoints.query.filter_by(season_year=SEASON).count() < 32:
            raise SystemExit("Run seed.py first -- the Loser Pool point values are missing.")

        thursday_3 = week_thursday(datetime.now()).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        thursdays = {
            1: thursday_3 - timedelta(days=14),
            2: thursday_3 - timedelta(days=7),
            3: thursday_3,
        }
        # Thursday, 7:30 PM: half an hour before the 8:00 kickoff, which puts
        # the Thursday game inside its one-hour lock and leaves the rest of
        # the week open.
        now = thursday_3 + timedelta(hours=19, minutes=30)
        set_setting(TESTBED_CLOCK_SETTING, now.isoformat())
        # An 'active_week' pin left behind by publish_next_week.py would
        # override the auto-detection and point the site at the wrong week.
        set_setting("active_week", "")
        print(f"Clock frozen at {now:%A %d %B %Y, %I:%M %p} Eastern")

        weeks, games = {}, {}
        for n in (1, 2, 3):
            weeks[n], games[n] = build_week(
                n, build_nfl_slate(teams), build_college_slate(),
                thursdays[n], played=(n in PLAYED_WEEKS),
            )
        db.session.commit()

        players = make_players()

        # Which Gridiron line favoured which side, so Drop Dead picks can lean
        # on it the way a real entrant would. NFL only -- college never
        # reaches the other two pools.
        for n in (1, 2, 3):
            fav_by_matchup = {
                (g.away_team, g.home_team): g.favorite
                for g in games[n]["gridiron"] if g.sport == "nfl"
            }
            for pool in ("dropdead", "loser"):
                for game in games[n][pool]:
                    game.favourite_side = fav_by_matchup.get(
                        (game.away_team, game.home_team)
                    )

        for n in PLAYED_WEEKS:
            play_week(n, weeks[n], games[n], players)
            if n == 1:
                # Week 1's casualties were offered their buy-back during week
                # 2, which is over, so that decision has been made.
                offered, taken = take_dropdead_buybacks(n, players, include_real=True)
                print(f"  week {n}: Drop Dead buy-back offered to {offered}, "
                      f"taken by {taken}")

        # Week 2's casualties are being offered theirs right now, in week 3.
        # Some of the field moved early; the real accounts have not, so the
        # offer is sitting there when they log in.
        offered, taken = take_dropdead_buybacks(2, players, include_real=False)
        print(f"  week 2: Drop Dead buy-back open now, {offered} eligible, "
              f"{taken} have taken it so far")

        submitted = open_week(weeks[3], games[3], players)
        print(f"  week 3: open, {submitted} of the mock field have picks in")

        report(players, weeks)


def report(players, weeks):
    from scoring import standings_gridiron, standings_loser

    alive = Entry.query.filter_by(pool="dropdead", season_year=SEASON,
                                  is_active=True).count()
    total = Entry.query.filter_by(pool="dropdead", season_year=SEASON).count()
    revived = Entry.query.filter(Entry.pool == "dropdead",
                                 Entry.buy_backs_used > 0).count()
    gi_bought = Entry.query.filter(Entry.pool == "gridiron",
                                   Entry.buy_backs_used > 0).count()
    nfl = Game.query.filter_by(sport="nfl").count()
    college = Game.query.filter_by(sport="college").count()

    print(f"\n{len(players)} players, all three pools.")
    print(f"  Games: {nfl} NFL rows across three pools, {college} college "
          f"rows in Gridiron")
    print(f"  Drop Dead: {alive} of {total} alive, {revived} bought back")
    print(f"  Gridiron:  {gi_bought} took the week-2 buy-back, "
          f"{GridironMiss.query.count()} missed weeks recorded")

    gw = get_current_week(SEASON, "gridiron")
    dw = get_current_week(SEASON, "dropdead")
    print(f"  Current week resolves to: gridiron {gw.number}, dropdead "
          f"{dw.number}; week 3 deadline passed? {deadline_passed(gw)}")

    tnf = Game.query.filter_by(week_id=weeks[3]['gridiron'].id, sport='nfl').order_by(Game.kickoff.asc()).first()
    print(f"  Week 3 first kickoff: {tnf.kickoff:%A %I:%M %p} ({tnf.label})")

    print("  Gridiron top of the table:")
    for rank, entry, w, l, t in standings_gridiron(SEASON)[:3]:
        print(f"    {rank}. {entry.user.username:<16} {w}-{l}-{t}")
    print("  Loser Pool top of the table:")
    for rank, entry, points in standings_loser(SEASON)[:3]:
        print(f"    {rank}. {entry.user.username:<16} {points}")

    print("\n  Real accounts (password 'Password'). Every one of them should")
    print("  read: gridiron makeup week 3, 8 picks + 2 penalty slots, and a")
    print("  Drop Dead buy-back on offer.")
    print(f"  {'username':<16}{'email':<30}{'role':<8}{'accts':<7}"
          f"{'gi wk3':<8}{'penalty':<9}{'DD buyback':<11}")
    ok = True
    for player in players:
        if not player["real"]:
            continue
        user = player["user"]
        gi = player["entries"]["gridiron"]
        dd = player["entries"]["dropdead"]
        limit = gridiron_pick_limit(gi, weeks[3]["gridiron"])
        penalty = gridiron_penalty_slots(gi, weeks[3]["gridiron"])
        buyback = dropdead_buyback_available(dd, weeks[3]["dropdead"])
        makeup = gridiron_makeup_week(gi)
        if not (limit == 8 and penalty == 2 and buyback and makeup == 3):
            ok = False
        print(f"  {user.username:<16}{user.email:<30}"
              f"{'admin' if user.is_admin else 'player':<8}{user.max_teams:<7}"
              f"{limit:<8}{penalty:<9}{'yes' if buyback else 'NO':<11}")
    print()
    print("  ALL FOURTEEN CORRECT" if ok else
          "  !! At least one real account is not in the intended state.")
    print()


if __name__ == "__main__":
    main()
