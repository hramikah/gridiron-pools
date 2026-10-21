"""The Data page (Weekly Wrapped): when a week publishes, and that building
it never writes to the database."""

from datetime import datetime, timedelta

from models import BuyBack, Entry, Game, Pick, User, Week, db
from tests.conftest import SEASON
import wrapped


def _finish(week):
    for g in Game.query.filter_by(week_id=week.id).all():
        g.home_score, g.away_score, g.is_final = 24, 17, True
    db.session.commit()


def _counts():
    return tuple(m.query.count() for m in (User, Entry, Week, Game, Pick, BuyBack))


def test_unfinished_week_is_admin_preview_only(make_week, make_entry, submit):
    week = make_week(1, games=6)
    week.pick_deadline = datetime.now() - timedelta(days=1)  # just passed
    db.session.commit()
    e = make_entry("alice")
    submit(e, week, 5, result="pending")
    state, _rows = wrapped.week_publish_state(SEASON, 1)
    assert state == "preview"
    assert wrapped.week_menu(SEASON) == []
    assert [n for n, _l, _s in wrapped.week_menu(SEASON, include_preview=True)] == [1]


def test_week_publishes_once_every_game_is_final(make_week, make_entry, submit):
    week = make_week(1, games=6)
    submit(make_entry("alice"), week, 5)
    _finish(week)
    assert wrapped.week_publish_state(SEASON, 1)[0] == "published"


def test_open_week_never_shows(make_week):
    make_week(1, games=6, future=True)
    assert wrapped.week_publish_state(SEASON, 1)[0] == "pending"
    assert wrapped.week_menu(SEASON, include_preview=True) == []


def test_stuck_game_publishes_after_fallback(make_week, make_entry, submit):
    # make_week dates deadlines years in the past, so the fallback window has
    # long since run out even though one game never went final.
    week = make_week(1, games=6)
    submit(make_entry("alice"), week, 5)
    _finish(week)
    g = Game.query.filter_by(week_id=week.id).first()
    g.is_final = False
    db.session.commit()
    assert wrapped.week_publish_state(SEASON, 1)[0] == "published"


def test_building_the_week_writes_nothing(make_week, make_entry, submit):
    week = make_week(1, games=6)
    for name in ("alice", "bob", "carol"):
        submit(make_entry(name), week, 5)
    _finish(week)
    before = _counts()
    data = wrapped.league_week(SEASON, 1, use_cache=False)
    assert data["gridiron"]["players"] == 3
    assert data["gridiron"]["popular"]["count"] == 3
    assert not db.session.new and not db.session.dirty and not db.session.deleted
    assert _counts() == before


def _walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


def test_cached_week_holds_no_database_objects(make_week, make_entry, submit, team):
    """league_week is cached across requests. A database object in it breaks
    the NEXT visitor's page (DetachedInstanceError) -- the first live 500."""
    wrapped._cache.clear()
    week = make_week(1, games=6)
    submit(make_entry("alice"), week, 5)
    _finish(week)
    bills = team("Buffalo", "Bills")
    for pool in ("dropdead", "loser"):
        w = make_week(1, games=0, pool=pool)
        g = Game(week_id=w.id, pool=pool, sport="nfl", home_team="Buffalo Bills",
                 away_team="Away X", home_team_id=bills.id, home_score=20,
                 away_score=10, is_final=True)
        db.session.add(g)
        e = make_entry(f"{pool}-bob", pool=pool)
        db.session.add(Pick(entry_id=e.id, week_id=w.id, pool=pool, team_id=bills.id,
                            result="win", points=1))
    db.session.commit()

    data = wrapped.league_week(SEASON, 1)
    assert not [o for o in _walk(data) if isinstance(o, db.Model)]

    # A later request: new session, data served from the cache.
    db.session.remove()
    cached = wrapped.league_week(SEASON, 1)
    for name in ("dropdead-bob", "loser-bob"):
        user = User.query.filter_by(username=name).first()
        mine = wrapped.your_week(SEASON, cached, user)
        assert mine and next(iter(mine.values()))["chip"]["name"] == "Bills"
