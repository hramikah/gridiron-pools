import os
import threading
import time
from collections import defaultdict
from functools import wraps

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort, current_app, request
from flask_login import current_user

from models import POOLS, PRESEASON_OFFSET, ActivityLog, ContactMessage, Game, Setting, Week, db
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
    anywhere else that is too narrow for the full label: 'Wk5', or 'Pre 2' for
    a preseason week (stored offset past the regular-season numbers)."""
    if number is None:
        return ""
    return f"Pre {number - PRESEASON_OFFSET}" if number > PRESEASON_OFFSET else f"Wk{number}"


def week_label(number):
    """Full label for a bare week number: 'Week 5', or 'Preseason Week 2'.

    Preseason weeks are stored as PRESEASON_OFFSET + N so they sort after the
    regular season and never collide with it, but that number is an internal
    detail -- a player should never be shown "Week 101". Week.label does this
    when the row itself is to hand; this is for the many places that carry
    only a number (entry.eliminated_week, the history-week picker, the admin
    week dropdowns).
    """
    if number is None:
        return ""
    return (
        f"Preseason Week {number - PRESEASON_OFFSET}"
        if number > PRESEASON_OFFSET
        else f"Week {number}"
    )


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


# A week opens for picking on the Thursday before its Saturday-noon deadline,
# which is also when its betting week starts (Thursday -> the next Wednesday).
WEEK_OPENS_DAYS_BEFORE_DEADLINE = 2


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

    # A week becomes "the current week" when its own betting window opens --
    # the Thursday before its Saturday-noon deadline -- not the instant the
    # previous deadline passes. Without this, at 12:01 on Saturday the pick
    # pages jumped to the next scheduled week (possibly a fortnight out) and
    # the picks players had just locked in vanished from view.
    window_open = n + timedelta(days=WEEK_OPENS_DAYS_BEFORE_DEADLINE)
    upcoming = (
        Week.query.filter(
            Week.season_year == season_year,
            Week.pool == pool,
            Week.pick_deadline >= n,
            Week.pick_deadline <= window_open,
        )
        .order_by(Week.pick_deadline.asc())
        .first()
    )
    if upcoming:
        return upcoming

    # Nothing open to pick. Stay on the most recently closed week so players
    # keep seeing what they locked in, rather than an empty future week.
    just_closed = (
        Week.query.filter(
            Week.season_year == season_year,
            Week.pool == pool,
            Week.pick_deadline < n,
        )
        .order_by(Week.pick_deadline.desc())
        .first()
    )
    if just_closed:
        return just_closed

    # Nothing has closed yet either: show the next week on the schedule, so a
    # brand-new season still has something to display.
    nxt = (
        Week.query.filter(Week.season_year == season_year, Week.pool == pool, Week.pick_deadline >= n)
        .order_by(Week.pick_deadline.asc())
        .first()
    )
    if nxt:
        return nxt
    return Week.query.filter_by(season_year=season_year, pool=pool).order_by(Week.number.desc()).first()


def pool_signup_deadline(season_year, pool):
    """When a pool closes to new entries: its **Week 1** pick deadline
    (Saturday noon Eastern of regular-season week 1).

    Every pool works the same way. An entry created after week 1 has closed
    starts the season a week down with no way to make it up -- Gridiron lost
    the buy-back that used to rescue that, and Drop Dead and Loser never had
    one, so nobody may join once the first week is locked.

    Prefers the pool's real Week 1 row, so an admin who moves that deadline
    moves the signup cutoff with it. Falls back to deriving it from the
    configured season start, so the cutoff is well-defined before Week 1 has
    been published. Returns None only if neither is available, which leaves
    signups open.
    """
    week1 = Week.query.filter_by(season_year=season_year, number=1, pool=pool).first()
    if week1:
        return week1.pick_deadline

    season_start_str = get_setting("season_start_thursday")
    if not season_start_str:
        return None
    try:
        season_start = datetime.fromisoformat(season_start_str).date()
    except ValueError:
        return None
    # Week 1's deadline is the Saturday after the season-start Thursday.
    week1_saturday = season_start + timedelta(days=2)
    return datetime.combine(week1_saturday, datetime.min.time()) + timedelta(hours=12)


def pool_signups_open(season_year, pool):
    """True while new entries in this pool are still allowed."""
    cutoff = pool_signup_deadline(season_year, pool)
    return cutoff is None or now_eastern() <= cutoff


# Gridiron-specific names kept because the templates, the join route and the
# tests have always used them.
def gridiron_signup_deadline(season_year):
    return pool_signup_deadline(season_year, "gridiron")


def gridiron_signups_open(season_year):
    return pool_signups_open(season_year, "gridiron")


def any_pool_signups_open(season_year):
    """True while at least one pool will still take a new entry.

    Once this is False there is nothing a brand-new account could join, so the
    "Add another account" route refuses rather than creating an account that
    lands on a join page with every button gone.
    """
    return any(pool_signups_open(season_year, p) for p in POOLS)


def week_sort_key(week):
    """Preseason weeks first, then the regular season, each ascending.

    Preseason week N is stored as PRESEASON_OFFSET + N, so ordering on the raw
    number drops preseason (101, 102...) below Week 18 -- which is how the
    Weekly Picks page ended up listing them at the bottom of every pool.

    is_preseason alone isn't enough: only the auto-publisher sets it, so a
    hand-created week numbered 101+ carries the flag as False. A week in the
    preseason number range belongs with the preseason whatever its flag says.
    """
    return (0 if (week.is_preseason or week.number >= PRESEASON_OFFSET) else 1,
            week.number)


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


def game_started(game):
    """True once this game's kickoff has actually been reached.

    game_pickable() goes False a full hour BEFORE kickoff, so it cannot tell
    "starting shortly" from "finished on Thursday". The pick pages showed
    "Kickoff too soon" on games that had been over for days because that one
    flag was carrying both meanings.
    """
    return game is not None and game.kickoff is not None and now_eastern() >= game.kickoff


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


def _read_cache():
    """Per-request memo, used only on read-only (GET/HEAD) requests.

    Standings asks the same handful of questions thousands of times while
    building the page -- the same entry's first missed week, the same week's
    game list, the same Setting row. Nothing writes to the database during a
    GET, so answering from a dict that lives for one request is safe and
    cannot go stale. Outside a request (scripts, tests, the seeders) this
    returns None and every caller falls through to a real query, so behaviour
    there is exactly what it was.
    """
    try:
        from flask import g, has_request_context, request
        if not has_request_context() or request.method not in ("GET", "HEAD"):
            return None
        if not hasattr(g, "_gp_read_cache"):
            g._gp_read_cache = {}
        return g._gp_read_cache
    except Exception:
        return None


def get_setting(key, default=None):
    cache = _read_cache()
    if cache is not None:
        ck = ("setting", key)
        if ck in cache:
            row = cache[ck]
            return row.value if row else default
        row = db.session.get(Setting, key)
        cache[ck] = row
        return row.value if row else default
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
