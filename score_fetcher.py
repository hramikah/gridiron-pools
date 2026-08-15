"""Pull final scores from The Odds API and finalize games automatically.

Mirrors exactly what an admin does by hand in Admin > enter result: writes
home_score/away_score, flips is_final, then runs the same scoring path
(score_game + enforce_dropdead_no_tie) so all three pools stay consistent.

Deliberately conservative:
  * only games the API reports as ``completed`` with both scores present,
  * never touches a game already marked final -- a manual correction by an
    admin always wins over a later API poll,
  * matches on the exact team-name pair within the game's own week, so a
    rematch in a different week can't be scored by the wrong result.
"""

import logging
from datetime import timedelta

import requests

from helpers import get_setting, now_eastern
from models import Game, Week, db
from publisher import ODDS_API_BASE, PRESEASON_SPORT_KEY, SPORT_KEYS, _parse_commence
from scoring import enforce_dropdead_no_tie, score_game

log = logging.getLogger(__name__)

# The API caps this at 3; games sit in the feed for that long after finishing,
# which is ample for a job that runs several times a day.
DAYS_FROM = 3

# A scores request costs 2 API credits against a 500/month plan, so this job
# only spends them when a game has actually finished and is still unscored:
# nothing kicked off long enough ago => no request at all. Three hours covers a
# normal game plus overtime.
GAME_LENGTH = timedelta(hours=3)

# Remaining-credit level at which the log starts warning.
LOW_CREDIT_WARNING = 100


def fetch_scores(sport, api_key, preseason=False):
    sport_key = PRESEASON_SPORT_KEY if (preseason and sport == "nfl") else SPORT_KEYS[sport]
    resp = requests.get(
        f"{ODDS_API_BASE}/{sport_key}/scores",
        params={"apiKey": api_key, "daysFrom": DAYS_FROM, "dateFormat": "iso"},
        timeout=20,
    )
    resp.raise_for_status()
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        try:
            remaining = int(float(remaining))
            log.info("Odds API credits remaining: %s", remaining)
            if remaining < LOW_CREDIT_WARNING:
                log.warning("Odds API credits running low: %s remaining", remaining)
        except ValueError:
            pass
    return resp.json()


def _final_scores(event):
    """(home_score, away_score) for a completed event, or None if the feed
    hasn't posted a usable final yet."""
    if not event.get("completed") or not event.get("scores"):
        return None
    by_name = {}
    for s in event["scores"]:
        try:
            by_name[s["name"]] = int(s["score"])
        except (TypeError, ValueError):
            return None
    home, away = event.get("home_team"), event.get("away_team")
    if home not in by_name or away not in by_name:
        return None
    return by_name[home], by_name[away]


def update_scores(app):
    with app.app_context():
        api_key = get_setting("odds_api_key")
        if not api_key:
            raise RuntimeError("No Odds API key configured (Admin > Settings).")

        season_year = app.config["CURRENT_SEASON"]

        # Which feeds are actually worth paying for: only those with a game
        # that has kicked off, had time to finish, and still isn't final.
        cutoff = now_eastern() - GAME_LENGTH
        awaiting = (
            Game.query.join(Week)
            .filter(
                Week.season_year == season_year,
                Game.is_final.is_(False),
                Game.kickoff.isnot(None),
                Game.kickoff <= cutoff,
            )
            .all()
        )
        if not awaiting:
            log.info("No finished games awaiting a score; skipping API call.")
            return {"finalized": 0, "already_final": 0, "pending": 0, "details": [], "feeds": []}

        feeds = set()
        for g in awaiting:
            if g.sport == "college":
                feeds.add(("college", False))
            else:
                feeds.add(("nfl", bool(g.week.is_preseason)))

        events = []
        for sport, preseason in sorted(feeds):
            events += fetch_scores(sport, api_key, preseason=preseason)

        # Key results by the matchup, the same way publisher.py stores them.
        results = {}
        for event in events:
            scores = _final_scores(event)
            if scores is None:
                continue
            results[(event["away_team"], event["home_team"])] = (scores, event["commence_time"])

        finalized, already_final, pending = 0, 0, 0
        touched_weeks = set()
        details = []

        for game in Game.query.join(Week).filter(Week.season_year == season_year).all():
            if game.is_final:
                already_final += 1
                continue
            hit = results.get((game.away_team, game.home_team))
            if hit is None:
                pending += 1
                continue
            (home_score, away_score), commence = hit

            # Guard against scoring a game with a same-matchup result from a
            # different week (rematches, or a preseason/regular overlap).
            kickoff = _parse_commence(commence)
            if game.kickoff and abs((kickoff - game.kickoff).total_seconds()) > 36 * 3600:
                pending += 1
                continue

            game.home_score = home_score
            game.away_score = away_score
            game.is_final = True
            db.session.commit()

            score_game(game)
            touched_weeks.add(game.week_id)
            finalized += 1
            details.append(f"{game.pool}: {game.away_team} {away_score} @ {game.home_team} {home_score}")

        # Drop Dead treats a tie as elimination; the admin path runs this after
        # each result, so do the same for every week we touched.
        for week_id in touched_weeks:
            week = db.session.get(Week, week_id)
            if week:
                enforce_dropdead_no_tie(week)
        db.session.commit()

        return {
            "finalized": finalized,
            "already_final": already_final,
            "pending": pending,
            "details": details,
            "feeds": sorted(f"{s}{'-preseason' if p else ''}" for s, p in feeds),
        }
