import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from helpers import (
    clear_login_attempts,
    get_setting,
    log_activity,
    gridiron_signup_deadline,
    gridiron_signups_open,
    login_rate_limited,
    record_failed_login,
    reset_request_rate_limited,
    send_async,
)
from mailer import send_password_reset_link_email, send_welcome_email
from models import POOL_LABELS, POOLS, Entry, Invite, PasswordReset, User, db
from models import now as _now

bp = Blueprint("auth", __name__)


def _has_no_entries(user):
    season_year = current_app.config["CURRENT_SEASON"]
    return Entry.query.filter_by(user_id=user.id, season_year=season_year).count() == 0


def _team_limit_for_email(email):
    """The effective account cap for an email group: the highest max_teams
    an admin has granted any account sharing that email."""
    accounts = User.query.filter_by(email=email).all()
    return max((u.max_teams for u in accounts), default=1)


@bp.route("/register", methods=["GET", "POST"])
def register():
    # The very first account ever (fresh install, nobody to send an invite
    # yet) bootstraps in as admin with no invite needed. Every registration
    # after that requires a valid, unused invite token tied to the email.
    bootstrapping = User.query.count() == 0
    # A token in the URL/form wins; otherwise fall back to one remembered in
    # the session (set below), so the nav's plain "Register" link -- shown
    # only once a valid invite has been seen -- still resolves correctly.
    token = request.values.get("token", "").strip() or session.get("invite_token", "")
    invite_row = Invite.query.filter_by(token=token).first() if token else None

    if not bootstrapping:
        if not invite_row or invite_row.used_at is not None:
            session.pop("invite_token", None)
            return render_template("auth/register.html", invite_error=True, invite=None)
        session["invite_token"] = token

    if request.method == "POST":
        username = request.form["username"].strip()
        email = (invite_row.email if invite_row else request.form["email"].strip().lower())
        password = request.form["password"]
        if not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("auth/register.html", invite=invite_row)
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("That username is taken.", "error")
            return render_template("auth/register.html", invite=invite_row)
        user = User(username=username, email=email)
        user.set_password(password)
        if bootstrapping:
            user.is_admin = True
        db.session.add(user)
        if invite_row:
            invite_row.used_at = _now()
            session.pop("invite_token", None)
        db.session.commit()
        send_welcome_email(user)
        login_user(user)
        log_activity("register", f"Account created ({email})", user=user)
        flash("Welcome! Your account has been created.", "success")
        return redirect(url_for("auth.choose_pools"))
    return render_template("auth/register.html", invite=invite_row)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if login_rate_limited():
            flash("Too many failed login attempts. Please wait a few minutes and try again.", "error")
            return render_template("auth/login.html")

        identifier = request.form["username"].strip().lower()
        password = request.form["password"]
        # Case-insensitive on both sides: usernames can contain uppercase
        # letters (e.g. "GentlemanJack"), but the typed identifier above is
        # always lowercased, so comparing against the raw column would never
        # match -- this silently locked out every mixed-case username.
        user = User.query.filter(
            (db.func.lower(User.username) == identifier) | (db.func.lower(User.email) == identifier)
        ).first()
        if user and user.check_password(password):
            clear_login_attempts()
            login_user(user)
            # A fresh login should see the current announcement again. The
            # "already seen" marker lives in the session, and logging out
            # doesn't wipe the session cookie, so without this a player who
            # dismissed an announcement once would never be shown it again on
            # any later login from the same browser.
            session.pop("seen_popup_id", None)
            log_activity("login", f"Signed in as {user.username}")
            flash("Logged in.", "success")
            next_url = request.args.get("next")
            if next_url:
                return redirect(next_url)
            if _has_no_entries(user):
                return redirect(url_for("auth.choose_pools"))
            return redirect(url_for("main.index"))
        record_failed_login()
        log_activity("login_failed", f"Failed sign-in attempt for '{identifier}'",
                     user=user if user else None)
        flash("Invalid username/email or password.", "error")
    return render_template("auth/login.html")


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if reset_request_rate_limited():
            flash("Too many reset requests. Please wait a few minutes and try again.", "error")
            return render_template("auth/forgot_password.html")

        identifier = request.form.get("identifier", "").strip().lower()
        users = User.query.filter(
            (db.func.lower(User.username) == identifier) | (db.func.lower(User.email) == identifier)
        ).all()

        # An email may cover several accounts (one per entry), so every
        # match gets its own single-use link in one message.
        # Same as invite links: build from the configured site_url, never
        # from the request's Host header (which the tunnel rewrites and a
        # caller can spoof).
        site_url = get_setting("site_url", "http://100.71.232.56:8090")
        by_email = {}
        for user in users:
            if not user.email:
                continue
            token = secrets.token_urlsafe(32)
            db.session.add(PasswordReset(user_id=user.id, token=token))
            link = f"{site_url}{url_for('auth.reset_password', token=token)}"
            by_email.setdefault(user.email, []).append((user.username, link))
        if by_email:
            db.session.commit()
            for email, username_links in by_email.items():
                send_async(send_password_reset_link_email, email, username_links)
            log_activity("password_reset_requested",
                         f"Reset link sent for '{identifier}'", user=users[0])

        # Deliberately the same answer whether or not anything matched --
        # otherwise this form tells a stranger which usernames and emails
        # are real.
        flash(
            "If that username or email has an account, a reset link is on its way. "
            "Check your inbox (and spam) -- the link expires in 1 hour.",
            "success",
        )
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    row = PasswordReset.query.filter_by(token=token).first()
    if not row or not row.is_valid():
        return render_template("auth/reset_password.html", invalid=True)

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
            return render_template("auth/reset_password.html", user=row.user)
        if new_password != confirm_password:
            flash("New passwords don't match.", "error")
            return render_template("auth/reset_password.html", user=row.user)
        row.user.set_password(new_password)
        row.used_at = _now()
        # Any other outstanding link for this account dies with it, so an
        # older email can't be replayed to take the account back.
        for other in PasswordReset.query.filter_by(user_id=row.user_id, used_at=None).all():
            other.used_at = _now()
        db.session.commit()
        log_activity("password_reset", "Reset their password via emailed link", user=row.user)
        flash("Password updated. You can log in now.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", user=row.user)


@bp.route("/add-account", methods=["GET", "POST"])
@login_required
def add_account():
    email = current_user.email
    existing_count = User.query.filter_by(email=email).count()
    limit = _team_limit_for_email(email)
    if existing_count >= limit:
        abort(
            403,
            description="You do not have permission to view this page. Reach out to the admins if you'd like more teams.",
        )
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("auth/add_account.html", email=email)
        if username.lower() == current_user.username.lower():
            flash("The new account needs a different username than this one.", "error")
            return render_template("auth/add_account.html", email=email)
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("That username is taken.", "error")
            return render_template("auth/add_account.html", email=email)
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        send_welcome_email(user)
        login_user(user)
        flash(f"New account '{username}' created and linked to {email}.", "success")
        return redirect(url_for("auth.choose_pools"))
    return render_template("auth/add_account.html", email=email)


@bp.route("/switch-account/<int:user_id>")
@login_required
def switch_account(user_id):
    target = User.query.get_or_404(user_id)
    if target.email != current_user.email:
        flash("You can only switch to another account that shares your email.", "error")
        return redirect(url_for("main.index"))
    logout_user()
    login_user(target)
    flash(f"Switched to {target.username}.", "success")
    if _has_no_entries(target):
        return redirect(url_for("auth.choose_pools"))
    return redirect(url_for("main.index"))


@bp.route("/choose-pools", methods=["GET", "POST"])
@login_required
def choose_pools():
    season_year = current_app.config["CURRENT_SEASON"]
    existing_pools = {
        e.pool for e in Entry.query.filter_by(user_id=current_user.id, season_year=season_year).all()
    }

    gridiron_open = gridiron_signups_open(season_year)

    if request.method == "POST":
        selected = [p for p in request.form.getlist("pools") if p in POOLS]
        joined = []
        for pool in selected:
            if pool in existing_pools:
                continue
            # Gridiron closes to new entries at the Week 2 deadline; enforced
            # here too, not just on the rules page, since this form is the
            # other way into a pool.
            if pool == "gridiron" and not gridiron_open:
                flash(
                    "Gridiron Investments is closed to new entries -- signups ended at the Week 2 deadline.",
                    "error",
                )
                continue
            count = Entry.query.filter_by(user_id=current_user.id, pool=pool, season_year=season_year).count()
            db.session.add(
                Entry(user_id=current_user.id, pool=pool, season_year=season_year, label=f"Entry {count + 1}")
            )
            joined.append(POOL_LABELS[pool])
        db.session.commit()
        if joined:
            log_activity("pool_joined", f"Joined {', '.join(joined)}")
            flash(f"You're in: {', '.join(joined)}.", "success")
        else:
            flash("No pools joined. You can join any pool later from its rules page.", "success")
        return redirect(url_for("main.index"))

    return render_template(
        "auth/choose_pools.html",
        existing_pools=existing_pools,
        gridiron_open=gridiron_open,
        gridiron_deadline=gridiron_signup_deadline(season_year),
    )


@bp.route("/logout")
@login_required
def logout():
    log_activity("logout", "Signed out")
    logout_user()
    # logout_user() only drops Flask-Login's own keys; everything else the app
    # stashed (seen announcements, invite token, last-seen stamp) would survive
    # into the next login on this browser. Clear the lot, as the inactivity
    # timeout already does.
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("main.index"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "error")
            return render_template("auth/change_password.html")
        if len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
            return render_template("auth/change_password.html")
        if new_password != confirm_password:
            flash("New passwords don't match.", "error")
            return render_template("auth/change_password.html")
        current_user.set_password(new_password)
        db.session.commit()
        log_activity("password_change", "Changed their password")
        flash("Password changed.", "success")
        return redirect(url_for("main.index"))
    return render_template("auth/change_password.html")
