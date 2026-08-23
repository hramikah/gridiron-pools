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
    deadline_epoch_ms,
    get_setting,
    short_week_label,
    unread_message_count,
    week_is_complete,
)
from models import Invite, User, db
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

    app.config["SESSION_COOKIE_SECURE"] = True
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
                .order_by(User.username)
                .all()
            )
            same_email_accounts = other_accounts + [current_user]
            limit = max((u.max_teams for u in same_email_accounts), default=1)
            can_add_account = len(same_email_accounts) < limit

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
        }

    app.jinja_env.globals["week_is_complete"] = week_is_complete
    app.jinja_env.globals["deadline_epoch_ms"] = deadline_epoch_ms
    app.jinja_env.globals["short_week_label"] = short_week_label
    # Every view that colours a result as text reads this one map, so a
    # colour change can't reach some pages and miss others.
    app.jinja_env.globals["result_class"] = RESULT_CLASS

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
        flash("That page had been sitting open too long, so the form was rejected. "
              "Please try again.", "error")
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

if __name__ == "__main__":
    # debug=False: the Werkzeug interactive debugger allows arbitrary code
    # execution if reachable by anyone else on the network -- never enable
    # it once other devices can reach this server. Use serve.py (waitress)
    # for real multi-device use; this entrypoint is for local-only dev.
    app.run(host="127.0.0.1", port=8090, debug=False)
