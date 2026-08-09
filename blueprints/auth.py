from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from helpers import clear_login_attempts, login_rate_limited, record_failed_login
from mailer import send_welcome_email
from models import POOL_LABELS, POOLS, Entry, Invite, User, db
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
    token = request.values.get("token", "").strip()
    invite_row = Invite.query.filter_by(token=token).first() if token else None

    if not bootstrapping:
        if not invite_row:
            return render_template("auth/register.html", invite_error=True, invite=None)
        if invite_row.used_at is not None:
            return render_template("auth/register.html", invite_error=True, invite=None)

    if request.method == "POST":
        username = request.form["username"].strip()
        email = (invite_row.email if invite_row else request.form["email"].strip().lower())
        password = request.form["password"]
        if not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("auth/register.html", invite=invite_row)
        if User.query.filter_by(username=username).first():
            flash("That username is taken.", "error")
            return render_template("auth/register.html", invite=invite_row)
        user = User(username=username, email=email)
        user.set_password(password)
        if bootstrapping:
            user.is_admin = True
        db.session.add(user)
        if invite_row:
            invite_row.used_at = _now()
        db.session.commit()
        send_welcome_email(user)
        login_user(user)
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
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first()
        if user and user.check_password(password):
            clear_login_attempts()
            login_user(user)
            flash("Logged in.", "success")
            next_url = request.args.get("next")
            if next_url:
                return redirect(next_url)
            if _has_no_entries(user):
                return redirect(url_for("auth.choose_pools"))
            return redirect(url_for("main.index"))
        record_failed_login()
        flash("Invalid username/email or password.", "error")
    return render_template("auth/login.html")


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
        if User.query.filter_by(username=username).first():
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

    if request.method == "POST":
        selected = [p for p in request.form.getlist("pools") if p in POOLS]
        joined = []
        for pool in selected:
            if pool in existing_pools:
                continue
            count = Entry.query.filter_by(user_id=current_user.id, pool=pool, season_year=season_year).count()
            db.session.add(
                Entry(user_id=current_user.id, pool=pool, season_year=season_year, label=f"Entry {count + 1}")
            )
            joined.append(POOL_LABELS[pool])
        db.session.commit()
        if joined:
            flash(f"You're in: {', '.join(joined)}.", "success")
        else:
            flash("No pools joined. You can join any pool later from its rules page.", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/choose_pools.html", existing_pools=existing_pools)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
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
        flash("Password changed.", "success")
        return redirect(url_for("main.index"))
    return render_template("auth/change_password.html")
