import importlib
import os
import re
from datetime import datetime, timedelta

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError

from helpers import (
    RESULT_CLASS,
    _testbed_clock,
    any_pool_signups_open,
    deadline_epoch_ms,
    game_started,
    get_setting,
    short_week_label,
    week_label,
    unread_message_count,
    week_is_complete,
)
from models import Invite, User, db, name_order
from testbed_guard import TESTBED_MARKER

csrf = CSRFProtect()

# Auto-logout after this long with no requests, enforced server-side and
# independent of the session cookie's own lifetime (which stays a
# browser-session cookie -- no Expires/Max-Age -- so it's still cleared on
# browser close for browsers that don't restore their previous session).
INACTIVITY_TIMEOUT = timedelta(minutes=30)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def get_or_create_secret_key():
    path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    key = os.urandom(32)
    with open(path, "wb") as f:
        f.write(key)
    return key


def is_local_test():
    """True when this is the practice copy on a laptop rather than the live
    site, so the templates can shout about it. Keyed on the hostname being
    localhost: the live site is only ever reached through Cloudflare at the
    real domain, so it can never match."""
    host = (request.host or "").split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def migrate_schema():
    """Add columns that create_all() can't add to tables it already made.

    create_all() only ever creates missing *tables*, so a new column on an
    existing model is invisible to it and every query then blows up on
    "no such column". Each step is idempotent, so this is safe on every boot.
    """
    cols = {row[1] for row in db.session.execute(db.text("PRAGMA table_info(week)")).all()}
    if "buyback_open" not in cols:
        db.session.execute(db.text("ALTER TABLE week ADD COLUMN buyback_open BOOLEAN DEFAULT 0 NOT NULL"))
        # Backfill what the printed rules already granted: Drop Dead weeks
        # 1-4 of a real season. Preseason weeks (101+) are left closed --
        # that's the case the admin now controls by hand.
        db.session.execute(db.text(
            "UPDATE week SET buyback_open = 1 WHERE pool = 'dropdead' AND number <= 4"
        ))
        db.session.commit()

    cols = {row[1] for row in db.session.execute(db.text("PRAGMA table_info(entry)")).all()}
    if "buy_backs_paid" not in cols:
        # Buy-backs were recorded but never billed: the payments page only
        # ever counted entry fees, so a revived entry showed nothing owed for
        # the $30 (or $100) it had just cost. Existing buy-backs default to
        # unpaid, which is the safe direction -- an admin can mark them paid,
        # and the alternative silently forgives money already collected on
        # paper but not in the app.
        db.session.execute(db.text("ALTER TABLE entry ADD COLUMN buy_backs_paid INTEGER DEFAULT 0"))
        db.session.execute(db.text("UPDATE entry SET buy_backs_paid = 0 WHERE buy_backs_paid IS NULL"))
        db.session.commit()

    # When each fee was settled. Players see this on their own Billing page, so
    # the date has to be recorded rather than inferred. Rows settled before this
    # existed keep NULL and are shown without a date rather than with a wrong
    # one.
    cols = {row[1] for row in db.session.execute(db.text("PRAGMA table_info(entry)")).all()}
    if "paid_at" not in cols:
        db.session.execute(db.text("ALTER TABLE entry ADD COLUMN paid_at DATETIME"))
        db.session.commit()
    if "buy_backs_paid_at" not in cols:
        db.session.execute(db.text("ALTER TABLE entry ADD COLUMN buy_backs_paid_at DATETIME"))
        db.session.commit()


    # Every address may hold 5 accounts now (a team plus four more), where the
    # column default used to be 1. Existing rows keep whatever they were given,
    # so they need raising once -- but only once: an admin who later drops
    # someone to 1 must not have it undone on the next boot. The marker setting
    # is what makes that "once" stick.
    try:
        done = db.session.execute(
            db.text("SELECT value FROM setting WHERE key = 'max_teams_default5'")
        ).scalar()
    except Exception:
        done = None
    if not done:
        db.session.execute(db.text("UPDATE user SET max_teams = 5 WHERE max_teams < 5"))
        db.session.execute(db.text(
            "INSERT INTO setting (key, value) VALUES ('max_teams_default5', 'done') "
            "ON CONFLICT(key) DO UPDATE SET value = 'done'"
        ))
        db.session.commit()

    # Each Drop Dead buy-back becomes its own row, so the Payments page can
    # settle exactly the one that was paid instead of moving a count. The old
    # counters said only "3 taken, 1 paid", so the backfill marks the first
    # `buy_backs_paid` of each entry's buy-backs as the settled ones -- the
    # same convention the counter itself implied.
    tables = {
        row[0]
        for row in db.session.execute(
            db.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).all()
    }
    if "buy_back" in tables:
        already = db.session.execute(db.text("SELECT COUNT(*) FROM buy_back")).scalar()
        if not already:
            rows = db.session.execute(db.text(
                "SELECT id, buy_backs_used, buy_backs_paid, buy_backs_paid_at, buyback_week "
                "FROM entry WHERE pool = 'dropdead' AND buy_backs_used > 0"
            )).all()
            for entry_id, used, paid, paid_at, week_number in rows:
                paid = max(0, min(paid or 0, used or 0))
                for i in range(used or 0):
                    db.session.execute(db.text(
                        "INSERT INTO buy_back (entry_id, week_number, fee, paid, paid_at, created_at) "
                        "VALUES (:e, :w, 30, :p, :pa, :ca)"
                    ), {
                        "e": entry_id,
                        # Only the most recent buy-back's week was ever kept.
                        "w": week_number if i == (used or 0) - 1 else None,
                        "p": 1 if i < paid else 0,
                        "pa": paid_at if i < paid else None,
                        "ca": None,
                    })
            if rows:
                db.session.commit()

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_or_create_secret_key()
    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
    # GRIDIRON_DATABASE_URI lets a test run point at a throwaway database.
    # Flask-SQLAlchemy binds its engine when init_app runs, so overriding
    # app.config afterwards silently does nothing and the test ends up
    # writing to instance/pools.db -- which is how a test once wiped it.
    # The override has to happen here, before init_app, to take effect.
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        os.environ.get("GRIDIRON_DATABASE_URI")
        or f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'pools.db')}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["CURRENT_SEASON"] = int(os.environ.get("SEASON_YEAR", 2026))

    # Cookie hardening: HttpOnly blocks JS from reading the session cookie
    # (XSS mitigation), Secure means it's never sent over plain HTTP, and
    # Lax stops it being sent on cross-site POSTs (CSRF mitigation, on top
    # of the CSRFProtect tokens below). This app is only ever reached over
    # HTTPS (Cloudflare terminates TLS in front of it), so Secure is safe.
    # Flask-WTF expires CSRF tokens after an hour by default, which on a
    # phone means any tab left open past that logs you out with a cryptic
    # 400. The token is stored in the session, and the session is already
    # bounded by the 30-minute inactivity logout above, so tying the token's
    # life to the session's is no weaker and far less confusing.
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # Secure means the browser only ever sends the cookie over HTTPS, which is
    # right for the live site (Cloudflare terminates TLS in front of it) and
    # wrong for the local test site, which is plain http on 127.0.0.1. Chrome
    # makes an exception for localhost; Safari and others do not, and there the
    # cookie is silently dropped -- every form then posts a CSRF token with no
    # session behind it and the sign-in "fails" with no useful explanation.
    # Keyed on the database being a testbed one, the same marker the frozen
    # clock and the seed guard use, so the live site can never take this branch.
    is_testbed = TESTBED_MARKER in (app.config["SQLALCHEMY_DATABASE_URI"] or "").lower()
    app.config["SESSION_COOKIE_SECURE"] = not is_testbed
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    db.init_app(app)
    csrf.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from blueprints.admin import bp as admin_bp
    from blueprints.auth import bp as auth_bp
    from blueprints.board import bp as board_bp
    from blueprints.dropdead import bp as dropdead_bp
    from blueprints.gridiron import bp as gridiron_bp
    from blueprints.loser import bp as loser_bp
    from blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(dropdead_bp, url_prefix="/dropdead")
    app.register_blueprint(loser_bp, url_prefix="/loser")
    app.register_blueprint(gridiron_bp, url_prefix="/gridiron")
    app.register_blueprint(board_bp, url_prefix="/board")

    @app.context_processor
    def inject_globals():
        other_accounts = []
        can_add_account = False
        if current_user.is_authenticated:
            other_accounts = (
                User.query.filter(User.email == current_user.email, User.id != current_user.id)
                .order_by(name_order(User.username))
                .all()
            )
            same_email_accounts = other_accounts + [current_user]
            limit = max((u.max_teams for u in same_email_accounts), default=1)
            # Hidden once every pool has closed to new entries: a fresh account
            # could not join anything, so offering it is a dead end. The route
            # itself refuses too, so a bookmarked link is no way around it.
            can_add_account = (
                len(same_email_accounts) < limit
                and any_pool_signups_open(app.config["CURRENT_SEASON"])
            )

        # The "Register" nav link is invite-only advertising: only show it to
        # a visitor who has actually arrived via a valid, unused invite link
        # (remembered in their session), never to a random logged-out visitor.
        has_valid_invite = User.query.count() == 0  # fresh install: first admin needs to find it too
        invite_token = session.get("invite_token")
        if not has_valid_invite and invite_token:
            invite_row = Invite.query.filter_by(token=invite_token).first()
            has_valid_invite = bool(invite_row and invite_row.used_at is None)

        # Popup announcement: shown once per new announcement, the next page
        # load after a player is logged in. "Seen" is tracked in the session
        # (not the DB) keyed to the announcement's id, so a fresh one from
        # the admin always resets everyone's "have I seen this" state.
        popup_announcement = None
        if current_user.is_authenticated:
            ann_text = get_setting("popup_announcement")
            ann_id = get_setting("popup_announcement_id")
            if ann_text and ann_id and session.get("seen_popup_id") != ann_id:
                popup_announcement = ann_text
                session["seen_popup_id"] = ann_id

        return {
            "current_user": current_user,
            "season_year": app.config["CURRENT_SEASON"],
            "other_accounts": other_accounts,
            "can_add_account": can_add_account,
            "has_valid_invite": has_valid_invite,
            "popup_announcement": popup_announcement,
            "unread_messages": unread_message_count(current_user),
            "is_local_test": is_local_test(),
            # Set only while a testbed database has asked for a frozen
            # clock, so the simulation banner in base.html appears and
            # disappears with the demo rather than needing a code change.
            "frozen_clock": _testbed_clock(),
        }

    app.jinja_env.globals["week_is_complete"] = week_is_complete
    app.jinja_env.globals["game_started"] = game_started
    app.jinja_env.globals["deadline_epoch_ms"] = deadline_epoch_ms
    app.jinja_env.globals["short_week_label"] = short_week_label
    # Bare week numbers are shown through this so a preseason week never
    # surfaces as "Week 101" -- see helpers.week_label.
    app.jinja_env.globals["week_label"] = week_label
    # Every view that colours a result as text reads this one map, so a
    # colour change can't reach some pages and miss others.
    app.jinja_env.globals["result_class"] = RESULT_CLASS

    def ordinal(n):
        """1 -> 1st, 2 -> 2nd, 11 -> 11th. Used for standings places."""
        try:
            n = int(n)
        except (TypeError, ValueError):
            return n
        if 10 <= n % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    app.jinja_env.globals["ordinal"] = ordinal

    # Cache-buster for static files. style.css is served with a long cache
    # life, so a CSS fix could sit invisible on a player's phone until they
    # cleared their browser. Stamping the file's own mtime into the URL means
    # a deploy changes the URL and the new file is fetched.
    def asset_v(filename):
        try:
            return int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            return 0

    app.jinja_env.globals["asset_v"] = asset_v

    # Site-wide login gate: nothing is visible to a logged-out visitor except
    # the login/register/password-reset pages themselves and static assets.
    PUBLIC_ENDPOINTS = {
        "auth.login",
        "auth.register",
        "auth.forgot_password",
        "auth.reset_password",
        "static",
    }

    @app.before_request
    def force_https():
        """Send plain-HTTP visitors to HTTPS before anything else runs.

        The session cookie is Secure, so over http the browser accepts the
        page but throws the cookie away -- every form then POSTs a CSRF
        token with no session behind it and dies on "The CSRF session token
        is missing". Phones hit this constantly, since typing a bare domain
        still tries http first.

        Only redirects when Cloudflare explicitly reports the visitor came
        in over http; with no such header (local dev, LAN access by IP)
        nothing changes, so plain http still works there.
        """
        forwarded = request.headers.get("X-Forwarded-Proto", "").lower()
        if not forwarded:
            visitor = request.headers.get("CF-Visitor", "")
            if '"scheme":"http"' in visitor.replace(" ", ""):
                forwarded = "http"
        if forwarded == "http":
            return redirect(request.url.replace("http://", "https://", 1), code=301)
        return None

    @app.before_request
    def enforce_inactivity_timeout():
        if not current_user.is_authenticated:
            return None
        now = datetime.utcnow()
        last_seen_raw = session.get("_last_seen")
        if last_seen_raw:
            last_seen = datetime.fromisoformat(last_seen_raw)
            if now - last_seen > INACTIVITY_TIMEOUT:
                logout_user()
                session.clear()
                flash("You were logged out after 30 minutes of inactivity.", "error")
                return redirect(url_for("auth.login"))
        session["_last_seen"] = now.isoformat()
        return None

    @app.before_request
    def require_login():
        if current_user.is_authenticated or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        return redirect(url_for("auth.login", next=request.path))

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        """A stale token is a normal thing to hit -- a page left open on a
        phone overnight, or a browser that dropped the session cookie --
        so say so in plain language on the page they were on instead of
        showing Flask's raw "400 Bad Request: The CSRF token is missing"."""
        flash("That form couldn't be submitted, usually because you signed out "
              "(or the page was left open) in another tab. Please sign in and "
              "try again.", "error")
        if current_user.is_authenticated:
            return redirect(url_for("main.index")), 302
        return redirect(url_for("auth.login")), 302

    @app.errorhandler(403)
    def forbidden(e):
        message = e.description if getattr(e, "description", None) else "You don't have access to that page."
        return render_template("error.html", message=message), 403

    with app.app_context():
        db.create_all()
        migrate_schema()

    return app


def run_requested_testbed_seed(app):
    """Run a seed script that a marker file asks for, then disarm.

    Whoever is maintaining this site remotely can write files into the folder
    but has no shell on the machine the test site runs on, so there is
    otherwise no way to load a scenario without the operator typing a
    command. Dropping a file at testbed/SEED_ME containing a script name asks
    the next reload to run it -- and the live-reload runner restarts whenever
    a .py file changes, so writing the seed script is itself the trigger.

    The marker is deleted before the script runs, so a seed that fails does
    so once instead of on every restart. Only a module name is accepted, and
    only from this directory: no paths, no arguments, nothing to point at
    another part of the disk.

    Testbed databases only, checked the same way testbed_guard.py checks --
    on the live site this returns before it looks at the filesystem.
    """
    if TESTBED_MARKER not in (app.config.get("SQLALCHEMY_DATABASE_URI") or "").lower():
        return
    here = os.path.dirname(os.path.abspath(__file__))
    marker = os.path.join(here, "testbed", "SEED_ME")
    if not os.path.exists(marker):
        return

    try:
        with open(marker) as fh:
            name = fh.read().strip()
    finally:
        os.remove(marker)

    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", name or ""):
        print(f"[testbed] ignoring SEED_ME: {name!r} is not a script name")
        return
    if not os.path.exists(os.path.join(here, f"{name}.py")):
        print(f"[testbed] ignoring SEED_ME: {name}.py is not in this folder")
        return

    print(f"[testbed] running {name}.py ...")
    try:
        importlib.import_module(name).main()
        print(f"[testbed] {name}.py finished. Refresh the browser.")
    except Exception as exc:  # a bad seed must not take the site down
        print(f"[testbed] {name}.py failed: {exc!r}")


app = create_app()
run_requested_testbed_seed(app)

# On a testbed database only, recompile templates whenever they change on disk
# so an edit shows up on the next browser refresh instead of needing the server
# restarted. The live site never takes this branch -- its database path has no
# "testbed" in it -- so it keeps the compiled-once behaviour and the speed.
if TESTBED_MARKER in os.environ.get("GRIDIRON_DATABASE_URI", "").lower():
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

if __name__ == "__main__":
    # debug=False: the Werkzeug interactive debugger allows arbitrary code
    # execution if reachable by anyone else on the network -- never enable
    # it once other devices can reach this server. Use serve.py (waitress)
    # for real multi-device use; this entrypoint is for local-only dev.
    app.run(host="127.0.0.1", port=8090, debug=False)
