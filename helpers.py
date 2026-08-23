import os
import threading
import time
from collections import defaultdict
from functools import wraps

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, current_app, request
from flask_login import current_user

from models import PRESEASON_OFFSET, ActivityLog, ContactMessage, Game, Setting, Week, db
from testbed_guard import TESTBED_MARKER

EASTERN = ZoneInfo("America/New_York")

# Login rate limiting: a small in-memory counter (fine for a single-process
# app) keyed by the real client IP -- CF-Connecting-IP is set by Cloudflare
# itself on every request through the tunnel and can't be spoofed by the
# client, unlike a plain X-Forwarded-For chain.
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300
_login_attempts = defaultdict(list)
_login_attempts_lock = threading.Lock()

RESET_MAX_REQUESTS = 5
RESET_WINDOW_SECONDS = 900
_reset_requests = defaultdict(list)
_reset_requests_lock = threading.Lock()


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


def reset_request_rate_limited():
    """Throttle the "forgot password" form per IP. Kept separate from the
    login counter so asking for a reset never eats into (or clears) the
    failed-login budget for the same address."""
    ip = _client_ip()
    now = time.time()
    with _reset_requests_lock:
        attempts = _reset_requests[ip]
        attempts[:] = [t for t in attempts if now - t < RESET_WINDOW_SECONDS]
        if len(attempts) >= RESET_MAX_REQUESTS:
            return True
        attempts.append(now)
        return False


TESTBED_CLOCK_SETTING = "testbed_fake_now"


def _testbed_clock():
    """The frozen clock a testbed database has asked for, or None.

    Only ever consulted when GRIDIRON_DATABASE_URI marks the database as a
    testbed one -- the same marker testbed_guard.py checks -- so the live
    site can't be talked into a fake clock and never pays for the lookup.

    Scenario seeds need the site to believe it is a particular moment: "week
    1 is scored, week 2 is open, it is Thursday afternoon and the buy-back
    shuts at 7." Dating the rows relative to the real clock can't do that --
    every day name on the page comes out wrong -- so the clock moves instead.
    """
    if TESTBED_MARKER not in os.environ.get("GRIDIRON_DATABASE_URI", "").lower():
        return None
    try:
        raw = get_setting(TESTBED_CLOCK_SETTING)
        return datetime.fromisoformat(raw) if raw else None
    except Exception:
        # No app context, no Setting table yet, or a value someone typed by
        # hand: fall through to the real clock rather than break the page.
        return None


def now_eastern():
    """Naive datetime representing the current time in US Eastern (EST/EDT).

    The pool rules peg every deadline to Eastern time explicitly ("Saturday
    at noon EST"), so all Week.pick_deadline / Game.kickoff values are stored
    as naive Eastern-local datetimes and compared against this.
    """
    frozen = _testbed_clock()
    if frozen is not None:
        return frozen
    return datetime.now(EASTERN).replace(tzinfo=None)


def log_activity(action, detail=None, pool=None, user=None):
    """Record one thing a player did. Never raises: an audit-trail failure must
    not take down the action being audited."""
    try:
        actor = user or (current_user if getattr(current_user, "is_authenticated", False) else None)
        db.session.add(
            ActivityLog(
                user_id=getattr(actor, "id", None),
                username=getattr(actor, "username", None),
                action=action,
                pool=pool,
                detail=detail,
                ip=_client_ip() if request else None,
            )
        )
        db.session.commit()
    except Exception:  # pragma: no cover - logging must never break a request
        db.session.rollback()


def unread_message_count(user):
    """Messages waiting for this user on the message board.

    Admins are the other side of every player's thread, so they count unread
    player-authored messages across all threads; a player counts unread admin
    replies in their own thread only."""
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    if user.is_admin:
        # Player-authored messages only, and never the admin's own thread --
        # a note an admin sends themselves through the board would otherwise
        # count forever, since nothing on either side ever marks it read.
        return sum(
            1
            for m in ContactMessage.query.filter_by(is_read=False).all()
            if not m.from_admin and m.user_id != user.id
        )
    return sum(
        1
        for m in ContactMessage.query.filter_by(user_id=user.id, is_read=False).all()
        if m.from_admin
    )


def short_week_label(number):
    """Compact label for a bare week number, for standings-matrix headers and
    anywhere else that only carries the number: 'Wk5', or 'PS2' for a
    preseason week (stored offset past the regular-season numbers)."""
    return f"PS{number - PRESEASON_OFFSET}" if number > PRESEASON_OFFSET else f"Wk{number}"


def deadline_epoch_ms(week):
    """The week's pick deadline as a real epoch-millisecond timestamp.

    Deadlines are stored naive-Eastern, so a countdown in the browser can't
    just parse them -- a player in another time zone (or on a machine with a
    skewed clock) would count down to the wrong instant. Stamping the true UTC
    epoch here lets the client subtract it from its own Date.now() and get the
    same remaining time everywhere, DST included."""
    if week is None or week.pick_deadline is None:
        return None
    return int(week.pick_deadline.replace(tzinfo=EASTERN).timestamp() * 1000)


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


def gridiron_signup_deadline(season_year):
    """When Gridiron Investments closes to new entries: the Week 2 pick
    deadline (Saturday noon Eastern of regular-season week 2).

    Prefers the real Week 2 row so an admin who moves that deadline moves the
    signup cutoff with it. Falls back to deriving it from the configured season
    start, so the cutoff is well-defined even before Week 2 has been published.
    Returns None only if neither is available, which leaves signups open."""
    week2 = Week.query.filter_by(season_year=season_year, number=2, pool="gridiron").first()
    if week2:
        return week2.pick_deadline

    season_start_str = get_setting("season_start_thursday")
    if not season_start_str:
        return None
    try:
        season_start = datetime.fromisoformat(season_start_str).date()
    except ValueError:
        return None
    # Week 2's Thursday is a week after Week 1's; its deadline is that
    # Saturday at noon.
    week2_saturday = season_start + timedelta(weeks=1, days=2)
    return datetime.combine(week2_saturday, datetime.min.time()) + timedelta(hours=12)


def gridiron_signups_open(season_year):
    """True while new Gridiron entries are still allowed."""
    cutoff = gridiron_signup_deadline(season_year)
    return cutoff is None or now_eastern() <= cutoff


def deadline_passed(week):
    if week is None:
        return True
    return now_eastern() > week.pick_deadline


# The Gridiron buy-back closes earlier than the week it is taken in: 7:00 PM
# Eastern on that week's Thursday, before Thursday night kicks off (~8:20).
# The week's own pick deadline is Saturday noon, which would leave the offer
# standing after two nights of football had already been played.
GRIDIRON_BUYBACK_CUTOFF_HOUR = 19


def gridiron_buyback_deadline(week):
    """When the $100 Gridiron buy-back closes for this week, or None.

    Derived from the week's own pick deadline rather than the configured
    season start, so an admin who moves a week moves this with it: walk back
    to the Thursday on or before that deadline, then set 7:00 PM. With the
    normal Saturday-noon deadline that lands on the Thursday two days before,
    which is the Thursday the betting week opens on.
    """
    if week is None or week.pick_deadline is None:
        return None
    # Monday is 0, so Thursday is 3. Walking back (weekday - 3) % 7 days lands
    # on the Thursday of that same betting week, or the deadline's own day
    # when the deadline is itself a Thursday.
    days_back = (week.pick_deadline.weekday() - 3) % 7
    thursday = (week.pick_deadline - timedelta(days=days_back)).date()
    return datetime.combine(thursday, datetime.min.time()) + timedelta(
        hours=GRIDIRON_BUYBACK_CUTOFF_HOUR
    )


def gridiron_buyback_closed(week):
    """True once the buy-back window for this week has shut."""
    cutoff = gridiron_buyback_deadline(week)
    return cutoff is None or now_eastern() > cutoff


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


def _format_spread(value):
    """7.0 -> '7', 7.5 -> '7.5' -- drop the trailing .0 non-half-point
    spreads pick up from being stored as floats."""
    return str(int(value)) if value == int(value) else str(value)


def _team_line(game, side):
    """This team's own spread notation, e.g. '-8', '+7.5', or 'PK'."""
    if game.favorite is None:
        return "PK"
    formatted = _format_spread(game.spread)
    return f"-{formatted}" if game.favorite == side else f"+{formatted}"


def team_matchups_for_week(week_id, pool):
    """Map team_id -> a full matchup description ('Away Team +8 @ Home
    Team -8 — Sun Aug 15, 01:00 PM Eastern') for every team with a game in
    this pool's week, so a plain team-name dropdown (Drop Dead, Loser) can
    show the spread and kickoff context for whichever team is picked, not
    just the bare team name -- and the same string is reused once a pick is
    locked in, so that detail doesn't disappear after saving."""
    matchups = {}
    for g in Game.query.filter_by(week_id=week_id, pool=pool).all():
        away_line = _team_line(g, "away")
        home_line = _team_line(g, "home")
        text = f"{g.away_team} {away_line} @ {g.home_team} {home_line}"
        if g.kickoff:
            text += f" — {g.kickoff.strftime('%a %b %d, %I:%M %p')} Eastern"
        if g.away_team_id:
            matchups[g.away_team_id] = text
        if g.home_team_id:
            matchups[g.home_team_id] = text
    return matchups


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


# One definition of how a graded pick is coloured, used by every view that
# prints a result as text rather than as a badge (the badge styles live in
# style.css as .pick-win / .pick-loss / .pick-push).
#
# This map used to be written out separately in blueprints/main.py and in
# templates/admin/pool_week.html. When pushes moved from grey to purple, one
# copy was updated and the other wasn't, so the Weekly Picks report went on
# rendering pushes in the same muted grey as an ungraded pick -- which is the
# exact confusion the colour change was meant to end. Registered as a Jinja
# global in app.py so templates don't need it passed in.
RESULT_CLASS = {
    "win": "text-success fw-bold",
    "loss": "text-danger fw-bold",
    "push": "text-push fw-bold",
}
