import os
from datetime import datetime, timedelta

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf import CSRFProtect

from helpers import get_setting, week_is_complete
from models import Invite, User, db

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


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_or_create_secret_key()
    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'pools.db')}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["CURRENT_SEASON"] = int(os.environ.get("SEASON_YEAR", 2026))

    # Cookie hardening: HttpOnly blocks JS from reading the session cookie
    # (XSS mitigation), Secure means it's never sent over plain HTTP, and
    # Lax stops it being sent on cross-site POSTs (CSRF mitigation, on top
    # of the CSRFProtect tokens below). This app is only ever reached over
    # HTTPS (Cloudflare terminates TLS in front of it), so Secure is safe.
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
        }

    app.jinja_env.globals["week_is_complete"] = week_is_complete

    # Site-wide login gate: nothing is visible to a logged-out visitor except
    # the login/register pages themselves and static assets.
    PUBLIC_ENDPOINTS = {"auth.login", "auth.register", "static"}

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

    @app.errorhandler(403)
    def forbidden(e):
        message = e.description if getattr(e, "description", None) else "You don't have access to that page."
        return render_template("error.html", message=message), 403

    with app.app_context():
        db.create_all()

    return app


app = create_app()

if __name__ == "__main__":
    # debug=False: the Werkzeug interactive debugger allows arbitrary code
    # execution if reachable by anyone else on the network -- never enable
    # it once other devices can reach this server. Use serve.py (waitress)
    # for real multi-device use; this entrypoint is for local-only dev.
    app.run(host="127.0.0.1", port=8090, debug=False)
