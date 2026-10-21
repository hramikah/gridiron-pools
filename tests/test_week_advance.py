"""The current week moves on as soon as the closed week's last game is final,
instead of holding until the next week's betting window opens."""

from datetime import timedelta

from helpers import get_current_week, now_eastern, set_setting
from models import Game, Week, db
from tests.conftest import SEASON


def _week(number, deadline, games=2, pool="gridiron"):
    w = Week(season_year=SEASON, number=number, pool=pool, pick_deadline=deadline)
    db.session.add(w)
    db.session.flush()
    for i in range(games):
        db.session.add(Game(week_id=w.id, pool=pool, sport="nfl",
                            home_team=f"H{number}-{i}", away_team=f"A{number}-{i}"))
    db.session.commit()
    return w


def _finish(week, upto=None):
    games = Game.query.filter_by(week_id=week.id).order_by(Game.id).all()
    for g in games[:upto]:
        g.home_score, g.away_score, g.is_final = 21, 14, True
    db.session.commit()


def _setup(app):
    now = now_eastern()
    # Closed Saturday; the next deadline is far enough out that its window
    # (3 days before it) has not opened yet -- the Sunday-to-Wednesday gap.
    closed = _week(6, now - timedelta(days=2))
    nxt = _week(7, now + timedelta(days=5), games=0)  # games not posted yet
    return closed, nxt


def test_holds_while_a_game_is_still_going(app):
    closed, _nxt = _setup(app)
    _finish(closed, upto=1)  # Monday night still to be scored
    assert get_current_week(SEASON, "gridiron").id == closed.id


def test_advances_once_every_game_is_final(app):
    closed, nxt = _setup(app)
    _finish(closed)
    assert get_current_week(SEASON, "gridiron").id == nxt.id


def test_admin_pin_still_wins(app):
    closed, _nxt = _setup(app)
    _finish(closed)
    set_setting("active_week", "6")
    assert get_current_week(SEASON, "gridiron").id == closed.id


def test_last_week_of_season_stays_put(app):
    closed = _week(18, now_eastern() - timedelta(days=2))
    _finish(closed)
    assert get_current_week(SEASON, "gridiron").id == closed.id
