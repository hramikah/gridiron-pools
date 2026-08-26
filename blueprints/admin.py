import os
import re
import secrets
import shutil
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import admin_required, deadline_passed, get_current_week, get_setting, log_activity, send_async, set_setting, week_label
from mailer import send_invite_link_emails, send_password_reset_email, send_player_message_email
from models import DEFAULT_MAX_TEAMS, DEFAULT_SITE_URL, ActivityLog, Announcement, BuyBack, ContactMessage, Entry, Game, GridironMiss, Invite, LoserPoolPoints, PRESEASON_OFFSET, POOLS, POOL_ENTRY_FEES, POOL_LABELS, Pick, Team, User, Week, db, default_buyback_open, now
from publisher import publish_week
from scoring import DROPDEAD_BUYBACK_FEE, GRIDIRON_GRID_COLUMNS, GRIDIRON_MISS_PENALTY_LOSSES, enforce_dropdead_no_tie, ensure_missed_processed, gridiron_pick_limit, process_due_weeks, gridiron_picks_grid, process_missed_picks, score_game

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

bp = Blueprint("admin", __name__)


@bp.before_request
@login_required
@admin_required
def guard():
    pass


@bp.route("/")
def dashboard():
    season_year = current_app.config["CURRENT_SEASON"]
    # per-pool summary for the landing cards: week count + game count
    week_counts = {pool: 0 for pool in POOLS}
    for w in Week.query.filter_by(season_year=season_year).all():
        week_counts[w.pool] = week_counts.get(w.pool, 0) + 1
    game_counts = {pool: 0 for pool in POOLS}
    for g in Game.query.join(Week).filter(Week.season_year == season_year).all():
        game_counts[g.pool] = game_counts.get(g.pool, 0) + 1
    return render_template(
        "admin/dashboard.html",
        pools=POOLS,
        pool_labels=POOL_LABELS,
        week_counts=week_counts,
        game_counts=game_counts,
    )


@bp.route("/pool/<pool>")
def pool_manager(pool):
    if pool not in POOLS:
        flash("Unknown pool.", "error")
        return redirect(url_for("admin.dashboard"))
    season_year = current_app.config["CURRENT_SEASON"]
    weeks = (
        Week.query.filter_by(season_year=season_year, pool=pool)
        .order_by(Week.number)
        .all()
    )
    # Apply penalties for every past-deadline week that still owes them.
    process_due_weeks(season_year, pool)
    game_counts = {}
    for g in Game.query.join(Week).filter(Week.season_year == season_year, Game.pool == pool).all():
        game_counts[g.week_id] = game_counts.get(g.week_id, 0) + 1
    entry_total = Entry.query.filter_by(pool=pool, season_year=season_year).count()
    entry_active = Entry.query.filter_by(pool=pool, season_year=season_year, is_active=True).count()
    return render_template(
        "admin/pool_manager.html",
        pool=pool,
        pool_label=POOL_LABELS[pool],
        weeks=weeks,
        game_counts=game_counts,
        entry_total=entry_total,
        entry_active=entry_active,
    )


@bp.route("/pool/<pool>/weeks/<int:week_id>")
def pool_week(pool, week_id):
    if pool not in POOLS:
        flash("Unknown pool.", "error")
        return redirect(url_for("admin.dashboard"))
    week = Week.query.get_or_404(week_id)
    if week.pool != pool:
        flash("That week belongs to a different pool.", "error")
        return redirect(url_for("admin.pool_manager", pool=pool))
    ensure_missed_processed(week)  # apply penalties if the deadline has passed
    teams = Team.query.order_by(Team.name).all()
    games = Game.query.filter_by(week_id=week.id, pool=pool).all()

    # Gridiron: build a per-player picks grid (username + up to N pick slots)
    picks_grid = None
    max_slots = 0
    if pool == "gridiron":
        picks_grid, max_slots = gridiron_picks_grid(week)

    return render_template(
        "admin/pool_week.html",
        pool=pool,
        pool_label=POOL_LABELS[pool],
        week=week,
        teams=teams,
        games=games,
        deadline_passed=deadline_passed(week),
        picks_grid=picks_grid,
        max_slots=max_slots,
        # Fixed-width grid: entries with a bigger allowance wrap onto a second
        # line rather than stretching the table to 10 columns for everyone.
        columns=min(GRIDIRON_GRID_COLUMNS, max_slots) or GRIDIRON_GRID_COLUMNS,
        miss_penalty=GRIDIRON_MISS_PENALTY_LOSSES,
    )


@bp.route("/users")
def user_list():
    users = User.query.order_by(User.username).all()
    return render_template("admin/user_list.html", users=users)


@bp.route("/players")
def players():
    season_year = current_app.config["CURRENT_SEASON"]
    users = User.query.order_by(User.username).all()
    entries_by_user = {}
    for e in Entry.query.filter_by(season_year=season_year).all():
        entries_by_user.setdefault(e.user_id, []).append(e)
    return render_template(
        "admin/players.html",
        users=users,
        entries_by_user=entries_by_user,
        pool_labels=POOL_LABELS,
    )


@bp.route("/players/<int:user_id>/max-teams", methods=["POST"])
def set_max_teams(user_id):
    user = User.query.get_or_404(user_id)
    try:
        value = int(request.form.get("max_teams", DEFAULT_MAX_TEAMS))
    except ValueError:
        value = DEFAULT_MAX_TEAMS
    value = max(1, min(10, value))
    user.max_teams = value
    db.session.commit()
    flash(f"{user.username} can now have up to {value} team{'s' if value != 1 else ''} (this account's email).", "success")
    return redirect(url_for("admin.players"))


@bp.route("/players/<int:user_id>/pools/<pool>/toggle", methods=["POST"])
def toggle_pool_membership(user_id, pool):
    if pool not in POOLS:
        flash("Unknown pool.", "error")
        return redirect(url_for("admin.players"))

    user = User.query.get_or_404(user_id)
    season_year = current_app.config["CURRENT_SEASON"]
    existing_entries = Entry.query.filter_by(user_id=user.id, pool=pool, season_year=season_year).all()

    if existing_entries:
        entry_ids = [e.id for e in existing_entries]
        picks_removed = Pick.query.filter(Pick.entry_id.in_(entry_ids)).delete(synchronize_session=False)
        GridironMiss.query.filter(GridironMiss.entry_id.in_(entry_ids)).delete(synchronize_session=False)
        Entry.query.filter(Entry.id.in_(entry_ids)).delete(synchronize_session=False)
        db.session.commit()
        note = f" ({picks_removed} pick{'s' if picks_removed != 1 else ''} removed with it)" if picks_removed else ""
        flash(f"Removed {user.username} from {POOL_LABELS[pool]}{note}.", "success")
    else:
        db.session.add(Entry(user_id=user.id, pool=pool, season_year=season_year, label="Entry 1"))
        db.session.commit()
        flash(f"Added {user.username} to {POOL_LABELS[pool]}.", "success")

    return redirect(url_for("admin.players"))


@bp.route("/players/<int:user_id>/toggle-admin", methods=["POST"])
def toggle_admin(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't change the admin status of the account you're logged in as.", "error")
        return redirect(url_for("admin.players"))
    user.is_admin = not user.is_admin
    db.session.commit()
    flash(f"{user.username} is now {'an admin' if user.is_admin else 'a regular player'}.", "success")
    return redirect(url_for("admin.players"))


@bp.route("/players/<int:user_id>/reset-password", methods=["POST"])
def reset_password(user_id):
    user = User.query.get_or_404(user_id)
    temp_password = secrets.token_urlsafe(6)
    user.set_password(temp_password)
    db.session.commit()
    if user.email:
        send_async(send_password_reset_email, user, temp_password)
        emailed = f" It was also emailed to {user.email}."
    else:
        emailed = " They have no email on file, so it wasn't sent to them."
    # Filed against the player whose password changed, with the admin named --
    # somebody else taking control of an account is exactly the kind of thing
    # you want to be able to find later. The temporary password itself is
    # never written here.
    log_activity(
        "password_reset",
        f"Password reset by {current_user.username}; temporary password "
        + ("emailed to them" if user.email else "shown on screen only (no email on file)"),
        user=user,
    )
    flash(
        f"New temporary password for {user.username}: {temp_password} "
        "-- give this to them now, it won't be shown again. They should change it "
        f"immediately after logging in (top-right menu -> Change Password).{emailed}",
        "success",
    )
    return redirect(url_for("admin.players"))


@bp.route("/players/<int:user_id>/delete", methods=["POST"])
def delete_player(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't delete the account you're logged in as.", "error")
        return redirect(url_for("admin.players"))

    username = user.username
    entry_ids = [e.id for e in Entry.query.filter_by(user_id=user.id).all()]
    if entry_ids:
        Pick.query.filter(Pick.entry_id.in_(entry_ids)).delete(synchronize_session=False)
        GridironMiss.query.filter(GridironMiss.entry_id.in_(entry_ids)).delete(synchronize_session=False)
        Entry.query.filter(Entry.id.in_(entry_ids)).delete(synchronize_session=False)
    ContactMessage.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    flash(f"Deleted account '{username}' and all their picks/entries.", "success")
    return redirect(url_for("admin.players"))


def preseason_number(number, is_preseason):
    """Storage number for a week the admin is creating by hand.

    Preseason weeks live at PRESEASON_OFFSET + N so they sort past the regular
    season and cannot collide with it. The admin types the preseason week's own
    number -- 1 for the first weekend -- and ticks the box; the offset is added
    here. Idempotent, so a number already in preseason range is left alone.

    Only the auto-publisher used to set is_preseason, so a week created by hand
    was a regular-season week whatever number it carried: it scored for real,
    eliminated Drop Dead entries for real, and rendered as "Week 101".
    """
    if not is_preseason:
        return number
    return number if number > PRESEASON_OFFSET else PRESEASON_OFFSET + number


@bp.route("/pool/<pool>/weeks/new", methods=["POST"])
def new_week(pool):
    if pool not in POOLS:
        flash("Unknown pool.", "error")
        return redirect(url_for("admin.dashboard"))
    season_year = current_app.config["CURRENT_SEASON"]
    number = int(request.form["number"])
    is_preseason = bool(request.form.get("is_preseason"))
    number = preseason_number(number, is_preseason)
    deadline_str = request.form["pick_deadline"]
    deadline = datetime.fromisoformat(deadline_str)
    if Week.query.filter_by(season_year=season_year, number=number, pool=pool).first():
        flash(f"{week_label(number)} already exists for {POOL_LABELS[pool]}.", "error")
        return redirect(url_for("admin.pool_manager", pool=pool))
    db.session.add(Week(season_year=season_year, number=number, pool=pool, pick_deadline=deadline,
                        is_preseason=is_preseason,
                        buyback_open=default_buyback_open(pool, number, is_preseason)))
    db.session.commit()
    flash(f"{week_label(number)} created for {POOL_LABELS[pool]}.", "success")
    return redirect(url_for("admin.pool_manager", pool=pool))


@bp.route("/build-season", methods=["POST"])
def build_season():
    """Create the full 18-week regular season across all three pools.

    Week numbers used to be typed in one at a time, which let them drift
    apart between pools and left gaps -- and a gap breaks the Drop Dead
    buy-back, which needs the week *after* an elimination to exist. Deadlines
    come off the season-start Thursday: Saturday noon Eastern each week,
    the same rule auto-publish uses.

    Idempotent: existing weeks are left exactly as they are, so this is safe
    to press twice and never touches a deadline an admin has adjusted.
    """
    season_year = current_app.config["CURRENT_SEASON"]
    season_start_str = get_setting("season_start_thursday")
    if not season_start_str:
        flash("Set the season start Thursday in Admin > Settings first.", "error")
        return redirect(url_for("admin.dashboard"))
    try:
        season_start = datetime.fromisoformat(season_start_str).date()
    except ValueError:
        flash("The season start date isn't a valid date. Fix it in Admin > Settings.", "error")
        return redirect(url_for("admin.dashboard"))

    created = 0
    for number in range(1, 19):
        # Week N's Thursday is N-1 weeks after Week 1's; its deadline is the
        # Saturday after that, at noon.
        week_thursday = season_start + timedelta(weeks=number - 1)
        deadline = datetime.combine(week_thursday + timedelta(days=2), datetime.min.time()) + timedelta(hours=12)
        for pool in POOLS:
            if Week.query.filter_by(season_year=season_year, number=number, pool=pool).first():
                continue
            db.session.add(Week(
                season_year=season_year, number=number, pool=pool,
                pick_deadline=deadline,
                buyback_open=default_buyback_open(pool, number),
            ))
            created += 1
    db.session.commit()

    if created:
        log_activity("build_season", f"Built the 18-week season ({created} week rows created)")
        flash(f"Season built: {created} new week(s) across the three pools. Existing weeks were left alone.", "success")
    else:
        flash("All 18 weeks already exist in every pool. Nothing to do.", "success")
    return redirect(url_for("admin.pool_manager", pool=request.form.get("pool", "gridiron")))


@bp.route("/weeks/<int:week_id>/buyback", methods=["POST"])
def toggle_buyback(week_id):
    week = Week.query.get_or_404(week_id)
    week.buyback_open = not week.buyback_open
    db.session.commit()
    state = "open" if week.buyback_open else "closed"
    log_activity("buyback_window", f"{week.label}: buy-backs {state}", pool=week.pool)
    flash(f"Buy-backs are now {state} for {week.label}.", "success")
    return redirect(url_for("admin.pool_manager", pool=week.pool))


@bp.route("/weeks/<int:week_id>/deadline", methods=["POST"])
def update_deadline(week_id):
    week = Week.query.get_or_404(week_id)
    deadline_str = request.form.get("pick_deadline", "")
    try:
        week.pick_deadline = datetime.fromisoformat(deadline_str)
    except ValueError:
        flash("Enter a valid date and time.", "error")
        return redirect(url_for("admin.pool_week", pool=week.pool, week_id=week.id))
    db.session.commit()
    flash(
        f"Deadline for {POOL_LABELS[week.pool]} {week.label} updated to "
        f"{week.pick_deadline.strftime('%a %b %d, %I:%M %p')} Eastern.",
        "success",
    )
    return redirect(url_for("admin.pool_week", pool=week.pool, week_id=week.id))


def _read_game_fields(form, pool):
    """Parse the add/edit game form into Game field values. Shared by
    new_game and edit_game so the two never drift apart.

    Drop Dead & Loser are straight-up NFL matchups (no spread/O-U); their team
    names are derived from the selected NFL team so it never depends on client
    JS. Gridiron carries lines (spread, over/under) and can include college.
    """
    sport = form.get("sport", "nfl")
    home_team = form.get("home_team", "").strip()
    away_team = form.get("away_team", "").strip()
    home_team_id = form.get("home_team_id") or None
    away_team_id = form.get("away_team_id") or None
    is_mnf = bool(form.get("is_mnf"))

    kickoff_str = form.get("kickoff", "").strip()
    kickoff = None
    if kickoff_str:
        try:
            kickoff = datetime.fromisoformat(kickoff_str)
        except ValueError:
            kickoff = None

    if pool == "gridiron":
        favorite = form.get("favorite") or None
        spread = form.get("spread") or None
        over_under = form.get("over_under") or None
    else:
        sport = "nfl"
        favorite = spread = over_under = None
        if away_team_id:
            t = Team.query.get(int(away_team_id))
            if t:
                away_team = t.name
        if home_team_id:
            t = Team.query.get(int(home_team_id))
            if t:
                home_team = t.name

    return {
        "sport": sport,
        "home_team": home_team,
        "away_team": away_team,
        "home_team_id": int(home_team_id) if home_team_id else None,
        "away_team_id": int(away_team_id) if away_team_id else None,
        "favorite": favorite,
        "spread": float(spread) if spread else None,
        "over_under": float(over_under) if over_under else None,
        "is_mnf": is_mnf,
        "kickoff": kickoff,
    }


@bp.route("/weeks/<int:week_id>/games/new", methods=["POST"])
def new_game(week_id):
    week = Week.query.get_or_404(week_id)
    pool = week.pool  # a week belongs to exactly one pool
    fields = _read_game_fields(request.form, pool)

    if not fields["home_team"] or not fields["away_team"]:
        flash("Both teams are required.", "error")
        return redirect(url_for("admin.pool_week", pool=pool, week_id=week.id))

    db.session.add(Game(week_id=week.id, pool=pool, **fields))
    db.session.commit()
    flash("Game added.", "success")
    return redirect(url_for("admin.pool_week", pool=pool, week_id=week.id))


@bp.route("/games/<int:game_id>/edit", methods=["POST"])
def edit_game(game_id):
    game = Game.query.get_or_404(game_id)
    pool = game.pool
    fields = _read_game_fields(request.form, pool)

    if not fields["home_team"] or not fields["away_team"]:
        flash("Both teams are required.", "error")
        return redirect(url_for("admin.pool_week", pool=pool, week_id=game.week_id))

    for key, value in fields.items():
        setattr(game, key, value)
    db.session.commit()
    if game.is_final:
        score_game(game)  # re-score picks against the edited line/teams/scores
        enforce_dropdead_no_tie(game.week)
    flash("Game updated.", "success")
    return redirect(url_for("admin.pool_week", pool=pool, week_id=game.week_id))


def _ensure_pool_week(season_year, number, pool):
    """Return the Week for (season, number, pool), creating it if missing by
    copying the deadline from an existing same-number week in another pool.
    Returns None if no same-number week exists anywhere to copy from."""
    w = Week.query.filter_by(season_year=season_year, number=number, pool=pool).first()
    if w:
        return w
    template = Week.query.filter_by(season_year=season_year, number=number).first()
    if not template:
        return None
    w = Week(season_year=season_year, number=number, pool=pool, pick_deadline=template.pick_deadline)
    db.session.add(w)
    db.session.commit()
    return w


@bp.route("/game-creator")
def game_creator():
    season_year = current_app.config["CURRENT_SEASON"]
    numbers = sorted({w.number for w in Week.query.filter_by(season_year=season_year).all()})
    selected = request.args.get("week", type=int)
    if selected is None and numbers:
        selected = numbers[-1]
    teams = Team.query.order_by(Team.name).all()
    games_by_pool = {}
    if selected is not None:
        for pool in POOLS:
            wk = Week.query.filter_by(season_year=season_year, number=selected, pool=pool).first()
            games_by_pool[pool] = Game.query.filter_by(week_id=wk.id).all() if wk else []
    active_week = get_setting("active_week") or ""
    return render_template(
        "admin/game_creator.html",
        numbers=numbers,
        selected=selected,
        teams=teams,
        games_by_pool=games_by_pool,
        pool_labels=POOL_LABELS,
        pools=POOLS,
        active_week=active_week,
    )


@bp.route("/game-creator/new-week", methods=["POST"])
def game_creator_new_week():
    season_year = current_app.config["CURRENT_SEASON"]
    number = request.form.get("number", type=int)
    if number is None:
        flash("Enter a week number.", "error")
        return redirect(url_for("admin.game_creator"))
    try:
        deadline = datetime.fromisoformat(request.form.get("pick_deadline", ""))
    except ValueError:
        flash("Enter a valid pick deadline.", "error")
        return redirect(url_for("admin.game_creator"))
    is_preseason = bool(request.form.get("is_preseason"))
    number = preseason_number(number, is_preseason)
    created = []
    for pool in POOLS:
        if not Week.query.filter_by(season_year=season_year, number=number, pool=pool).first():
            db.session.add(Week(season_year=season_year, number=number, pool=pool, pick_deadline=deadline,
                                is_preseason=is_preseason,
                                buyback_open=default_buyback_open(pool, number, is_preseason)))
            created.append(POOL_LABELS[pool])
    db.session.commit()
    if created:
        flash(f"{week_label(number)} created for: {', '.join(created)}. Adjust per-pool deadlines in Pick Manager if they differ.", "success")
    else:
        flash(f"{week_label(number)} already exists in all pools.", "error")
    return redirect(url_for("admin.game_creator", week=number))


@bp.route("/game-creator/set-current-week", methods=["POST"])
def set_current_week():
    value = request.form.get("active_week", "")
    if value == "auto" or value == "":
        set_setting("active_week", "")
        flash("Current week is now automatic (based on each pool's deadline).", "success")
    else:
        set_setting("active_week", str(int(value)))
        flash(f"Current week pinned to {week_label(int(value))} for all pools.", "success")
    return redirect(url_for("admin.game_creator"))


@bp.route("/game-creator/add", methods=["POST"])
def game_creator_add():
    season_year = current_app.config["CURRENT_SEASON"]
    number = request.form.get("week", type=int)
    if number is None:
        flash("Choose a week.", "error")
        return redirect(url_for("admin.game_creator"))

    sport = request.form.get("sport", "nfl")

    favorite = request.form.get("favorite") or None
    spread = request.form.get("spread") or None
    over_under = request.form.get("over_under") or None
    is_mnf = bool(request.form.get("is_mnf"))
    kickoff = None
    kickoff_str = request.form.get("kickoff", "").strip()
    if kickoff_str:
        try:
            kickoff = datetime.fromisoformat(kickoff_str)
        except ValueError:
            kickoff = None

    spread_val = float(spread) if spread else None
    ou_val = float(over_under) if over_under else None

    if sport == "college":
        # College is Gridiron-only -- Drop Dead & Loser never carry it, so
        # there's no Team row to look up and nothing to mirror elsewhere.
        away_name = request.form.get("away_team_name", "").strip()
        home_name = request.form.get("home_team_name", "").strip()
        if not away_name or not home_name:
            flash("Enter both college team names.", "error")
            return redirect(url_for("admin.game_creator", week=number))
        if away_name.lower() == home_name.lower():
            flash("Away and home team must differ.", "error")
            return redirect(url_for("admin.game_creator", week=number))

        gw = _ensure_pool_week(season_year, number, "gridiron")
        if gw:
            db.session.add(Game(
                week_id=gw.id, pool="gridiron", sport="college",
                home_team=home_name, away_team=away_name,
                favorite=favorite, spread=spread_val, over_under=ou_val,
                kickoff=kickoff,
            ))
            db.session.commit()
            flash(f"{away_name} @ {home_name} added to: Gridiron.", "success")
        return redirect(url_for("admin.game_creator", week=number))

    away = Team.query.get(int(request.form["away_team_id"])) if request.form.get("away_team_id") else None
    home = Team.query.get(int(request.form["home_team_id"])) if request.form.get("home_team_id") else None
    if not away or not home:
        flash("Pick both NFL teams.", "error")
        return redirect(url_for("admin.game_creator", week=number))
    if away.id == home.id:
        flash("Away and home team must differ.", "error")
        return redirect(url_for("admin.game_creator", week=number))

    created = []

    # Gridiron carries the full line; Drop Dead / Loser get the straight-up
    # matchup only (Loser also carries the Monday-Night flag for its auto-pick).
    gw = _ensure_pool_week(season_year, number, "gridiron")
    if gw:
        db.session.add(Game(
            week_id=gw.id, pool="gridiron", sport="nfl",
            home_team=home.name, away_team=away.name,
            home_team_id=home.id, away_team_id=away.id,
            favorite=favorite, spread=spread_val, over_under=ou_val,
            is_mnf=is_mnf, kickoff=kickoff,
        ))
        created.append("Gridiron")

    for pool in ("dropdead", "loser"):
        pw = _ensure_pool_week(season_year, number, pool)
        if pw:
            db.session.add(Game(
                week_id=pw.id, pool=pool, sport="nfl",
                home_team=home.name, away_team=away.name,
                home_team_id=home.id, away_team_id=away.id,
                is_mnf=(is_mnf if pool == "loser" else False),
                kickoff=kickoff,
            ))
            created.append(POOL_LABELS[pool])

    db.session.commit()
    flash(f"{away.name} @ {home.name} added to: {', '.join(created)}.", "success")
    return redirect(url_for("admin.game_creator", week=number))


@bp.route("/games/<int:game_id>/result", methods=["POST"])
def enter_result(game_id):
    game = Game.query.get_or_404(game_id)
    game.home_score = int(request.form["home_score"])
    game.away_score = int(request.form["away_score"])
    game.is_final = True
    db.session.commit()
    score_game(game)
    enforce_dropdead_no_tie(game.week)
    flash(f"{game.label} finalized and picks scored.", "success")
    return redirect(url_for("admin.pool_week", pool=game.pool, week_id=game.week_id))

@bp.route("/games/<int:game_id>/delete", methods=["POST"])
def delete_game(game_id):
    game = Game.query.get_or_404(game_id)
    week_id = game.week_id
    pool = game.pool
    # Return any player picks tied to this game so deleting it doesn't leave
    # them stuck with a pick they can no longer change:
    #  - Gridiron picks reference the game directly (game_id).
    #  - Drop Dead / Loser picks reference a team, so return picks for either
    #    team in this matchup (same pool + week).
    picks_removed = Pick.query.filter_by(game_id=game.id).delete(synchronize_session=False)
    if pool in ("dropdead", "loser"):
        team_ids = [tid for tid in (game.home_team_id, game.away_team_id) if tid is not None]
        if team_ids:
            picks_removed += (
                Pick.query.filter(
                    Pick.pool == pool,
                    Pick.week_id == week_id,
                    Pick.team_id.in_(team_ids),
                ).delete(synchronize_session=False)
            )
    db.session.delete(game)
    db.session.commit()
    note = f" {picks_removed} player pick(s) returned." if picks_removed else ""
    flash(f"Game removed.{note}", "success")
    return redirect(url_for("admin.pool_week", pool=pool, week_id=week_id))


@bp.route("/picks/<int:pick_id>/delete", methods=["POST"])
def delete_pick(pick_id):
    pick = Pick.query.get_or_404(pick_id)
    week = pick.week
    pool = pick.pool
    db.session.delete(pick)
    db.session.commit()
    flash("Pick deleted and returned to the player.", "success")
    return redirect(url_for("admin.pool_week", pool=pool, week_id=week.id))


@bp.route("/picks/<int:pick_id>/change", methods=["POST"])
def change_pick(pick_id):
    pick = Pick.query.get_or_404(pick_id)
    week = pick.week
    back = redirect(url_for("admin.pool_week", pool="gridiron", week_id=week.id))

    try:
        gid_s, market, side = request.form.get("selection", "").split("|")
        gid = int(gid_s)
    except ValueError:
        flash("Choose a selection.", "error")
        return back

    valid_sides = {"spread": ("home", "away"), "total": ("over", "under")}
    if market not in valid_sides or side not in valid_sides[market]:
        flash("That selection isn't valid.", "error")
        return back

    game = Game.query.filter_by(id=gid, week_id=week.id, pool="gridiron").first()
    if not game or (market == "total" and game.over_under is None):
        flash("That selection isn't available for this week.", "error")
        return back

    # can't hold both sides of the same market on the same game
    dup = Pick.query.filter(
        Pick.entry_id == pick.entry_id,
        Pick.week_id == week.id,
        Pick.pool == "gridiron",
        Pick.game_id == gid,
        Pick.market == market,
        Pick.id != pick.id,
    ).first()
    if dup:
        # Surface this as a blocking pop-up (not a subtle top-of-page flash) --
        # it's an easy, high-impact admin mistake. pool_week shows a modal when
        # pick_error=dup is present.
        return redirect(url_for("admin.pool_week", pool="gridiron", week_id=week.id, pick_error="dup"))

    pick.game_id = gid
    pick.market = market
    pick.side = side
    pick.result = "pending"
    pick.points = 0
    db.session.commit()
    if game.is_final:
        score_game(game)  # re-score against the new selection
    flash("Pick updated.", "success")
    return back


@bp.route("/entries/<int:entry_id>/weeks/<int:week_id>/pick", methods=["POST"])
def add_pick(entry_id, week_id):
    """Admin fills an empty Gridiron pick slot for a player (e.g. after a
    missed week). Enforces the entry's weekly pick limit and the one-side-per-
    market rule, and clears the week's GridironMiss since the entry now has a
    pick (it's no longer a full no-show)."""
    entry = Entry.query.get_or_404(entry_id)
    week = Week.query.get_or_404(week_id)
    back = redirect(url_for("admin.pool_week", pool="gridiron", week_id=week.id))
    if entry.pool != "gridiron" or week.pool != "gridiron":
        flash("Adding picks here is Gridiron-only.", "error")
        return back

    try:
        gid_s, market, side = request.form.get("selection", "").split("|")
        gid = int(gid_s)
    except ValueError:
        flash("Choose a selection.", "error")
        return back

    valid_sides = {"spread": ("home", "away"), "total": ("over", "under")}
    if market not in valid_sides or side not in valid_sides[market]:
        flash("That selection isn't valid.", "error")
        return back

    game = Game.query.filter_by(id=gid, week_id=week.id, pool="gridiron").first()
    if not game or (market == "total" and game.over_under is None):
        flash("That selection isn't available for this week.", "error")
        return back

    existing = Pick.query.filter_by(entry_id=entry.id, week_id=week.id, pool="gridiron").count()
    if existing >= gridiron_pick_limit(entry, week):
        flash("That entry has already used all its picks for this week.", "error")
        return back

    dup = Pick.query.filter_by(
        entry_id=entry.id, week_id=week.id, pool="gridiron", game_id=gid, market=market
    ).first()
    if dup:
        return redirect(url_for("admin.pool_week", pool="gridiron", week_id=week.id, pick_error="dup"))

    # they now have a pick, so they're no longer a full no-show for the week
    GridironMiss.query.filter_by(entry_id=entry.id, week_id=week.id).delete()
    db.session.add(
        Pick(entry_id=entry.id, week_id=week.id, pool="gridiron", game_id=gid, market=market, side=side)
    )
    db.session.commit()
    if game.is_final:
        score_game(game)
    flash("Pick added for the player.", "success")
    return back


@bp.route("/weeks/<int:week_id>/process-missed", methods=["POST"])
def process_missed(week_id):
    week = Week.query.get_or_404(week_id)
    process_missed_picks(week)
    enforce_dropdead_no_tie(week)
    week.missed_processed = True
    db.session.commit()
    messages = {
        "dropdead": "Drop Dead no-shows eliminated for this week.",
        "loser": "Loser Pool no-shows assigned the MNF visitor for this week.",
        "gridiron": "Gridiron no-shows scored 0-5 (8-pick makeup unlocked) for this week.",
    }
    flash(f"Missed picks processed: {messages.get(week.pool, 'done.')}", "success")
    return redirect(url_for("admin.pool_week", pool=week.pool, week_id=week.id))


@bp.route("/reports")
def reports():
    season_year = current_app.config["CURRENT_SEASON"]

    total_users = User.query.count()
    pool_entry_counts = {
        pool: Entry.query.filter_by(pool=pool, season_year=season_year).count() for pool in POOLS
    }

    # each pool has its own current week now
    current_weeks = {pool: get_current_week(season_year, pool) for pool in POOLS}
    current_week = current_weeks.get("gridiron") or next(
        (w for w in current_weeks.values() if w), None
    )

    missing_picks = {}
    for pool in POOLS:
        cw = current_weeks[pool]
        if not cw:
            missing_picks[pool] = []
            continue
        entries = Entry.query.filter_by(pool=pool, season_year=season_year).all()
        if pool == "dropdead":
            entries = [e for e in entries if e.is_active]
        picked_entry_ids = {
            p.entry_id for p in Pick.query.filter_by(pool=pool, week_id=cw.id).all()
        }
        missing_picks[pool] = [e for e in entries if e.id not in picked_entry_ids]

    pending_games = (
        Game.query.join(Week)
        .filter(Week.season_year == season_year, Game.is_final.is_(False))
        .order_by(Week.number, Game.kickoff)
        .all()
    )

    dropdead_alive = Entry.query.filter_by(pool="dropdead", season_year=season_year, is_active=True).count()
    dropdead_total = pool_entry_counts["dropdead"]

    all_teams = Team.query.order_by(Team.name).all()
    teams_with_points = {
        lp.team_id for lp in LoserPoolPoints.query.filter_by(season_year=season_year).all()
    }
    missing_loser_points = [t for t in all_teams if t.id not in teams_with_points]

    return render_template(
        "admin/reports.html",
        total_users=total_users,
        pool_entry_counts=pool_entry_counts,
        pool_labels=POOL_LABELS,
        current_week=current_week,
        missing_picks=missing_picks,
        pending_games=pending_games,
        dropdead_alive=dropdead_alive,
        dropdead_total=dropdead_total,
        missing_loser_points=missing_loser_points,
    )


@bp.route("/activity")
def activity():
    """Audit trail: pick a player and see everything they did while logged in."""
    users = User.query.order_by(User.username).all()
    selected_id = request.args.get("player", type=int)
    action_filter = request.args.get("action", "").strip()

    q = ActivityLog.query
    if selected_id:
        q = q.filter(ActivityLog.user_id == selected_id)
    if action_filter:
        q = q.filter(ActivityLog.action == action_filter)
    entries = q.order_by(ActivityLog.created_at.desc()).limit(500).all()

    actions = sorted({a for (a,) in db.session.query(ActivityLog.action).distinct().all() if a})
    selected_user = User.query.get(selected_id) if selected_id else None
    return render_template(
        "admin/activity.html",
        users=users,
        entries=entries,
        selected_user=selected_user,
        selected_id=selected_id,
        actions=actions,
        action_filter=action_filter,
        POOL_LABELS=POOL_LABELS,
    )


@bp.route("/payments")
def payments():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = (
        Entry.query.filter_by(season_year=season_year)
        .join(User)
        .order_by(User.username, Entry.pool)
        .all()
    )

    # models.POOL_ENTRY_FEES is the one definition; the member Billing page
    # quotes the same table, so the two views can never drift apart.
    fees = dict(POOL_ENTRY_FEES)
    # A buy-back is money owed just like an entry fee, and until now the page
    # never billed it: a revived Drop Dead entry showed "Paid" and $0 owed
    # while its $30 was outstanding. The Loser Pool has no buy-back.
    # Drop Dead is the only pool with a buy-back. Gridiron's was removed in
    # August 2026; any legacy rows are billed at $0 and shown nowhere.
    buyback_fees = {"dropdead": DROPDEAD_BUYBACK_FEE, "gridiron": 0, "loser": 0}

    # One BuyBack row per buy-back taken, each with its own paid flag, so an
    # entry that died and came back twice is billed twice and either fee can
    # be settled on its own.
    buybacks = {}
    for e in entries:
        rows = sorted(e.buy_backs, key=lambda b: b.id) if buyback_fees[e.pool] else []
        unpaid = [b for b in rows if not b.paid]
        buybacks[e.id] = {
            "rows": rows,
            "used": len(rows),
            "paid": sum(1 for b in rows if b.paid),
            "unpaid": len(unpaid),
            "fee": buyback_fees[e.pool],
            "owed": sum(b.fee for b in unpaid),
        }

    by_player = {}
    for e in entries:
        row = by_player.setdefault(e.user.username, {"dropdead": [], "loser": [], "gridiron": [], "owed": 0})
        row[e.pool].append(e)
        if not e.paid:
            row["owed"] += fees[e.pool]
        row["owed"] += buybacks[e.id]["owed"]

    # Drop Dead buy-backs get a column each (BB1-BB4, the weeks the printed
    # rules allow one). Each slot is "paid", "unpaid", or None for a buy-back
    # that was never taken. Buy-backs on an entry are interchangeable, so the
    # first `paid` of them are the settled ones -- the same convention the
    # rest of this page uses.
    for row in by_player.values():
        slots = [None, None, None, None]
        filled = 0
        for e in row["dropdead"]:
            for row_bb in buybacks[e.id]["rows"]:
                if filled > 3:
                    break
                # The row itself, so the badge posts to that exact buy-back.
                slots[filled] = row_bb
                filled += 1
        row["bb_slots"] = slots

    player_rows = sorted(by_player.items())

    # The All Entries table lists a buy-back as its own line rather than as a
    # column hanging off the entry it belongs to: same player, same pool, the
    # next entry number, and the buy-back's own fee and paid status. That is
    # how the commissioners count the money -- a buy-back is another stake in
    # the pool, not an annotation on an existing one.
    #
    # Numbering runs per (player, pool): the real entries first, then the
    # buy-backs continuing the sequence. Buy-backs on one entry are
    # interchangeable, so the first `buy_backs_paid` of them are the settled
    # ones and each row's button just moves that counter.
    grouped = {}
    for e in entries:
        grouped.setdefault((e.user.username, e.pool), []).append(e)

    # When each charge was committed to -- the moment the player took it on,
    # not when it was paid. Joining a pool commits the entry fee, so that is
    # the entry's created_at. Pressing a buy-back button commits that fee, and
    # the only record of when is the activity row written at the time.
    buyback_commits = {}
    for row in (
        ActivityLog.query.filter_by(action="buyback")
        .order_by(ActivityLog.created_at)
        .all()
    ):
        buyback_commits.setdefault((row.user_id, row.pool), []).append(row.created_at)

    entry_rows = []
    for (username, pool), group in grouped.items():
        number = 1
        for e in group:
            entry_rows.append({
                "username": username, "pool": pool,
                "label": f"Entry {number}", "fee": fees[pool],
                "paid": bool(e.paid), "kind": "entry", "entry_id": e.id,
                "order": number, "committed_at": e.created_at,
            })
            number += 1
        for e in group:
            commits = list(buyback_commits.get((e.user_id, pool), []))
            for i, b in enumerate(buybacks[e.id]["rows"]):
                entry_rows.append({
                    "username": username, "pool": pool,
                    "label": f"Entry {number}", "fee": b.fee,
                    "paid": bool(b.paid), "kind": "buyback", "buyback_id": b.id,
                    "entry_id": e.id, "order": number,
                    "committed_at": b.created_at or (commits[i] if i < len(commits) else None),
                })
                number += 1
    entry_rows.sort(key=lambda r: (r["username"].lower(), r["pool"], r["order"]))

    return render_template(
        "admin/payments.html",
        entries=entries,
        pool_labels=POOL_LABELS,
        player_rows=player_rows,
        fees=fees,
        buybacks=buybacks,
        entry_rows=entry_rows,
    )


@bp.route("/entries/<int:entry_id>/toggle-paid", methods=["POST"])
def toggle_paid(entry_id):
    entry = Entry.query.get_or_404(entry_id)
    entry.paid = not entry.paid
    # Stamped so the player's Billing page can say when it was settled.
    entry.paid_at = now() if entry.paid else None
    db.session.commit()
    # Filed against the PLAYER, not the admin who clicked: this is money on
    # that player's account, so it belongs in their activity, with the admin
    # named in the detail so the trail still says who did it.
    log_activity(
        "payment",
        f"{POOL_LABELS[entry.pool]} entry fee (${POOL_ENTRY_FEES.get(entry.pool, 0)}) "
        f"marked {'PAID' if entry.paid else 'UNPAID'} by {current_user.username}",
        pool=entry.pool,
        user=entry.user,
    )
    flash(f"{entry.user.username} ({POOL_LABELS[entry.pool]}, {entry.label}) marked {'paid' if entry.paid else 'unpaid'}.", "success")
    return redirect(url_for("admin.payments"))


@bp.route("/buybacks/<int:buyback_id>/paid", methods=["POST"])
def toggle_buyback_paid(buyback_id):
    """Settle (or un-settle) one specific buy-back fee.

    Each buy-back is its own row, so this marks exactly the one the admin
    clicked. The counter on Entry is kept in step for the code that still
    counts buy-backs off it.
    """
    bb = BuyBack.query.get_or_404(buyback_id)
    entry = bb.entry
    bb.paid = not bb.paid
    bb.paid_at = now() if bb.paid else None
    settled = sum(1 for b in entry.buy_backs if b.paid)
    entry.buy_backs_paid = settled
    entry.buy_backs_paid_at = now() if settled else None
    db.session.commit()
    log_activity(
        "payment",
        f"{POOL_LABELS[entry.pool]} buy-back (${bb.fee})"
        + (f" from week {bb.week_number}" if bb.week_number else "")
        + f" marked {'PAID' if bb.paid else 'UNPAID'} by {current_user.username}",
        pool=entry.pool,
        user=entry.user,
    )
    flash(
        f"{entry.user.username} ({POOL_LABELS[entry.pool]}): buy-back "
        f"{'marked paid' if bb.paid else 'marked unpaid'} "
        f"({settled} of {len(entry.buy_backs)} settled).",
        "success",
    )
    return redirect(url_for("admin.payments"))


@bp.route("/messages")
def messages():
    all_messages = ContactMessage.query.order_by(ContactMessage.created_at.asc()).all()
    threads = {}
    for m in all_messages:
        threads.setdefault(m.user_id, []).append(m)
    thread_rows = []
    for user_id, msgs in threads.items():
        unread = sum(1 for m in msgs if not m.from_admin and not m.is_read)
        thread_rows.append({"user": msgs[-1].user, "last": msgs[-1], "unread": unread, "count": len(msgs)})
    thread_rows.sort(key=lambda r: (r["unread"] == 0, -r["last"].created_at.timestamp()))
    unread_count = sum(r["unread"] for r in thread_rows)
    return render_template("admin/messages.html", threads=thread_rows, unread_count=unread_count)


@bp.route("/messages/<int:user_id>")
def message_thread(user_id):
    player = User.query.get_or_404(user_id)
    thread = (
        ContactMessage.query.filter_by(user_id=user_id)
        .order_by(ContactMessage.created_at.asc())
        .all()
    )
    unread = [m for m in thread if not m.from_admin and not m.is_read]
    if unread:
        for m in unread:
            m.is_read = True
        db.session.commit()
    return render_template("admin/message_thread.html", player=player, thread=thread)


@bp.route("/messages/<int:user_id>/reply", methods=["POST"])
def reply_message(user_id):
    player = User.query.get_or_404(user_id)
    body = request.form.get("body", "").strip()
    if not body:
        flash("Reply can't be empty.", "error")
        return redirect(url_for("admin.message_thread", user_id=user_id))
    message = ContactMessage(user_id=player.id, sender_id=current_user.id, body=body)
    db.session.add(message)
    db.session.commit()

    # Let the player know there is an answer waiting, the same way the
    # commissioners are told a message arrived.
    if player.email:
        site_url = get_setting("site_url", DEFAULT_SITE_URL) or DEFAULT_SITE_URL
        link = f"{site_url}{url_for('board.index')}"
        send_async(send_player_message_email, player, body, link)
        note = f" {player.username} was emailed."
    else:
        note = f" {player.username} has no email on file, so they were not notified."
    flash(f"Reply sent to {player.username}.{note}", "success")
    return redirect(url_for("admin.message_thread", user_id=user_id))


@bp.route("/messages/<int:user_id>/<int:message_id>/delete", methods=["POST"])
def delete_message(user_id, message_id):
    message = ContactMessage.query.filter_by(id=message_id, user_id=user_id).first_or_404()
    db.session.delete(message)
    db.session.commit()
    flash("Message deleted.", "success")
    return redirect(url_for("admin.message_thread", user_id=user_id))


@bp.route("/messages/<int:user_id>/delete-thread", methods=["POST"])
def delete_thread(user_id):
    player = User.query.get_or_404(user_id)
    count = ContactMessage.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    db.session.commit()
    flash(f"Deleted the entire conversation with {player.username} ({count} message{'s' if count != 1 else ''}).", "success")
    return redirect(url_for("admin.messages"))


@bp.route("/loser-points", methods=["GET", "POST"])
def loser_points():
    season_year = current_app.config["CURRENT_SEASON"]
    if request.method == "POST":
        for team in Team.query.all():
            val = request.form.get(f"points_{team.id}")
            if val is None or val == "":
                continue
            lp = LoserPoolPoints.query.filter_by(season_year=season_year, team_id=team.id).first()
            if lp:
                lp.points = int(val)
            else:
                db.session.add(LoserPoolPoints(season_year=season_year, team_id=team.id, points=int(val)))
        db.session.commit()
        flash("Loser Pool point values updated.", "success")
        return redirect(url_for("admin.loser_points"))

    teams = Team.query.order_by(Team.name).all()
    current_points = {
        lp.team_id: lp.points for lp in LoserPoolPoints.query.filter_by(season_year=season_year).all()
    }
    return render_template("admin/loser_points.html", teams=teams, current_points=current_points)


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        api_key = request.form.get("odds_api_key", "").strip()
        season_start = request.form.get("season_start_thursday", "").strip()
        site_url = request.form.get("site_url", "").strip()
        sendgrid_api_key = request.form.get("sendgrid_api_key", "").strip()
        sendgrid_from_email = request.form.get("sendgrid_from_email", "").strip()
        if api_key:
            set_setting("odds_api_key", api_key)
        if season_start:
            set_setting("season_start_thursday", season_start)
        if site_url:
            set_setting("site_url", site_url.rstrip("/"))
        if sendgrid_api_key:
            set_setting("sendgrid_api_key", sendgrid_api_key)
        if sendgrid_from_email:
            set_setting("sendgrid_from_email", sendgrid_from_email)
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        odds_api_key=get_setting("odds_api_key", ""),
        season_start_thursday=get_setting("season_start_thursday", ""),
        site_url=get_setting("site_url", DEFAULT_SITE_URL) or DEFAULT_SITE_URL,
        sendgrid_api_key=get_setting("sendgrid_api_key", ""),
        sendgrid_from_email=get_setting("sendgrid_from_email", ""),
    )


@bp.route("/popup-announcement", methods=["GET", "POST"])
def popup_announcement():
    if request.method == "POST":
        body = request.form.get("body", "").strip()
        set_setting("popup_announcement", body)
        if body:
            # bump the id so every player's "have I seen this one" check
            # resets, even if the text happens to match a previous one
            next_id = int(get_setting("popup_announcement_id", "0") or "0") + 1
            set_setting("popup_announcement_id", str(next_id))
            flash("Popup announcement is live -- players will see it once, next time they load a page.", "success")
        else:
            flash("Popup announcement cleared.", "success")
        return redirect(url_for("admin.popup_announcement"))

    return render_template(
        "admin/popup_announcement.html",
        current_body=get_setting("popup_announcement", ""),
    )


@bp.route("/reset-test-data", methods=["POST"])
def reset_test_data():
    if request.form.get("confirm_text", "").strip() != "DELETE":
        flash('You must type "DELETE" exactly to confirm the reset.', "error")
        return redirect(url_for("admin.settings"))

    db_path = current_app.config["SQLALCHEMY_DATABASE_URI"].removeprefix("sqlite:///")
    if os.path.exists(db_path):
        backup_dir = os.path.join(os.path.dirname(db_path), "old_backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(db_path, os.path.join(backup_dir, f"pools_pre_reset_{stamp}.db"))

    # Children before parents, to avoid orphaning rows on SQLite's ID reuse.
    Pick.query.delete()
    GridironMiss.query.delete()
    Game.query.delete()
    Week.query.delete()
    Entry.query.delete()
    Announcement.query.delete()
    ContactMessage.query.delete()
    db.session.commit()

    flash("All test data (weeks, games, picks, entries, announcements, messages) has been wiped. Player logins were kept.", "success")
    return redirect(url_for("admin.settings"))


@bp.route("/invite", methods=["GET", "POST"])
def invite():
    if request.method == "POST":
        raw = request.form.get("emails", "")
        candidates = [e.strip() for e in re.split(r"[,\n\r]+", raw) if e.strip()]
        valid = sorted(set(e for e in candidates if EMAIL_RE.match(e)))
        invalid = sorted(set(e for e in candidates if e not in valid))

        if not valid:
            flash("No valid email addresses found.", "error")
            return redirect(url_for("admin.invite"))

        # An address that already has an account is not invited again. A second
        # invite would be a second registration link, and registering through it
        # builds a WHOLE SECOND ACCOUNT on that address rather than signing the
        # person in -- someone re-invited by mistake ends up with two teams they
        # never asked for and two lines in the standings. Extra teams are what
        # "Add Another Account" is for, from inside the account.
        registered = {
            (row[0] or "").strip().lower()
            for row in db.session.query(User.email).all()
        }
        already = sorted(e for e in valid if e.lower() in registered)
        valid = [e for e in valid if e.lower() not in registered]

        # A live unused invite is reused rather than replaced, so a resend
        # delivers the SAME link. Minting a second token left the first one
        # working too, and every one of them was another way into a duplicate
        # account.
        existing = {
            (row.email or "").strip().lower(): row
            for row in Invite.query.filter(Invite.used_at.is_(None)).all()
        }

        if not valid:
            msg = "Nobody new to invite."
            if already:
                msg += f" Already registered: {', '.join(already)}."
            if invalid:
                msg += f" Not a valid address: {', '.join(invalid)}."
            flash(msg, "error")
            return redirect(url_for("admin.invite"))

        site_url = get_setting("site_url", DEFAULT_SITE_URL) or DEFAULT_SITE_URL
        email_links = []
        resent = []
        for email in valid:
            invite_row = existing.get(email.lower())
            if invite_row is not None:
                resent.append(email)
            else:
                invite_row = Invite(email=email, token=secrets.token_urlsafe(32))
                db.session.add(invite_row)
                db.session.flush()
            email_links.append((email, f"{site_url}{url_for('auth.register', token=invite_row.token)}"))
        db.session.commit()
        send_async(send_invite_link_emails, email_links)

        msg = f"Invite sent to {len(valid)} address{'es' if len(valid) != 1 else ''}."
        if resent:
            msg += (f" {len(resent)} already had an unused invite, so the same link "
                    f"was sent again: {', '.join(resent)}.")
        if already:
            msg += (f" Skipped {len(already)} that already " 
                    f"{'has' if len(already) == 1 else 'have'} an account: {', '.join(already)}.")
        if invalid:
            msg += f" Skipped {len(invalid)} invalid: {', '.join(invalid)}."
        flash(msg, "success" if not (invalid or already) else "error")
        return redirect(url_for("admin.invite"))

    return render_template("admin/invite.html")


@bp.route("/publish-now", methods=["POST"])
def publish_now():
    from flask import current_app as _app

    try:
        summary = publish_week(_app._get_current_object())
    except Exception as exc:
        flash(f"Publish failed: {exc}", "error")
        return redirect(url_for("admin.settings"))

    msg = (
        f"Published {summary['week_label']}: {summary['created']} games added, "
        f"{summary['already_published']} already published (lines frozen, unchanged)."
    )
    if summary["unmatched"]:
        msg += f" Unmatched NFL team names: {', '.join(summary['unmatched'])}."
    flash(msg, "success" if not summary["unmatched"] else "error")
    return redirect(url_for("admin.dashboard"))
