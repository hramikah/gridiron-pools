from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import deadline_passed, log_activity, game_pickable, get_current_week, team_game_this_week, team_matchups_for_week
from team_colors import styles_for
from models import Entry, Game, Pick, Team, Week, db
from scoring import dropdead_buyback_available, dropdead_eliminated_for_no_pick, ensure_missed_processed, standings_dropdead

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
        raw_team_id = next((v for v in request.form.getlist("team_id") if v.strip()), "")
        if not raw_team_id:
            flash("Choose a team to win outright before saving.", "error")
            return redirect(url_for("dropdead.pick"))
        try:
            team_id = int(raw_team_id)
        except ValueError:
            flash("That isn't a valid team.", "error")
            return redirect(url_for("dropdead.pick"))
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
            flash("You already have a pick locked in for this week. Remove it first if you want to pick a different team.", "error")
            return redirect(url_for("dropdead.pick"))
        if team_id in entry.used_team_ids():
            flash("You've already used that team this season.", "error")
            return redirect(url_for("dropdead.pick"))
        team_game = team_game_this_week(team_id, week.id, pool="dropdead")
        # A team on a bye has no game this week, so there's nothing to win --
        # it can't be picked. Checked here as well as hidden from the page,
        # since game_pickable() treats "no game" as pickable for other callers.
        if team_game is None:
            flash("That team isn't playing this week, so it can't be picked.", "error")
            return redirect(url_for("dropdead.pick"))
        if not game_pickable(team_game):
            flash("Too late to pick that team — picks lock 1 hour before their game's kickoff.", "error")
            return redirect(url_for("dropdead.pick"))
        db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=team_id))
        db.session.commit()
        team = Team.query.get(team_id)
        log_activity(
            "pick_saved",
            f"{week.label}: picked {team} to WIN",
            pool="dropdead",
        )
        flash("Pick saved and locked in for the week.", "success")
        return redirect(url_for("dropdead.pick"))

    teams = Team.query.order_by(Team.name).all()
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
                team_game = team_game_this_week(p.team_id, week.id, pool="dropdead")
                removable_picks[e.id] = game_pickable(team_game)
        for g in Game.query.filter_by(week_id=week.id, pool="dropdead").all():
            if not game_pickable(g):
                unpickable_team_ids.update(t for t in (g.home_team_id, g.away_team_id) if t is not None)
        team_matchups = team_matchups_for_week(week.id, "dropdead")
        # One row per matchup on the pick page, mirroring the Loser Pool.
        games = (
            Game.query.filter_by(week_id=week.id, pool="dropdead")
            .order_by(Game.kickoff.asc().nullslast())
            .all()
        )
        for g in games:
            teams_with_games.update(t for t in (g.home_team_id, g.away_team_id) if t is not None)

    return render_template(
        "dropdead/pick.html",
        entries=entries,
        week=week,
        locked=locked,
        teams=teams,
        picks_this_week=picks_this_week,
        removable_picks=removable_picks,
        unpickable_team_ids=unpickable_team_ids,
        team_matchups=team_matchups,
        games=games,
        teams_with_games=teams_with_games,
        # Entries whose elimination was a no-show: no buy-back offered, per
        # the printed rules.
        no_pick_eliminations={e.id for e in entries if dropdead_eliminated_for_no_pick(e)},
        # Which entries may buy back right now. Worked out here rather than in
        # the template so the page and the route can't drift apart on the rule.
        buyback_available={e.id for e in entries if dropdead_buyback_available(e, week)},
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
        return redirect(url_for("dropdead.pick"))
    week = pick.week
    if deadline_passed(week):
        flash("The pick deadline for this week has passed.", "error")
        return redirect(url_for("dropdead.pick"))
    team_game = team_game_this_week(pick.team_id, week.id, pool="dropdead")
    if not game_pickable(team_game):
        flash("Too late to remove that pick — it locks 1 hour before kickoff.", "error")
        return redirect(url_for("dropdead.pick"))
    removed = str(pick.team) if pick.team else "pick"
    removed_week = week.label if week else ""
    db.session.delete(pick)
    db.session.commit()
    log_activity("pick_removed", f"{removed_week}: removed pick {removed}", pool="dropdead")
    flash("Pick removed — you can make a new selection.", "success")
    return redirect(url_for("dropdead.pick"))


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
    if not entry.eliminated_week:
        flash("That entry has no recorded elimination to buy back from.", "error")
        return redirect(url_for("dropdead.pick"))
    # You never buy back into the week that knocked you out -- that week is
    # already played. The buy-back puts you into the *following* week, so it
    # has to happen while that week is current and still open for picks.
    week = get_current_week(entry.season_year, "dropdead")
    if not week or week.number != entry.eliminated_week + 1:
        flash(
            "Buy-backs are only open during the week after the one you were "
            "eliminated in.",
            "error",
        )
        return redirect(url_for("dropdead.pick"))
    if deadline_passed(week):
        flash("The pick deadline for this week has passed, so it's too late to buy back.", "error")
        return redirect(url_for("dropdead.pick"))
    # Whether a week's eliminations may be bought back from is the admin's
    # call per week (Pool Manager), not a week-number rule -- preseason and
    # test weeks don't number the way the printed weeks 1-4 rule assumes.
    # The flag lives on the week the entry died in, not the week they return for.
    elim_week = Week.query.filter_by(
        season_year=entry.season_year, pool="dropdead", number=entry.eliminated_week
    ).first()
    if not elim_week or not elim_week.buyback_open:
        flash("Buy-backs aren't open for that week.", "error")
        return redirect(url_for("dropdead.pick"))
    # Printed rules: no buy-back for an entrant who failed to turn in a pick.
    if dropdead_eliminated_for_no_pick(entry):
        flash(
            "Buy-backs aren't available when the entry was eliminated for not "
            "turning in a pick.",
            "error",
        )
        return redirect(url_for("dropdead.pick"))
    entry.is_active = True
    entry.buy_backs_used += 1
    entry.buyback_week = entry.eliminated_week
    db.session.commit()
    log_activity(
        "buyback",
        f"Bought back in ($30) after elimination in week {entry.eliminated_week}",
        pool="dropdead",
    )
    flash("Entry revived. ($30 buy-back fee due to the commissioners.)", "success")
    return redirect(url_for("dropdead.pick"))


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = standings_dropdead(season_year)
    return render_template("dropdead/standings.html", entries=entries)
