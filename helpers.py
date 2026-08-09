import threading
import time
from collections import defaultdict
from functools import wraps

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, current_app, request
from flask_login import current_user

from models import Game, Setting, Week, db

EASTERN = ZoneInfo("America/New_York")

# Login rate limiting: a small in-memory counter (fine for a single-process
# app) keyed by the real client IP -- CF-Connecting-IP is set by Cloudflare
# itself on every request through the tunnel and can't be spoofed by the
# client, unlike a plain X-Forwarded-For chain.
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300
_login_attempts = defaultdict(list)
_login_attempts_lock = threading.Lock()


def _client_ip():
    return request.headers.get("CF-Connecting-IP", request.remote_addr)


def login_rate_limited():
    ip = _client_ip()
    now = time.time()
    with _login_attempts_lock:
        attempts = _login_attempts[ip]
        attempts[:] = [t for t in attempts if now - t < LOGIN_WINDOW_SECONDS]
        return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_failed_login():
    ip = _client_ip()
    with _login_attempts_lock:
        _login_attempts[ip].append(time.time())


def clear_login_attempts():
    ip = _client_ip()
    with _login_attempts_lock:
        _login_attempts.pop(ip, None)


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
    """The next week (for this pool) whose deadline hasn't passed, else the
    pool's most recent week. Each pool keeps its own weeks and deadlines."""
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
