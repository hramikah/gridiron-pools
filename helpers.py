import threading
from functools import wraps

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, current_app
from flask_login import current_user

from models import Game, Setting, Week, db

EASTERN = ZoneInfo("America/New_York")


def now_eastern():
    """Naive datetime representing the current time in US Eastern (EST/EDT).

    The pool rules peg every deadline to Eastern time explicitly ("Saturday
    at noon EST"), so all Week.pick_deadline / Game.kickoff values are stored
    as naive Eastern-local datetimes and compared against this.
    """
    return datetime.now(EASTERN).replace(tzinfo=None)


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)

    return wrapper


def get_current_week(season_year, pool):
    """The active week players see for this pool.

    If an admin has pinned the current week (the ``active_week`` setting), that
    week number wins for every pool. Otherwise it auto-detects: the next week
    whose deadline hasn't passed, else the pool's most recent week."""
    override = get_setting("active_week")
    if override:
        try:
            num = int(override)
        except (TypeError, ValueError):
            num = None
        if num is not None:
            pinned = Week.query.filter_by(season_year=season_year, number=num, pool=pool).first()
            if pinned:
                return pinned

    n = now_eastern()
    upcoming = (
        Week.query.filter(Week.season_year == season_year, Week.pool == pool, Week.pick_deadline >= n)
        .order_by(Week.pick_deadline.asc())
        .first()
    )
    if upcoming:
        return upcoming
    return Week.query.filter_by(season_year=season_year, pool=pool).order_by(Week.number.desc()).first()


def deadline_passed(week):
    if week is None:
        return True
    return now_eastern() > week.pick_deadline


def week_is_complete(week):
    """True once every NFL/college game posted for this week has a final
    score. Win/loss coloring only shows once this is true, so nobody sees a
    partial, mid-week picture."""
    if week is None:
        return False
    games = Game.query.filter_by(week_id=week.id).all()
    return bool(games) and all(g.is_final for g in games)


def game_pickable(game):
    """A game can be picked until 1 hour before its own kickoff, even if the
    week's overall Saturday-noon deadline hasn't hit yet -- per the printed
    rules, this applies to every pool, not just Gridiron."""
    if game is None:
        return True
    return game.kickoff is None or now_eastern() < game.kickoff - timedelta(hours=1)


def team_game_this_week(team_id, week_id, pool=None):
    """The Game a team plays in for a given week, or None (e.g. a bye).
    Pass ``pool`` to resolve within that pool's own lines."""
    q = Game.query.filter(
        Game.week_id == week_id,
        db.or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
    )
    if pool is not None:
        q = q.filter(Game.pool == pool)
    return q.first()


def week_unlocked(week):
    """A week's picks/results become visible to everyone -- including other
    players' picks -- strictly once its real pick deadline has passed. Not
    gated on week_is_complete: a mock/simulated week with a future-dated
    deadline must not leak picks early just because its games are already
    scored."""
    return deadline_passed(week)


def send_async(fn, *args):
    """Run a mailer/notification call on a background thread with its own
    app context, so the request that triggered it (saving a pick, posting
    an announcement) returns immediately instead of waiting on SMTP."""
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            fn(*args)

    threading.Thread(target=run, daemon=True).start()


def get_setting(key, default=None):
    row = db.session.get(Setting, key)
    return row.value if row else default


def set_setting(key, value):
    row = db.session.get(Setting, key)
    if row:
        row.value = value
    else:
        db.session.add(Setting(key=key, value=value))
    db.session.commit()
