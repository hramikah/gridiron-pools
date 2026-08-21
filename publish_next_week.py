"""Thursday: the next week's lines are out and nothing has kicked off yet.

Creates the following week in all three pools with a deadline at the coming
Saturday noon, a full slate carrying spreads and totals, and no scores. No
picks are submitted -- the board is open and every entry is looking at it
fresh, which is what a Thursday morning actually looks like after the
publisher has run.

This is the offline stand-in for publisher.py, which pulls the same thing
from The Odds API. It burns no API credits and needs no key, so it's safe to
run against a test database as often as you like.

Also repairs each existing week's buy-back window to what default_buyback_open
says it should be, so a week seeded before that rule existed still offers the
Drop Dead weeks 1-4 and Gridiron week 2 buy-backs.

    venv/bin/python publish_next_week.py

Local test databases only -- see testbed_guard.py.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import set_setting
from models import Game, Team, Week, db, default_buyback_open
from testbed_guard import require_testbed_database

SEASON = 2026
POOLS = ("dropdead", "loser", "gridiron")

NFL_GAMES = 16          # a full slate: all 32 teams play
COLLEGE_GAMES = 8       # Gridiron only, per the printed rules

COLLEGE_MATCHUPS = [
    ("Michigan Wolverines", "Penn State Nittany Lions"),
    ("Georgia Bulldogs", "Tennessee Volunteers"),
    ("Oklahoma Sooners", "Texas Longhorns"),
    ("USC Trojans", "Oregon Ducks"),
    ("Florida State Seminoles", "Miami Hurricanes"),
    ("Alabama Crimson Tide", "LSU Tigers"),
    ("Ohio State Buckeyes", "Wisconsin Badgers"),
    ("Notre Dame Fighting Irish", "Clemson Tigers"),
]


def next_saturday_noon(now):
    """Noon Eastern on the next Saturday strictly in the future."""
    days_ahead = (5 - now.weekday()) % 7  # Monday is 0, Saturday is 5
    saturday = (now + timedelta(days=days_ahead)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    if saturday <= now:
        saturday += timedelta(days=7)
    return saturday


def repair_buyback_windows():
    """Bring every existing week's buy-back flag in line with the rules."""
    changed = []
    for week in Week.query.filter_by(season_year=SEASON).all():
        want = default_buyback_open(week.pool, week.number, week.is_preseason)
        if week.buyback_open != want:
            week.buyback_open = want
            changed.append(f"{week.pool} week {week.number} -> {'open' if want else 'closed'}")
    db.session.commit()
    return changed


def build_slate(rng, deadline):
    """A full NFL slate plus a college card, mirrored the way the publisher
    does it: the straight-up NFL matchups go into all three pools with their
    team foreign keys set, and college stays Gridiron-only."""
    teams = Team.query.order_by(Team.id).all()
    if len(teams) < NFL_GAMES * 2:
        raise SystemExit("Not enough teams -- run seed.py first.")
    shuffled = teams[:]
    rng.shuffle(shuffled)
    matchups = [(shuffled[i], shuffled[i + 1]) for i in range(0, NFL_GAMES * 2, 2)]

    kickoffs = (
        [deadline - timedelta(days=2) + timedelta(hours=8, minutes=15)]     # Thu 20:15
        + [deadline + timedelta(days=1, hours=1)] * 8                       # Sun 13:00
        + [deadline + timedelta(days=1, hours=4, minutes=25)] * 6           # Sun 16:25
        + [deadline + timedelta(days=2, hours=8, minutes=15)]               # Mon 20:15
    )
    return matchups, kickoffs


def run():
    require_testbed_database(app, "publish_next_week.py")
    rng = random.Random()

    with app.app_context():
        repaired = repair_buyback_windows()

        existing = [w.number for w in Week.query.filter_by(season_year=SEASON).all()]
        number = (max(existing) + 1) if existing else 1
        if Week.query.filter_by(season_year=SEASON, number=number).first():
            raise SystemExit(f"Week {number} already exists.")

        deadline = next_saturday_noon(datetime.now())
        weeks = {}
        for pool in POOLS:
            week = Week(
                season_year=SEASON,
                number=number,
                pool=pool,
                pick_deadline=deadline,
                is_preseason=False,
                buyback_open=default_buyback_open(pool, number),
            )
            db.session.add(week)
            weeks[pool] = week
        db.session.flush()

        matchups, kickoffs = build_slate(rng, deadline)
        for idx, ((away, home), kickoff) in enumerate(zip(matchups, kickoffs)):
            is_mnf = idx == len(matchups) - 1
            favorite = rng.choice(["home", "away"])
            spread = rng.choice([1.0, 2.5, 3.0, 3.5, 4.5, 6.0, 6.5, 7.0, 9.5, 10.0])
            over_under = rng.choice([38.0, 41.5, 43.0, 44.5, 47.0, 48.5, 51.0])
            for pool in POOLS:
                db.session.add(Game(
                    week_id=weeks[pool].id, pool=pool, sport="nfl",
                    away_team=away.name, home_team=home.name,
                    away_team_id=away.id, home_team_id=home.id,
                    favorite=favorite if pool == "gridiron" else None,
                    spread=spread if pool == "gridiron" else None,
                    over_under=over_under if pool == "gridiron" else None,
                    kickoff=kickoff, is_mnf=is_mnf,
                ))

        for away_name, home_name in COLLEGE_MATCHUPS[:COLLEGE_GAMES]:
            db.session.add(Game(
                week_id=weeks["gridiron"].id, pool="gridiron", sport="college",
                away_team=away_name, home_team=home_name,
                favorite=rng.choice(["home", "away"]),
                spread=rng.choice([2.5, 3.0, 6.5, 7.0, 10.5, 14.0]),
                over_under=None,
                kickoff=deadline + timedelta(hours=3),
            ))
        db.session.commit()

        set_setting("active_week", str(number))
        report(number, deadline, weeks, repaired)


def report(number, deadline, weeks, repaired):
    from models import Entry, User
    from scoring import (gridiron_buyback_available, gridiron_makeup_week,
                         gridiron_pick_limit)
    try:
        from scoring import dropdead_buyback_available
    except ImportError:
        dropdead_buyback_available = None

    print()
    print("=" * 74)
    print(f"  Week {number} lines are out. Deadline {deadline:%a %d %b %Y at %I:%M %p} Eastern.")
    print(f"  Nothing has kicked off. No picks submitted.")
    print("=" * 74)
    if repaired:
        print("  Buy-back windows corrected:")
        for line in repaired:
            print(f"    {line}")
        print()

    for pool in POOLS:
        w = weeks[pool]
        print(f"  {pool:<9} {Game.query.filter_by(week_id=w.id).count():>2} games   "
              f"buy-backs {'OPEN' if w.buyback_open else 'closed'}")

    print(f"\n  What each Gridiron entry is looking at in week {number}:")
    seen = {}
    for entry in Entry.query.filter_by(pool="gridiron", season_year=SEASON).join(User).order_by(User.username).all():
        limit = gridiron_pick_limit(entry, weeks["gridiron"])
        makeup = gridiron_makeup_week(entry) == number
        buyback = gridiron_buyback_available(entry, weeks["gridiron"])
        key = (limit, makeup, buyback)
        seen.setdefault(key, []).append(entry.user.username)
    for (limit, makeup, buyback), names in sorted(seen.items()):
        note = []
        if makeup:
            note.append("makeup week (2-game penalty)")
        if buyback:
            note.append("$100 buy-back offered")
        print(f"    {limit} picks{' -- ' + ', '.join(note) if note else ''}")
        print(f"      {', '.join(names)}")

    if dropdead_buyback_available:
        print(f"\n  Drop Dead buy-back in week {number}:")
        offered, denied = [], []
        for entry in Entry.query.filter_by(pool="dropdead", season_year=SEASON, is_active=False).join(User).order_by(User.username).all():
            (offered if dropdead_buyback_available(entry, weeks["dropdead"]) else denied).append(entry.user.username)
        print(f"    offered ($30): {', '.join(offered) or 'nobody'}")
        print(f"    denied:        {', '.join(denied) or 'nobody'}  (rule 6: no buy-back after a no-show)")
    print()


if __name__ == "__main__":
    run()
