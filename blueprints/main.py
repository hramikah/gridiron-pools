from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from helpers import week_unlocked
from models import Entry, Game, User, Week
from pdf_report import build_week_pdf
from scoring import (
    dropdead_matrix,
    dropdead_status_through_week,
    gridiron_matrix,
    gridiron_record_through_week,
    loser_matrix,
    loser_totals_through_week,
    player_pick_history,
    standings_dropdead,
    standings_gridiron,
    standings_loser,
)

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    season_year = current_app.config["CURRENT_SEASON"]
    my_entries = {}
    if current_user.is_authenticated:
        for e in Entry.query.filter_by(user_id=current_user.id, season_year=season_year).all():
            my_entries.setdefault(e.pool, []).append(e)
    return render_template("home.html", my_entries=my_entries)


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    weeks = Week.query.filter_by(season_year=season_year).order_by(Week.number).all()
    unlocked_weeks = [w for w in weeks if week_unlocked(w)]

    history_week = request.args.get("week", type=int)
    history_data = None
    if history_week is not None and any(w.number == history_week for w in unlocked_weeks):
        history_data = {
            "dropdead": dropdead_status_through_week(season_year, history_week),
            "loser": loser_totals_through_week(season_year, history_week),
            "gridiron": gridiron_record_through_week(season_year, history_week),
        }

    player_ids_with_entries = {
        e.user_id for e in Entry.query.filter_by(season_year=season_year).all()
    }
    players = User.query.filter(User.id.in_(player_ids_with_entries)).order_by(User.username).all()
    selected_player_id = request.args.get("player", type=int)
    player_history = None
    if selected_player_id is not None and any(p.id == selected_player_id for p in players):
        player_history = player_pick_history(season_year, selected_player_id)

    unlocked_week_numbers = [w.number for w in unlocked_weeks]
    all_weeks_data = {
        "dropdead": dropdead_matrix(season_year, unlocked_week_numbers),
        "loser": loser_matrix(season_year, unlocked_week_numbers),
        "gridiron": gridiron_matrix(season_year, unlocked_week_numbers),
    }

    gridiron_recent_picks = {}
    gridiron_recent_week = max(unlocked_week_numbers) if unlocked_week_numbers else None
    if gridiron_recent_week is not None:
        for entry, _wins, _losses, _ties, week_picks in gridiron_record_through_week(season_year, gridiron_recent_week):
            gridiron_recent_picks[entry.id] = week_picks

    return render_template(
        "standings.html",
        dropdead_entries=standings_dropdead(season_year),
        loser_rows=standings_loser(season_year),
        gridiron_rows=standings_gridiron(season_year),
        unlocked_weeks=unlocked_weeks,
        history_week=history_week,
        history_data=history_data,
        players=players,
        selected_player_id=selected_player_id,
        player_history=player_history,
        unlocked_week_numbers=unlocked_week_numbers,
        all_weeks_data=all_weeks_data,
        gridiron_recent_picks=gridiron_recent_picks,
        gridiron_recent_week=gridiron_recent_week,
    )


@bp.route("/scores")
def scores():
    season_year = current_app.config["CURRENT_SEASON"]
    weeks = Week.query.filter_by(season_year=season_year).order_by(Week.number.desc()).all()

    by_week = []
    for week in weeks:
        games = (
            Game.query.filter_by(week_id=week.id, is_final=True)
            .order_by(Game.sport, Game.away_team)
            .all()
        )
        if games:
            by_week.append((week, games))

    return render_template("scores.html", by_week=by_week)


@bp.route("/reports")
@login_required
def reports():
    season_year = current_app.config["CURRENT_SEASON"]
    weeks = Week.query.filter_by(season_year=season_year).order_by(Week.number).all()
    return render_template(
        "reports.html",
        weeks=[(w, week_unlocked(w)) for w in weeks],
    )


@bp.route("/weeks/<int:week_id>/picks.pdf")
@login_required
def week_picks_pdf(week_id):
    week = Week.query.get_or_404(week_id)
    if not week_unlocked(week):
        flash("This week's picks report unlocks once the pick deadline has passed.", "error")
        return redirect(url_for("main.reports"))
    buf = build_week_pdf(week)
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"week_{week.number}_picks.pdf",
    )
