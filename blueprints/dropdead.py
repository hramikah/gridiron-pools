from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import deadline_passed, game_pickable, get_current_week, send_async, team_game_this_week
from models import Entry, Game, Pick, Team, db
from notifications import email_entry_pick_confirmation
from scoring import ensure_missed_processed, standings_dropdead

bp = Blueprint("dropdead", __name__)


@bp.route("/rules")
def rules():
    return render_template("dropdead/rules.html")


@bp.route("/join", methods=["POST"])
@login_required
def join():
    season_year = current_app.config["CURRENT_SEASON"]
    existing = Entry.query.filter_by(user_id=current_user.id, pool="dropdead", season_year=season_year).first()
    if existing:
        flash("You already have an entry in the Drop Dead Pool (one per person).", "error")
        return redirect(url_for("dropdead.pick"))
    entry = Entry(user_id=current_user.id, pool="dropdead", season_year=season_year, label="Entry 1")
    db.session.add(entry)
    db.session.commit()
    flash("You're in the Drop Dead Pool. Good luck!", "success")
    return redirect(url_for("dropdead.pick"))


@bp.route("/pick", methods=["GET", "POST"])
@login_required
def pick():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = Entry.query.filter_by(user_id=current_user.id, pool="dropdead", season_year=season_year).all()
    week = get_current_week(season_year, "dropdead")
    ensure_missed_processed(week)
    locked = deadline_passed(week)

    if request.method == "POST":
        entry_id = int(request.form["entry_id"])
        team_id = int(request.form["team_id"])
        entry = Entry.query.get_or_404(entry_id)
        if entry.user_id != current_user.id:
            flash("Not your entry.", "error")
            return redirect(url_for("dropdead.pick"))
        if not entry.is_active:
            flash("That entry has been eliminated.", "error")
            return redirect(url_for("dropdead.pick"))
        if locked:
            flash("The pick deadline for this week has passed.", "error")
            return redirect(url_for("dropdead.pick"))
        if Pick.query.filter_by(entry_id=entry.id, week_id=week.id).first():
            flash("Your pick for this week is already locked in and can't be changed.", "error")
            return redirect(url_for("dropdead.pick"))
        if team_id in entry.used_team_ids():
            flash("You've already used that team this season.", "error")
            return redirect(url_for("dropdead.pick"))
        team_game = team_game_this_week(team_id, week.id, pool="dropdead")
        if not game_pickable(team_game):
            flash("Too late to pick that team — picks lock 1 hour before their game's kickoff.", "error")
            return redirect(url_for("dropdead.pick"))
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=team_id))
        db.session.commit()
        send_async(email_entry_pick_confirmation, current_user.id, week.id, "dropdead", entry.id)
        flash("Pick saved and locked in for the week.", "success")
        return redirect(url_for("dropdead.pick"))

    teams = Team.query.order_by(Team.name).all()
    picks_this_week = {}
    unpickable_team_ids = set()
    if week:
        for e in entries:
            p = Pick.query.filter_by(entry_id=e.id, week_id=week.id).first()
            picks_this_week[e.id] = p
        for g in Game.query.filter_by(week_id=week.id, pool="dropdead").all():
            if not game_pickable(g):
                unpickable_team_ids.update(t for t in (g.home_team_id, g.away_team_id) if t is not None)

    return render_template(
        "dropdead/pick.html",
        entries=entries,
        week=week,
        locked=locked,
        teams=teams,
        picks_this_week=picks_this_week,
        unpickable_team_ids=unpickable_team_ids,
    )


@bp.route("/buyback/<int:entry_id>", methods=["POST"])
@login_required
def buyback(entry_id):
    entry = Entry.query.get_or_404(entry_id)
    if entry.user_id != current_user.id:
        flash("Not your entry.", "error")
        return redirect(url_for("dropdead.pick"))
    if entry.is_active:
        flash("That entry is still alive.", "error")
        return redirect(url_for("dropdead.pick"))
    if not entry.eliminated_week or entry.eliminated_week > 4:
        flash("Buy-backs are only available for eliminations in weeks 1-4.", "error")
        return redirect(url_for("dropdead.pick"))
    week = get_current_week(entry.season_year, "dropdead")
    if week and week.number != entry.eliminated_week:
        flash("The buy-back window closed once the next week's picks opened.", "error")
        return redirect(url_for("dropdead.pick"))
    entry.is_active = True
    entry.buy_backs_used += 1
    db.session.commit()
    flash("Entry revived. ($30 buy-back fee due to the commissioners.)", "success")
    return redirect(url_for("dropdead.pick"))


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = standings_dropdead(season_year)
    return render_template("dropdead/standings.html", entries=entries)
