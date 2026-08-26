import secrets

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from helpers import (
    any_pool_signups_open,
    pool_signup_deadline,
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
from mailer import send_password_reset_link_email
from models import DEFAULT_MAX_TEAMS, DEFAULT_SITE_URL, POOL_LABELS, POOLS, Entry, Invite, PasswordReset, User, db
from models import now as _now

bp = Blueprint("auth", __name__)


def _has_no_entries(user):
    season_year = current_app.config["CURRENT_SEASON"]
    return Entry.query.filter_by(user_id=user.id, season_year=season_year).count() == 0


def _team_limit_for_email(email):
    """The effective account cap for an email group: the highest max_teams
    an admin has granted any account sharing that email."""
    accounts = User.query.filter_by(email=email).all()
    return max((u.max_teams for u in accounts), default=DEFAULT_MAX_TEAMS)


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

        # An address that already has an account never registers again. A
        # live invite is not a way in for someone who is already a member: it
        # would build a SECOND account on their address and sign them into it,
        # which reads as "the site lost my team" and leaves a duplicate in the
        # standings. This matters for anyone invited twice before the invite
        # page started refusing that, whose second link is still unused.
        # Extra teams come from Add Another Account, inside the account.
        owner = User.query.filter(
            db.func.lower(User.email) == (invite_row.email or "").strip().lower()
        ).first()
        if owner is not None:
            session.pop("invite_token", None)
            return render_template(
                "auth/register.html",
                invite_error=True,
                invite=None,
                already_registered=invite_row.email,
                signed_in_as=current_user.username if current_user.is_authenticated else None,
            )

        session["invite_token"] = token

    if request.method == "POST":
        username = request.form["username"].strip()
        email = (invite_row.email if invite_row else request.form["email"].strip().lower())
        password = request.form["password"]
        if not username or not email or not password:
            flash("All fields are required.", "error")
            return render_template("auth/register.html", invite=invite_row)
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("Another team is already using that name. Pick a different one.", "error")
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
        flash("Invalid team name/email or password.", "error")
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
        site_url = get_setting("site_url", DEFAULT_SITE_URL) or DEFAULT_SITE_URL
        by_email = {}
        for user in users:
            if not user.email:
                continue
            token = secrets.token_urlsafe(32)
            db.session.add(PasswordReset(user_id=user.id, token=token))
            link = f"{site_url}{url_for('auth.reset_password', token=token)}"
            by_email.setdefault(user.email, []).append((user.username, link))
        # Hunter's call: tell people plainly when nothing matched. The generic
        # "if that account exists" wording confused players who had simply
        # mistyped their address, and this is a 150-person private pool behind
        # invite-only registration, not a public signup. The trade-off is that
        # the form now confirms whether an address is registered here.
        if not users:
            flash(
                "There is no team with that name or email address. "
                "Check the spelling, or ask a commissioner to send you an invite.",
                "error",
            )
            return render_template("auth/forgot_password.html")

        if not by_email:
            flash(
                "That account has no email address on file, so a reset link "
                "cannot be sent. Message a commissioner and they can reset it "
                "for you.",
                "error",
            )
            return render_template("auth/forgot_password.html")

        db.session.commit()
        for email, username_links in by_email.items():
            send_async(send_password_reset_link_email, email, username_links)
        log_activity("password_reset_requested",
                     f"Reset link sent for '{identifier}'", user=users[0])
        flash(
            "A reset link is on its way. Check your inbox (and spam) -- the "
            "link expires in 1 hour.",
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
    season_year = current_app.config["CURRENT_SEASON"]
    # An extra account is only ever worth having because it can hold an extra
    # entry. Once every pool has passed its Week 1 deadline there is nothing
    # left to join, so creating one would drop the player on a join page with
    # no buttons and leave the commissioners an empty account to puzzle over.
    if not any_pool_signups_open(season_year):
        flash(
            "Extra teams are closed for the season -- signups ended at the "
            "Week 1 deadline. Ask a commissioner on the message board if you "
            "think you should still be able to add one.",
            "error",
        )
        return redirect(url_for("main.index"))
    existing_count = User.query.filter_by(email=email).count()
    limit = _team_limit_for_email(email)
    if existing_count >= limit:
        abort(
            403,
            description="You do not have permission to view this page. Reach out to the admins if you'd like more teams.",
        )
    if request.method == "POST":
        username = request.form["username"].strip()
        if not username:
            flash("Pick a team name for the new account.", "error")
            return render_template("auth/add_account.html", email=email)
        if username.lower() == current_user.username.lower():
            flash("The new team needs a different name from this one.", "error")
            return render_template("auth/add_account.html", email=email)
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("Another team is already using that name. Pick a different one.", "error")
            return render_template("auth/add_account.html", email=email)
        user = User(username=username, email=email)
        # No password to choose: extra entries belong to the person already
        # signed in, so the new account starts on the same password as the one
        # that created it. Asking for a second password was pure friction --
        # nothing ever required it to differ, and a forgotten one means a
        # commissioner has to reset an account the player thinks of as theirs.
        # The hash is copied rather than re-derived, so the password itself is
        # never handled here. They stay independent afterwards: changing one
        # account's password does not touch the others.
        user.password_hash = current_user.password_hash
        db.session.add(user)
        db.session.commit()
        log_activity("account_added", f"Created another account '{username}' on {email}")
        login_user(user)
        flash(f"New account '{username}' created, using the same password as "
              f"{email}. You're signed into it now.", "success")
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
    """Retired: the home page is the join page now.

    Kept as a redirect because register, add_account and switch_account all
    send people here after creating or changing accounts, and an old bookmark
    or a stale open tab would otherwise 404.
    """
    return redirect(url_for("main.index"))


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


def _name_changes_open(season_year):
    """Team names are editable until the Week 1 deadline, then fixed.

    Once entries are locked the name is on a bill, on the standings and in the
    message board, so a rename after that point has to go through a
    commissioner. It uses the same cutoff as signups -- while any pool will
    still take an entry, names are still free to change.
    """
    return any_pool_signups_open(season_year)


@bp.route("/change-name", methods=["GET", "POST"])
@login_required
def change_name():
    """Rename this team. Password required, because the name is what everyone
    else sees on the standings and a borrowed browser should not be able to
    change it."""
    season_year = current_app.config["CURRENT_SEASON"]
    deadline = pool_signup_deadline(season_year, "gridiron")
    open_now = _name_changes_open(season_year)

    if request.method == "POST":
        if not open_now:
            flash("Team names are locked for the season. Ask a commissioner on "
                  "the message board if it needs changing.", "error")
            return redirect(url_for("main.index"))

        new_name = request.form.get("new_name", "").strip()
        password = request.form.get("password", "")

        if not current_user.check_password(password):
            flash("That password is not right, so the name was not changed.", "error")
            return render_template("auth/change_name.html", open_now=open_now, deadline=deadline)
        if not new_name:
            flash("Type the new team name.", "error")
            return render_template("auth/change_name.html", open_now=open_now, deadline=deadline)
        if len(new_name) > 50:
            flash("That name is too long -- 50 characters at most.", "error")
            return render_template("auth/change_name.html", open_now=open_now, deadline=deadline)
        if new_name.lower() == current_user.username.lower() and new_name == current_user.username:
            flash("That is already your team name.", "error")
            return render_template("auth/change_name.html", open_now=open_now, deadline=deadline)
        # Case-insensitive, and excluding this account -- so "hunter" can fix
        # itself to "Hunter" without colliding with itself.
        taken = User.query.filter(
            db.func.lower(User.username) == new_name.lower(), User.id != current_user.id
        ).first()
        if taken:
            flash("Another team is already using that name. Pick a different one.", "error")
            return render_template("auth/change_name.html", open_now=open_now, deadline=deadline)

        old_name = current_user.username
        current_user.username = new_name
        db.session.commit()
        log_activity("name_change", f"Team name changed from '{old_name}' to '{new_name}'")
        flash(f"Your team is now called {new_name}.", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/change_name.html", open_now=open_now, deadline=deadline)


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
