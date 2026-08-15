"""Publish weekly NFL/NCAAF lines from a single consistent source: The Odds
API (the-odds-api.com), preferring one bookmaker so the number doesn't hop
between books week to week. Idempotent -- safe to run repeatedly; never
touches a Game that's already been marked final.
"""

from datetime import datetime, timedelta

import requests

from helpers import EASTERN, get_setting, now_eastern
from models import PRESEASON_OFFSET, Game, Team, Week, db

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"
PREFERRED_BOOKMAKERS = ["draftkings", "fanduel", "betmgm"]

SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "college": "americanfootball_ncaaf",
}

# Before the regular season opens there's nothing on the main NFL feed, so
# preseason weeks pull exhibition games (which carry real spreads/totals) from
# the separate preseason feed instead.
PRESEASON_SPORT_KEY = "americanfootball_nfl_preseason"


def _team_lookup():
    return {f"{t.city} {t.name}".strip(): t for t in Team.query.all()}


def _parse_commence(iso_str):
    dt_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt_utc.astimezone(EASTERN).replace(tzinfo=None)


def _pick_bookmaker(event):
    books = {b["key"]: b for b in event.get("bookmakers", [])}
    for key in PREFERRED_BOOKMAKERS:
        if key in books:
            return books[key]
    return event["bookmakers"][0] if event.get("bookmakers") else None


def fetch_odds(sport, api_key, preseason=False):
    sport_key = PRESEASON_SPORT_KEY if (preseason and sport == "nfl") else SPORT_KEYS[sport]
    resp = requests.get(
        f"{ODDS_API_BASE}/{sport_key}/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "markets": "spreads,totals",
            "oddsFormat": "american",
            "dateFormat": "iso",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_lines(event):
    bookmaker = _pick_bookmaker(event)
    spread_by_team = {}
    total = None
    if bookmaker:
        for market in bookmaker.get("markets", []):
            if market["key"] == "spreads":
                for outcome in market["outcomes"]:
                    spread_by_team[outcome["name"]] = outcome["point"]
            elif market["key"] == "totals":
                for outcome in market["outcomes"]:
                    if outcome["name"] == "Over":
                        total = outcome["point"]
    return spread_by_team, total


def _first_thursday_of_august(year):
    d = datetime(year, 8, 1).date()
    return d + timedelta(days=(3 - d.weekday()) % 7)  # Thursday = 3


def _window_for_thursday(week_thursday):
    """The Thu-Wed game window and Saturday-noon pick deadline for a week
    that starts on the given Thursday (all Eastern, naive)."""
    window_start = datetime.combine(week_thursday, datetime.min.time())
    window_end = window_start + timedelta(days=6, hours=23, minutes=59)
    pick_deadline = datetime.combine(week_thursday + timedelta(days=2), datetime.min.time()) + timedelta(hours=12)
    return window_start, window_end, pick_deadline


def week_window(season_start, reference=None):
    """Figure out which week 'today' falls in relative to the season's Week-1
    Thursday, and that week's Thu-Wed window + Saturday noon pick deadline
    (all Eastern, naive).

    Before the season opens this returns a *preseason* week instead, numbered
    from the first Thursday in August the way the NFL numbers its exhibition
    weeks (Aug 6 -> Preseason Week 1, Aug 13 -> Week 2, ...), and stored
    offset past the regular-season numbers. Returns
    ``(number, window_start, window_end, pick_deadline, is_preseason)``."""
    ref = reference or now_eastern()
    days_since_start = (ref.date() - season_start).days

    if days_since_start < 0:
        # This week's Thursday, i.e. the Thursday on or before today.
        week_thursday = ref.date() - timedelta(days=(ref.weekday() - 3) % 7)
        # Clamped at 1 so an unusually early window (a July run, say) can't
        # produce a number that collides back into regular-season territory.
        ps_number = max(1, (week_thursday - _first_thursday_of_august(week_thursday.year)).days // 7 + 1)
        return (PRESEASON_OFFSET + ps_number, *_window_for_thursday(week_thursday), True)

    number = max(1, days_since_start // 7 + 1)
    week_thursday = season_start + timedelta(weeks=number - 1)
    return (number, *_window_for_thursday(week_thursday), False)


def publish_week(app):
    with app.app_context():
        api_key = get_setting("odds_api_key")
        season_start_str = get_setting("season_start_thursday")
        if not api_key:
            raise RuntimeError("No Odds API key configured (Admin > Settings).")
        if not season_start_str:
            raise RuntimeError("No season start date configured (Admin > Settings).")

        season_start = datetime.fromisoformat(season_start_str).date()
        season_year = app.config["CURRENT_SEASON"]

        number, window_start, window_end, pick_deadline, is_preseason = week_window(season_start)

        # Published lines (spreads/O-U) are Gridiron's; it's the only pool
        # with lines, so auto-publish targets the Gridiron week. Drop Dead and
        # Loser get the same week (and the straight-up matchups below) so all
        # three pools are playable off one publish.
        pool_weeks = {}
        newly_created_weeks = set()
        for pool in ("gridiron", "dropdead", "loser"):
            w = Week.query.filter_by(season_year=season_year, number=number, pool=pool).first()
            if not w:
                w = Week(
                    season_year=season_year,
                    number=number,
                    pool=pool,
                    pick_deadline=pick_deadline,
                    is_preseason=is_preseason,
                )
                db.session.add(w)
                newly_created_weeks.add(pool)
            pool_weeks[pool] = w
        db.session.commit()
        week = pool_weeks["gridiron"]
        # Captured now: an empty week may be deleted below, and reading an
        # attribute off a deleted instance afterwards would blow up.
        week_label = week.label

        team_lookup = _team_lookup()
        created, already_published = 0, 0
        unmatched = set()

        for sport in ("nfl", "college"):
            events = fetch_odds(sport, api_key, preseason=is_preseason)
            for event in events:
                kickoff = _parse_commence(event["commence_time"])
                if not (window_start <= kickoff <= window_end):
                    continue

                home_name = event["home_team"]
                away_name = event["away_team"]
                spread_by_team, total = _extract_lines(event)

                favorite, spread_value = None, None
                if home_name in spread_by_team:
                    home_pt = spread_by_team[home_name]
                    if home_pt < 0:
                        favorite, spread_value = "home", abs(home_pt)
                    elif home_pt > 0:
                        favorite, spread_value = "away", abs(home_pt)
                    else:
                        favorite, spread_value = None, 0

                home_team_obj = team_lookup.get(home_name)
                away_team_obj = team_lookup.get(away_name)
                if sport == "nfl":
                    if home_team_obj is None:
                        unmatched.add(home_name)
                    if away_team_obj is None:
                        unmatched.add(away_name)

                existing = Game.query.filter_by(
                    week_id=week.id, home_team=home_name, away_team=away_name
                ).first()
                if existing:
                    # The line is frozen once first published -- re-running
                    # publish (the Thursday job, or a manual re-click) must
                    # never move the spread/total on a game players may
                    # already be picking against. Only kickoff (a schedule
                    # fact, not a line) stays in sync.
                    existing.kickoff = kickoff
                    already_published += 1
                    continue

                game = Game(
                    week_id=week.id,
                    pool="gridiron",
                    sport=sport,
                    home_team=home_name,
                    away_team=away_name,
                    home_team_id=home_team_obj.id if home_team_obj else None,
                    away_team_id=away_team_obj.id if away_team_obj else None,
                    favorite=favorite,
                    spread=spread_value,
                    over_under=total if sport == "nfl" else None,
                    kickoff=kickoff,
                )
                db.session.add(game)
                created += 1

                # Drop Dead and Loser are straight-up NFL pools: they carry the
                # matchup and kickoff but never the line, and never college.
                if sport != "nfl":
                    continue
                for pool in ("dropdead", "loser"):
                    pw = pool_weeks[pool]
                    if Game.query.filter_by(
                        week_id=pw.id, home_team=home_name, away_team=away_name
                    ).first():
                        continue
                    db.session.add(Game(
                        week_id=pw.id,
                        pool=pool,
                        sport="nfl",
                        home_team=home_name,
                        away_team=away_name,
                        home_team_id=home_team_obj.id if home_team_obj else None,
                        away_team_id=away_team_obj.id if away_team_obj else None,
                        kickoff=kickoff,
                    ))

        db.session.commit()

        # Determine the Monday-nighter from each pool week's full current game
        # set (existing + newly created), not just what this run touched --
        # otherwise a re-publish that only adds new games would miss an
        # already-published Monday game when picking the MNF flag. Loser reads
        # the flag off its own games for its auto-pick, so every pool week
        # needs it set, not just Gridiron's.
        for pw in pool_weeks.values():
            nfl_games = Game.query.filter_by(week_id=pw.id, sport="nfl").all()
            monday_games = [g for g in nfl_games if g.kickoff and g.kickoff.weekday() == 0]
            monday_game = max(monday_games, key=lambda g: g.kickoff) if monday_games else None
            if monday_game:
                for g in nfl_games:
                    g.is_mnf = g is monday_game
        db.session.commit()

        # A window with nothing to publish -- the gap between the last
        # exhibition weekend and the opener, say -- shouldn't leave an empty
        # week sitting on players' pick pages. Only weeks this run created are
        # eligible, so a week an admin built by hand is never dropped.
        dropped = []
        for pool in sorted(newly_created_weeks):
            pw = pool_weeks[pool]
            if not Game.query.filter_by(week_id=pw.id).first():
                dropped.append(pool)
                db.session.delete(pw)
        if dropped:
            db.session.commit()

        return {
            "week_number": number,
            "week_label": week_label,
            "dropped_empty_pools": dropped,
            "is_preseason": is_preseason,
            "created": created,
            "already_published": already_published,
            "unmatched": sorted(unmatched),
        }
