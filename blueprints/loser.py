from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import deadline_passed, log_activity, game_pickable, get_current_week, team_game_this_week, team_matchups_for_week
from team_colors import styles_for
from models import Entry, Game, LoserPoolPoints, Pick, Team, db
from scoring import ensure_missed_processed, standings_loser

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
    week = get_current_week(season_year, "loser")
    ensure_missed_processed(week)
    locked = deadline_passed(week)

    if request.method == "POST":
        entry_id = int(request.form["entry_id"])
        # The form offers radios per game plus a fallback select for teams with
        # no listed game; both post `team_id`, and either may be empty.
        raw_team_id = next((v for v in request.form.getlist("team_id") if v.strip()), "")
        if not raw_team_id:
            flash("Choose a team to LOSE before saving.", "error")
            return redirect(url_for("loser.pick"))
        try:
            team_id = int(raw_team_id)
        except ValueError:
            flash("That isn't a valid team.", "error")
            return redirect(url_for("loser.pick"))
        entry = Entry.query.get_or_404(entry_id)
        if entry.user_id != current_user.id:
            flash("Not your entry.", "error")
            return redirect(url_for("loser.pick"))
        if locked:
            flash("The pick deadline for this week has passed.", "error")
            return redirect(url_for("loser.pick"))
        if Pick.query.filter_by(entry_id=entry.id, week_id=week.id).first():
            flash("You already have a pick locked in for this week. Remove it first if you want to pick a different team.", "error")
            return redirect(url_for("loser.pick"))
        team_game = team_game_this_week(team_id, week.id, pool="loser")
        if not game_pickable(team_game):
            flash("Too late to pick that team — picks lock 1 hour before their game's kickoff.", "error")
            return redirect(url_for("loser.pick"))
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="loser", team_id=team_id))
        db.session.commit()
        team = Team.query.get(team_id)
        log_activity(
            "pick_saved",
            f"{week.label}: picked {team} to LOSE",
            pool="loser",
        )
        flash("Pick saved and locked in for the week.", "success")
        return redirect(url_for("loser.pick"))

    teams = Team.query.order_by(Team.name).all()
    points_by_team = {
        lp.team_id: lp.points for lp in LoserPoolPoints.query.filter_by(season_year=season_year).all()
    }
    picks_this_week = {}
    removable_picks = {}
    unpickable_team_ids = set()
    team_matchups = {}
    games = []
    teams_with_games = set()
    if week:
        for e in entries:
            p = Pick.query.filter_by(entry_id=e.id, week_id=week.id).first()
            picks_this_week[e.id] = p
            if p and not locked:
                team_game = team_game_this_week(p.team_id, week.id, pool="loser")
                removable_picks[e.id] = game_pickable(team_game)
        for g in Game.query.filter_by(week_id=week.id, pool="loser").all():
            if not game_pickable(g):
                unpickable_team_ids.update(t for t in (g.home_team_id, g.away_team_id) if t is not None)
        team_matchups = team_matchups_for_week(week.id, "loser")
        # Ordered game list so the pick page can lay out one row per matchup
        # instead of cramming every team + its game into one dropdown.
        games = (
            Game.query.filter_by(week_id=week.id, pool="loser")
            .order_by(Game.kickoff.asc().nullslast())
            .all()
        )
        for g in games:
            teams_with_games.update(t for t in (g.home_team_id, g.away_team_id) if t is not None)

    running_totals = {e.id: sum(p.points or 0 for p in e.picks) for e in entries}

    return render_template(
        "loser/pick.html",
        entries=entries,
        week=week,
        locked=locked,
        teams=teams,
        points_by_team=points_by_team,
        picks_this_week=picks_this_week,
        removable_picks=removable_picks,
        team_matchups=team_matchups,
        running_totals=running_totals,
        unpickable_team_ids=unpickable_team_ids,
        games=games,
        teams_with_games=teams_with_games,
        team_styles=styles_for(
            [g.away_team for g in games] + [g.home_team for g in games]
        ),
    )


@bp.route("/picks/<int:pick_id>/remove", methods=["POST"])
@login_required
def remove_pick(pick_id):
    pick = Pick.query.get_or_404(pick_id)
    if pick.entry.user_id != current_user.id:
        flash("Not your pick.", "error")
        return redirect(url_for("loser.pick"))
    week = pick.week
    if deadline_passed(week):
        flash("The pick deadline for this week has passed.", "error")
        return redirect(url_for("loser.pick"))
    team_game = team_game_this_week(pick.team_id, week.id, pool="loser")
    if not game_pickable(team_game):
        flash("Too late to remove that pick — it locks 1 hour before kickoff.", "error")
        return redirect(url_for("loser.pick"))
    removed = str(pick.team) if pick.team else "pick"
    removed_week = week.label if week else ""
    db.session.delete(pick)
    db.session.commit()
    log_activity("pick_removed", f"{removed_week}: removed pick {removed}", pool="loser")
    flash("Pick removed — you can make a new selection.", "success")
    return redirect(url_for("loser.pick"))


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    rows = standings_loser(season_year)
    return render_template("loser/standings.html", rows=rows)
