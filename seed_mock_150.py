"""A 150-player Gridiron pool, frozen at Thursday 4:00 PM of week 2.

Week 1 is played, finalised and scored. Week 2 is open, its lines are up, and
the $100 buy-back has three hours left on it -- the window shuts at 7:00 PM
Thursday. That is the moment worth staring at: everything the revise rules do
is either visible or still decidable.

Who is who:

  ~70%  turned in all 5 week-1 picks
  ~15%  turned in some but not all of them (each empty slot is a loss)
  ~15%  turned in nothing at all (0-5, and a makeup week now owed)
   18   have already paid the $100 -- a mix of people who forgot week 1 and
        people who played it badly -- so week 2 is a 10-pick catch-up for them
   ~55  have already made a start on their week-2 picks

The **admin** account gets an entry that sat out week 1, so logging in as
admin lands on a pick page showing both the buy-back offer and the 8-pick
makeup it would otherwise take. Every mock player's password is `test1234`.

The site really does believe it is Thursday afternoon: the seed writes the
moment into the `testbed_fake_now` setting, which helpers.now_eastern() reads
**only** when the database is a testbed one. Delete that setting (or run any
other seed) and the real clock comes back.

Local test databases only -- see testbed_guard.py.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import TESTBED_CLOCK_SETTING, set_setting
from models import Entry, Game, GridironMiss, Pick, User, Week, db
from scoring import process_missed_picks, score_game
from testbed_guard import require_testbed_database

SEASON = 2026
PLAYER_COUNT = 150
REBUY_COUNT = 18
MOCK_PASSWORD = "test1234"

# A fixed seed so a re-run reproduces the same pool -- easier to talk about a
# bug when "gutierrez_k is the one on 4-1" stays true.
RNG = random.Random(20260821)

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

# (away, home, favorite, spread, over/under)
WEEK1 = [
    ("Cowboys", "Eagles", "home", 3.0, 44.5),
    ("Packers", "Bears", "away", 6.5, 41.5),
    ("Chiefs", "Ravens", "away", 2.5, 48.5),
    ("49ers", "Rams", "away", 4.0, 45.5),
    ("Bills", "Dolphins", "away", 3.5, 50.5),
    ("Bengals", "Steelers", "home", 1.5, 40.5),
    ("Lions", "Vikings", "away", 5.0, 47.5),
    ("Jets", "Patriots", "home", 2.0, 38.5),
    ("Texans", "Colts", "away", 3.0, 43.5),
    ("Chargers", "Broncos", "home", 1.0, 42.5),
    ("Saints", "Falcons", "home", 2.5, 44.5),
    ("Seahawks", "Cardinals", "away", 4.5, 46.5),
    ("Buccaneers", "Panthers", "away", 6.0, 41.5),
    ("Raiders", "Titans", "home", 2.0, 39.5),
]
WEEK2 = [
    ("Eagles", "Falcons", "away", 3.0, 43.5),
    ("Bears", "Texans", "home", 2.5, 42.5),
    ("Ravens", "Raiders", "away", 7.0, 45.5),
    ("Rams", "Cardinals", "away", 4.5, 46.5),
    ("Dolphins", "Seahawks", "home", 1.0, 44.5),
    ("Steelers", "Broncos", "away", 3.0, 39.5),
    ("Vikings", "Saints", "home", 2.0, 43.5),
    ("Patriots", "Titans", "away", 5.5, 41.5),
    ("Colts", "Jaguars", "away", 2.0, 44.5),
    ("Browns", "Bengals", "home", 4.0, 42.5),
    ("Giants", "Commanders", "home", 3.5, 40.5),
    ("Jets", "Bills", "home", 7.5, 45.5),
    ("Panthers", "Buccaneers", "home", 5.0, 43.5),
    ("Chiefs", "Chargers", "away", 3.0, 47.5),
]

# Kickoff offsets from the week's Thursday, in hours: the Thursday nighter,
# then a Sunday slate, then Monday night.
KICKOFFS = [4 + 20.25] + [3 * 24 + 13.0] * 8 + [3 * 24 + 16.42] * 3 + [
    3 * 24 + 20.33, 4 * 24 + 20.25
]


def week_thursday(reference):
    """The Thursday of the week `reference` falls in (Monday-based)."""
    return reference - timedelta(days=reference.weekday() - 3) if reference.weekday() >= 3 \
        else reference - timedelta(days=reference.weekday() + 4)


def build_week(number, rows, thursday, final):
    """One Gridiron week: Saturday-noon deadline, 14 games hung off the
    Thursday. `final` scores them; otherwise they're still to play."""
    deadline = datetime.combine(
        (thursday + timedelta(days=2)).date(), datetime.min.time()
    ) + timedelta(hours=12)
    week = Week(
        season_year=SEASON,
        number=number,
        pool="gridiron",
        pick_deadline=deadline,
        buyback_open=True,  # week 2 needs it; harmless on week 1
    )
    db.session.add(week)
    db.session.flush()

    games = []
    for (away, home, favorite, spread, ou), hours in zip(rows, KICKOFFS):
        game = Game(
            week_id=week.id,
            pool="gridiron",
            sport="nfl",
            away_team=away,
            home_team=home,
            favorite=favorite,
            spread=spread,
            over_under=ou,
            kickoff=thursday.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(hours=hours),
            is_mnf=hours > 4 * 24,
        )
        if final:
            # Scores that land on both sides of the spread and the total,
            # including the occasional push, so the report shows every colour.
            base = RNG.choice([(24, 17), (20, 23), (31, 13), (14, 17), (27, 24),
                               (10, 30), (21, 21), (17, 20), (35, 28), (13, 16)])
            game.away_score, game.home_score = base
            game.is_final = True
        games.append(game)
        db.session.add(game)
    db.session.flush()
    return week, games


def wipe():
    """Clear the pool clean, leaving teams, point values and real accounts."""
    Pick.query.delete(synchronize_session=False)
    GridironMiss.query.delete(synchronize_session=False)
    Entry.query.delete(synchronize_session=False)
    Game.query.delete(synchronize_session=False)
    Week.query.delete(synchronize_session=False)
    User.query.filter(User.email.like("%@mockpool.test")).delete(
        synchronize_session=False
    )
    db.session.commit()


def make_players():
    names, used = [], set()
    while len(names) < PLAYER_COUNT:
        candidate = f"{RNG.choice(FIRST_NAMES)}_{RNG.choice(LAST_INITIALS)}"
        if candidate not in used:
            used.add(candidate)
            names.append(candidate)

    # One hash, reused for every mock account. Hashing 150 passwords
    # properly takes about half a minute of pbkdf2 and buys nothing here --
    # they all share the same throwaway password anyway.
    from werkzeug.security import generate_password_hash

    shared_hash = generate_password_hash(MOCK_PASSWORD, method="pbkdf2:sha256")

    entries = []
    for name in names:
        user = User(username=name, email=f"{name}@mockpool.test", max_teams=1,
                    password_hash=shared_hash)
        db.session.add(user)
        db.session.flush()
        entry = Entry(user_id=user.id, pool="gridiron", season_year=SEASON,
                      label="Entry 1")
        db.session.add(entry)
        entries.append(entry)
    db.session.flush()
    return entries


def submit(entry, week, games, count):
    """`count` picks for this entry, spread of markets, sides chosen at
    random so week 1 produces a real spread of records."""
    if count <= 0:
        return
    slots = []
    for game in games:
        slots.append((game, "spread", RNG.choice(["home", "away"])))
        if game.over_under is not None:
            slots.append((game, "total", RNG.choice(["over", "under"])))
    for game, market, side in RNG.sample(slots, min(count, len(slots))):
        db.session.add(
            Pick(entry_id=entry.id, week_id=week.id, pool="gridiron",
                 game_id=game.id, market=market, side=side)
        )


def main():
    with app.app_context():
        uri = require_testbed_database(app, "seed_mock_150.py")
        print(f"Seeding {uri}")
        wipe()

        # --- the moment ---------------------------------------------------
        thursday_2 = week_thursday(datetime.now()).replace(
            hour=16, minute=0, second=0, microsecond=0
        )
        thursday_1 = thursday_2 - timedelta(days=7)
        set_setting(TESTBED_CLOCK_SETTING, thursday_2.isoformat())
        print(f"Clock frozen at {thursday_2:%A %d %B %Y, %I:%M %p} Eastern")

        week1, games1 = build_week(1, WEEK1, thursday_1, final=True)
        week2, games2 = build_week(2, WEEK2, thursday_2, final=False)
        db.session.commit()

        # --- the field ----------------------------------------------------
        entries = make_players()

        admin = User.query.filter_by(username="admin").first()
        admin_entry = None
        if admin:
            admin_entry = Entry(user_id=admin.id, pool="gridiron",
                                season_year=SEASON, label="Entry 1")
            db.session.add(admin_entry)
            db.session.flush()

        # --- week 1 -------------------------------------------------------
        shuffled = entries[:]
        RNG.shuffle(shuffled)
        full = shuffled[:105]           # all five picks in
        partial = shuffled[105:128]     # started and stopped
        missing = shuffled[128:]        # never turned up

        for entry in full:
            submit(entry, week1, games1, 5)
        for entry in partial:
            submit(entry, week1, games1, RNG.randint(1, 4))
        # `missing` submit nothing, and the admin entry sits week 1 out too so
        # that logging in as admin lands on the interesting page.
        db.session.commit()

        for game in games1:
            score_game(game)
        process_missed_picks(week1)
        week1.missed_processed = True
        db.session.commit()

        # --- the $100 --------------------------------------------------
        # Split between people who forgot week 1 and people who played it and
        # hated the result, because both are entitled to it.
        forgot = [e for e in missing][:10]
        played_badly = sorted(
            full,
            key=lambda e: sum(1 for p in e.picks if p.result == "win"),
        )[: REBUY_COUNT - len(forgot)]
        for entry in forgot + played_badly:
            entry.buyback_week = 1
            entry.buy_backs_used = 1
        db.session.commit()

        # --- week 2, partly done ------------------------------------------
        from scoring import gridiron_pick_limit

        started = RNG.sample(entries, 55)
        for entry in started:
            limit = gridiron_pick_limit(entry, week2)
            # Some are finished, some are mid-way through.
            submit(entry, week2, games2,
                   limit if RNG.random() < 0.55 else RNG.randint(1, limit - 1))
        db.session.commit()

        # --- what we ended up with ----------------------------------------
        from scoring import standings_gridiron

        rows = standings_gridiron(SEASON)
        print(f"\n{len(entries)} players + admin. Week 1 scored, week 2 open.")
        print(f"  all 5 picks in: {len(full)}   partial: {len(partial)}   "
              f"no picks: {len(missing) + (1 if admin_entry else 0)}")
        print(f"  bought back:    {REBUY_COUNT}")
        print(f"  week-2 picks started: {len(started)}")
        print("\n  top of the table:")
        for rank, entry, wins, losses, ties in rows[:5]:
            print(f"    {rank}. {entry.user.username:<16} {wins}-{losses}-{ties}")
        print(f"\n  Log in as admin, or any player with password {MOCK_PASSWORD!r}.")
        print(f"  The buy-back closes at {thursday_2.replace(hour=19):%I:%M %p} "
              "-- three hours from 'now'.")


if __name__ == "__main__":
    main()
