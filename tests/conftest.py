"""Shared fixtures for the scoring tests.

Everything runs against a throwaway in-memory SQLite database built straight
from models.py, so the tests never touch instance/pools.db and need no
seeding, no network and no Odds API key.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Entry, Game, LoserPoolPoints, Pick, Team, User, Week, db  # noqa: E402

SEASON = 2026

# Every deadline the tests create sits far enough in the past that
# helpers.deadline_passed() is unconditionally true, so a test never depends
# on the wall clock of the machine running it.
LONG_PAST = datetime(2020, 9, 5, 12, 0)


@pytest.fixture
def app():
    application = Flask(__name__)
    application.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite://",  # in-memory
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        CURRENT_SEASON=SEASON,
    )
    db.init_app(application)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def make_week(app):
    """A gridiron week with `games` NFL games, all past deadline by default.

    Each game carries both a spread and an over/under, so 8 games means 16
    pickable slots -- comfortably more than any pick allowance under test,
    which keeps the "capped at what was actually pickable" branch out of the
    way unless a test asks for it.
    """

    def _make(number, games=8, pool="gridiron", future=False, over_under=44.5, **kw):
        deadline = (
            datetime.now() + timedelta(days=30)
            if future
            else LONG_PAST + timedelta(days=7 * number)
        )
        week = Week(
            season_year=SEASON, number=number, pool=pool, pick_deadline=deadline, **kw
        )
        db.session.add(week)
        db.session.flush()
        for i in range(games):
            db.session.add(
                Game(
                    week_id=week.id,
                    pool=pool,
                    sport="nfl",
                    home_team=f"Home{number}-{i}",
                    away_team=f"Away{number}-{i}",
                    favorite="home",
                    spread=3.0,
                    over_under=over_under,
                    kickoff=deadline + timedelta(hours=26),
                )
            )
        db.session.commit()
        return week

    return _make


@pytest.fixture
def make_entry(app):
    def _make(name, pool="gridiron"):
        user = User(username=name, email=f"{name}@example.com", password_hash="x")
        db.session.add(user)
        db.session.flush()
        entry = Entry(user_id=user.id, pool=pool, season_year=SEASON, label=name)
        db.session.add(entry)
        db.session.commit()
        return entry

    return _make


@pytest.fixture
def submit(app):
    """Save `count` gridiron picks for an entry in a week, all with `result`."""

    def _submit(entry, week, count, result="win"):
        games = Game.query.filter_by(week_id=week.id, pool="gridiron").all()
        assert count <= len(games), "week needs more games than picks requested"
        for game in games[:count]:
            db.session.add(
                Pick(
                    entry_id=entry.id,
                    week_id=week.id,
                    pool="gridiron",
                    game_id=game.id,
                    market="spread",
                    side="home",
                    result=result,
                    points=1 if result == "win" else 0,
                )
            )
        db.session.commit()

    return _submit


@pytest.fixture
def record(app):
    """(wins, losses, ties) for one entry, straight out of standings_gridiron."""
    from scoring import standings_gridiron

    def _record(entry):
        for _rank, e, wins, losses, ties in standings_gridiron(SEASON):
            if e.id == entry.id:
                return (wins, losses, ties)
        raise AssertionError("entry not present in standings")

    return _record


@pytest.fixture
def team(app):
    def _team(city, name):
        t = Team(city=city, name=name)
        db.session.add(t)
        db.session.commit()
        return t

    return _team
