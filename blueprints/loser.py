from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import deadline_passed, game_pickable, get_current_week, send_async, team_game_this_week
from models import Entry, Game, LoserPoolPoints, Pick, Team, db
from notifications import email_entry_pick_confirmation
from scoring import standings_loser

bp = Blueprint("loser", __name__)


@bp.route("/rules")
def rules():
    season_year = current_app.config["CURRENT_SEASON"]
    points = (
        LoserPoolPoints.query.filter_by(season_year=season_year)
        .join(Team)
        .order_by(LoserPoolPoints.points)
        .all()
    )
    return render_template("loser/rules.html", points=points)


@bp.route("/join", methods=["POST"])
@login_required
def join():
    season_year = current_app.config["CURRENT_SEASON"]
    existing = Entry.query.filter_by(user_id=current_user.id, pool="loser", season_year=season_year).first()
    if existing:
        flash("You already have an entry in the Loser Pool (one per account). For another entry, register a separate account.", "error")
        return redirect(url_for("loser.pick"))
    entry = Entry(user_id=current_user.id, pool="loser", season_year=season_year, label="Entry 1")
    db.session.add(entry)
    db.session.commit()
    flash("You're in the Loser Pool. Pick your losers wisely.", "success")
    return redirect(url_for("loser.pick"))


@bp.route("/pick", methods=["GET", "POST"])
@login_required
def pick():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = Entry.query.filter_by(user_id=current_user.id, pool="loser", season_year=season_year).all()
    week = get_current_week(season_year)
    locked = deadline_passed(week)

    if request.method == "POST":
        entry_id = int(request.form["entry_id"])
        team_id = int(request.form["team_id"])
        entry = Entry.query.get_or_404(entry_id)
        if entry.user_id != current_user.id:
            flash("Not your entry.", "error")
            return redirect(url_for("loser.pick"))
        if locked:
            flash("The pick deadline for this week has passed.", "error")
            return redirect(url_for("loser.pick"))
        if Pick.query.filter_by(entry_id=entry.id, week_id=week.id).first():
            flash("Your pick for this week is already locked in and can't be changed.", "error")
            return redirect(url_for("loser.pick"))
        team_game = team_game_this_week(team_id, week.id)
        if not game_pickable(team_game):
            flash("Too late to pick that team — picks lock 1 hour before their game's kickoff.", "error")
            return redirect(url_for("loser.pick"))
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="loser", team_id=team_id))
        db.session.commit()
        send_async(email_entry_pick_confirmation, current_user.id, week.id, "loser", entry.id)
        flash("Pick saved and locked in for the week.", "success")
        return redirect(url_for("loser.pick"))

    teams = Team.query.order_by(Team.name).all()
    points_by_team = {
        lp.team_id: lp.points for lp in LoserPoolPoints.query.filter_by(season_year=season_year).all()
    }
    picks_this_week = {}
    unpickable_team_ids = set()
    if week:
        for e in entries:
            picks_this_week[e.id] = Pick.query.filter_by(entry_id=e.id, week_id=week.id).first()
        for g in Game.query.filter_by(week_id=week.id).all():
            if not game_pickable(g):
                unpickable_team_ids.update(t for t in (g.home_team_id, g.away_team_id) if t is not None)

    running_totals = {e.id: sum(p.points or 0 for p in e.picks) for e in entries}

    return render_template(
        "loser/pick.html",
        entries=entries,
        week=week,
        locked=locked,
        teams=teams,
        points_by_team=points_by_team,
        picks_this_week=picks_this_week,
        running_totals=running_totals,
        unpickable_team_ids=unpickable_team_ids,
    )


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    rows = standings_loser(season_year)
    return render_template("loser/standings.html", rows=rows)
