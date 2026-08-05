from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import deadline_passed, game_pickable, get_current_week, send_async
from models import Entry, Game, Pick, db
from notifications import email_entry_pick_confirmation
from scoring import gridiron_pick_limit, standings_gridiron

bp = Blueprint("gridiron", __name__)


@bp.route("/rules")
def rules():
    return render_template("gridiron/rules.html")


@bp.route("/join", methods=["POST"])
@login_required
def join():
    season_year = current_app.config["CURRENT_SEASON"]
    existing = Entry.query.filter_by(user_id=current_user.id, pool="gridiron", season_year=season_year).first()
    if existing:
        flash("You already have an entry in Gridiron Investments (one per account). For another entry, register a separate account.", "error")
        return redirect(url_for("gridiron.pick"))
    entry = Entry(user_id=current_user.id, pool="gridiron", season_year=season_year, label="Entry 1")
    db.session.add(entry)
    db.session.commit()
    flash("You're in Gridiron Investments.", "success")
    return redirect(url_for("gridiron.pick"))


@bp.route("/pick", methods=["GET", "POST"])
@login_required
def pick():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = Entry.query.filter_by(user_id=current_user.id, pool="gridiron", season_year=season_year).all()
    week = get_current_week(season_year)
    locked = deadline_passed(week)
    all_games = Game.query.filter_by(week_id=week.id).all() if week else []
    games = [g for g in all_games if game_pickable(g)]

    if request.method == "POST":
        entry_id = int(request.form["entry_id"])
        entry = Entry.query.get_or_404(entry_id)
        if entry.user_id != current_user.id:
            flash("Not your entry.", "error")
            return redirect(url_for("gridiron.pick"))
        if not entry.is_active:
            flash("This entry has been benched for missing too many weeks.", "error")
            return redirect(url_for("gridiron.pick"))
        if locked:
            flash("The pick deadline for this week has passed.", "error")
            return redirect(url_for("gridiron.pick"))

        existing_picks = Pick.query.filter_by(entry_id=entry.id, week_id=week.id, pool="gridiron").all()
        already_keys = {(p.game_id, p.market) for p in existing_picks}
        pick_limit = gridiron_pick_limit(entry, week)
        remaining = pick_limit - len(existing_picks)
        if remaining <= 0:
            flash("You've already used all your picks for this week.", "error")
            return redirect(url_for("gridiron.pick"))

        # Parse submitted selections: form fields named "sel_<game_id>_spread" / "sel_<game_id>_total"
        game_by_id = {g.id: g for g in all_games}
        selections = []  # (game_id, market, side)
        for game in games:
            spread_side = request.form.get(f"sel_{game.id}_spread")
            if spread_side in ("home", "away") and (game.id, "spread") not in already_keys:
                selections.append((game.id, "spread", spread_side))
            total_side = request.form.get(f"sel_{game.id}_total")
            if total_side in ("over", "under") and (game.id, "total") not in already_keys:
                selections.append((game.id, "total", total_side))

        if len(selections) == 0:
            flash("Pick at least one game.", "error")
            return redirect(url_for("gridiron.pick"))
        if len(selections) > remaining:
            flash(f"You only have {remaining} pick(s) left for this week.", "error")
            return redirect(url_for("gridiron.pick"))

        # Re-check each game's own kickoff cutoff server-side, in case time
        # passed between page load and submit.
        for game_id, market, side in selections:
            g = game_by_id.get(game_id)
            if g and not game_pickable(g):
                flash(f"Too late to pick {g.away_team} @ {g.home_team} — picks lock 1 hour before kickoff.", "error")
                return redirect(url_for("gridiron.pick"))

        for game_id, market, side in selections:
            db.session.add(
                Pick(entry_id=entry.id, week_id=week.id, pool="gridiron", game_id=game_id, market=market, side=side)
            )
        db.session.commit()
        send_async(email_entry_pick_confirmation, current_user.id, week.id, "gridiron", entry.id)
        flash("Picks saved and locked in.", "success")
        return redirect(url_for("gridiron.pick"))

    picks_this_week = {}
    picks_results_this_week = {}
    pick_limits = {}
    remaining_by_entry = {}
    if week:
        for e in entries:
            existing_picks = Pick.query.filter_by(entry_id=e.id, week_id=week.id, pool="gridiron").all()
            picks_this_week[e.id] = {(p.game_id, p.market): p.side for p in existing_picks}
            picks_results_this_week[e.id] = {(p.game_id, p.market): p.result for p in existing_picks}
            limit = gridiron_pick_limit(e, week)
            pick_limits[e.id] = limit
            remaining_by_entry[e.id] = max(0, limit - len(existing_picks))

    return render_template(
        "gridiron/pick.html",
        entries=entries,
        week=week,
        locked=locked,
        games=games,
        all_games=all_games,
        picks_this_week=picks_this_week,
        picks_results_this_week=picks_results_this_week,
        pick_limits=pick_limits,
        remaining_by_entry=remaining_by_entry,
    )


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    rows = standings_gridiron(season_year)
    return render_template("gridiron/standings.html", rows=rows)
