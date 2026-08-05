import re
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import admin_required, deadline_passed, get_current_week, get_setting, send_async, set_setting
from mailer import send_invite_emails
from models import POOL_LABELS, POOLS, ContactMessage, Entry, Game, GridironMiss, LoserPoolPoints, Pick, Team, User, Week, db
from notifications import email_week_picks
from publisher import publish_week
from scoring import process_missed_picks, score_game

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
    weeks = Week.query.filter_by(season_year=season_year).order_by(Week.number).all()
    return render_template("admin/dashboard.html", weeks=weeks)


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
        value = int(request.form.get("max_teams", 1))
    except ValueError:
        value = 1
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


@bp.route("/weeks/new", methods=["POST"])
def new_week():
    season_year = current_app.config["CURRENT_SEASON"]
    number = int(request.form["number"])
    deadline_str = request.form["pick_deadline"]
    deadline = datetime.fromisoformat(deadline_str)
    if Week.query.filter_by(season_year=season_year, number=number).first():
        flash(f"Week {number} already exists.", "error")
        return redirect(url_for("admin.dashboard"))
    db.session.add(Week(season_year=season_year, number=number, pick_deadline=deadline))
    db.session.commit()
    flash(f"Week {number} created.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/weeks/<int:week_id>")
def week_detail(week_id):
    week = Week.query.get_or_404(week_id)
    teams = Team.query.order_by(Team.name).all()
    games = Game.query.filter_by(week_id=week.id).all()
    return render_template(
        "admin/week_detail.html",
        week=week,
        teams=teams,
        games=games,
        deadline_passed=deadline_passed(week),
    )


@bp.route("/weeks/<int:week_id>/games/new", methods=["POST"])
def new_game(week_id):
    week = Week.query.get_or_404(week_id)
    sport = request.form["sport"]
    home_team = request.form["home_team"].strip()
    away_team = request.form["away_team"].strip()
    home_team_id = request.form.get("home_team_id") or None
    away_team_id = request.form.get("away_team_id") or None
    favorite = request.form.get("favorite") or None
    spread = request.form.get("spread") or None
    over_under = request.form.get("over_under") or None
    is_mnf = bool(request.form.get("is_mnf"))

    game = Game(
        week_id=week.id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        home_team_id=int(home_team_id) if home_team_id else None,
        away_team_id=int(away_team_id) if away_team_id else None,
        favorite=favorite,
        spread=float(spread) if spread else None,
        over_under=float(over_under) if over_under else None,
        is_mnf=is_mnf,
    )
    db.session.add(game)
    db.session.commit()
    flash("Game added.", "success")
    return redirect(url_for("admin.week_detail", week_id=week.id))


@bp.route("/games/<int:game_id>/result", methods=["POST"])
def enter_result(game_id):
    game = Game.query.get_or_404(game_id)
    game.home_score = int(request.form["home_score"])
    game.away_score = int(request.form["away_score"])
    game.is_final = True
    db.session.commit()
    score_game(game)
    flash(f"{game.label} finalized and picks scored.", "success")
    return redirect(url_for("admin.week_detail", week_id=game.week_id))

@bp.route("/games/<int:game_id>/delete", methods=["POST"])
def delete_game(game_id):
    game = Game.query.get_or_404(game_id)
    week_id = game.week_id
    db.session.delete(game)
    db.session.commit()
    flash("Game removed.", "success")
    return redirect(url_for("admin.week_detail", week_id=week_id))


@bp.route("/weeks/<int:week_id>/process-missed", methods=["POST"])
def process_missed(week_id):
    week = Week.query.get_or_404(week_id)
    process_missed_picks(week)
    flash("Missed picks processed: Drop Dead no-shows eliminated, Loser Pool no-shows assigned the MNF visitor, Gridiron no-shows scored 0-5.", "success")
    return redirect(url_for("admin.week_detail", week_id=week.id))


@bp.route("/weeks/<int:week_id>/email-picks", methods=["POST"])
def email_picks(week_id):
    week = Week.query.get_or_404(week_id)
    if not deadline_passed(week):
        flash("This week's deadline hasn't passed yet.", "error")
        return redirect(url_for("admin.week_detail", week_id=week.id))
    if week.picks_emailed:
        flash("Picks for this week were already emailed.", "error")
        return redirect(url_for("admin.week_detail", week_id=week.id))
    count = email_week_picks(week)
    flash(f"Picks recap emailed to {count} player(s) for Week {week.number}.", "success")
    return redirect(url_for("admin.week_detail", week_id=week.id))


@bp.route("/reports")
def reports():
    season_year = current_app.config["CURRENT_SEASON"]

    total_users = User.query.count()
    pool_entry_counts = {
        pool: Entry.query.filter_by(pool=pool, season_year=season_year).count() for pool in POOLS
    }

    current_week = get_current_week(season_year)

    missing_picks = {}
    if current_week:
        for pool in POOLS:
            entries = Entry.query.filter_by(pool=pool, season_year=season_year).all()
            if pool == "dropdead":
                entries = [e for e in entries if e.is_active]
            picked_entry_ids = {
                p.entry_id for p in Pick.query.filter_by(pool=pool, week_id=current_week.id).all()
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


@bp.route("/payments")
def payments():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = (
        Entry.query.filter_by(season_year=season_year)
        .join(User)
        .order_by(User.username, Entry.pool)
        .all()
    )

    fees = {"dropdead": 20, "loser": 20, "gridiron": 100}
    by_player = {}
    for e in entries:
        row = by_player.setdefault(e.user.username, {"dropdead": [], "loser": [], "gridiron": [], "owed": 0})
        row[e.pool].append(e)
        if not e.paid:
            row["owed"] += fees[e.pool]
    player_rows = sorted(by_player.items())

    return render_template(
        "admin/payments.html",
        entries=entries,
        pool_labels=POOL_LABELS,
        player_rows=player_rows,
        fees=fees,
    )


@bp.route("/entries/<int:entry_id>/toggle-paid", methods=["POST"])
def toggle_paid(entry_id):
    entry = Entry.query.get_or_404(entry_id)
    entry.paid = not entry.paid
    db.session.commit()
    flash(f"{entry.user.username} ({POOL_LABELS[entry.pool]}, {entry.label}) marked {'paid' if entry.paid else 'unpaid'}.", "success")
    return redirect(url_for("admin.payments"))


@bp.route("/messages")
def messages():
    unread_count = ContactMessage.query.filter_by(is_read=False).count()
    messages_ = ContactMessage.query.order_by(ContactMessage.created_at.desc()).all()
    return render_template("admin/messages.html", messages=messages_, unread_count=unread_count)


@bp.route("/messages/<int:message_id>/mark-read", methods=["POST"])
def mark_message_read(message_id):
    message = ContactMessage.query.get_or_404(message_id)
    message.is_read = not message.is_read
    db.session.commit()
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
        if api_key:
            set_setting("odds_api_key", api_key)
        if season_start:
            set_setting("season_start_thursday", season_start)
        if site_url:
            set_setting("site_url", site_url.rstrip("/"))
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        odds_api_key=get_setting("odds_api_key", ""),
        season_start_thursday=get_setting("season_start_thursday", ""),
        site_url=get_setting("site_url", "http://100.71.232.56:8090"),
    )


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

        site_url = get_setting("site_url", "http://100.71.232.56:8090")
        send_async(send_invite_emails, valid, site_url)

        msg = f"Invite sent to {len(valid)} address{'es' if len(valid) != 1 else ''}."
        if invalid:
            msg += f" Skipped {len(invalid)} invalid: {', '.join(invalid)}"
        flash(msg, "success" if not invalid else "error")
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
        f"Published Week {summary['week_number']}: {summary['created']} games added, "
        f"{summary['already_published']} already published (lines frozen, unchanged)."
    )
    if summary["unmatched"]:
        msg += f" Unmatched NFL team names: {', '.join(summary['unmatched'])}."
    flash(msg, "success" if not summary["unmatched"] else "error")
    return redirect(url_for("admin.dashboard"))
