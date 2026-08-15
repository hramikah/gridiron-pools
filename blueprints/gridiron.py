from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import (
    deadline_passed,
    game_pickable,
    get_current_week,
    gridiron_signup_deadline,
    gridiron_signups_open,
    send_async,
)
from models import Entry, Game, Pick, db
from notifications import email_entry_pick_confirmation
from scoring import (
    GRIDIRON_MAKEUP_PENALTY_LOSSES,
    GRIDIRON_MAKEUP_PICKS,
    GRIDIRON_STARTOVER_PICKS,
    ensure_missed_processed,
    gridiron_makeup_week,
    gridiron_pick_limit,
    gridiron_startover_available,
    standings_gridiron,
)

bp = Blueprint("gridiron", __name__)


@bp.route("/rules")
def rules():
    season_year = current_app.config["CURRENT_SEASON"]
    return render_template(
        "gridiron/rules.html",
        signups_open=gridiron_signups_open(season_year),
        signup_deadline=gridiron_signup_deadline(season_year),
    )


@bp.route("/join", methods=["POST"])
@login_required
def join():
    season_year = current_app.config["CURRENT_SEASON"]
    existing = Entry.query.filter_by(user_id=current_user.id, pool="gridiron", season_year=season_year).first()
    if existing:
        flash("You already have an entry in Gridiron Investments (one per account). For another entry, register a separate account.", "error")
        return redirect(url_for("gridiron.pick"))
    # Entries close for good at the Week 2 deadline.
    if not gridiron_signups_open(season_year):
        cutoff = gridiron_signup_deadline(season_year)
        flash(
            "Gridiron Investments is closed to new entries -- signups ended at the Week 2 deadline"
            + (f" ({cutoff.strftime('%a %b %d, %Y at %I:%M %p')} Eastern)." if cutoff else "."),
            "error",
        )
        return redirect(url_for("gridiron.rules"))
    entry = Entry(user_id=current_user.id, pool="gridiron", season_year=season_year, label="Entry 1")
    db.session.add(entry)
    db.session.commit()
    flash("You're in Gridiron Investments.", "success")
    return redirect(url_for("gridiron.pick"))


@bp.route("/makeup-choice/<int:entry_id>", methods=["POST"])
@login_required
def makeup_choice(entry_id):
    """Player elects how to play their one makeup week: the standard 8 picks
    with the 2-game penalty, or -- only if the week they missed was week 1 --
    10 picks with no penalty ("start over"). Locked in once any pick for that
    week has been saved, so the allowance can't shift under existing picks."""
    season_year = current_app.config["CURRENT_SEASON"]
    entry = Entry.query.get_or_404(entry_id)
    if entry.user_id != current_user.id or entry.pool != "gridiron":
        abort(403)

    week = get_current_week(season_year, "gridiron")
    if not gridiron_startover_available(entry, week):
        flash("That option isn't available for this entry.", "error")
        return redirect(url_for("gridiron.pick"))
    if deadline_passed(week):
        flash("The deadline for this week has passed.", "error")
        return redirect(url_for("gridiron.pick"))
    if Pick.query.filter_by(entry_id=entry.id, week_id=week.id, pool="gridiron").first():
        flash("You've already saved picks this week, so this choice is locked in.", "error")
        return redirect(url_for("gridiron.pick"))

    choice = request.form.get("choice")
    if choice not in ("makeup", "startover"):
        flash("Choose one of the two options.", "error")
        return redirect(url_for("gridiron.pick"))

    entry.makeup_choice = choice
    db.session.commit()
    if choice == "startover":
        flash(f"Starting over: {GRIDIRON_STARTOVER_PICKS} picks this week, no penalty.", "success")
    else:
        flash(f"Makeup week: {GRIDIRON_MAKEUP_PICKS} picks with the {GRIDIRON_MAKEUP_PENALTY_LOSSES}-game penalty.", "success")
    return redirect(url_for("gridiron.pick"))


@bp.route("/pick", methods=["GET", "POST"])
@login_required
def pick():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = Entry.query.filter_by(user_id=current_user.id, pool="gridiron", season_year=season_year).all()
    week = get_current_week(season_year, "gridiron")
    ensure_missed_processed(week)
    locked = deadline_passed(week)
    all_games = Game.query.filter_by(week_id=week.id, pool="gridiron").all() if week else []
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
    picks_ids_this_week = {}
    pick_limits = {}
    remaining_by_entry = {}
    makeup_by_entry = {}
    startover_by_entry = {}
    if week:
        for e in entries:
            existing_picks = Pick.query.filter_by(entry_id=e.id, week_id=week.id, pool="gridiron").all()
            picks_this_week[e.id] = {(p.game_id, p.market): p.side for p in existing_picks}
            picks_results_this_week[e.id] = {(p.game_id, p.market): p.result for p in existing_picks}
            picks_ids_this_week[e.id] = {(p.game_id, p.market): p.id for p in existing_picks}
            limit = gridiron_pick_limit(e, week)
            pick_limits[e.id] = limit
            remaining_by_entry[e.id] = max(0, limit - len(existing_picks))
            # Flag the one makeup week so the page can explain the 8-of-10
            # allowance and the 2 losses that come with it.
            makeup_by_entry[e.id] = gridiron_makeup_week(e) == week.number
            startover_by_entry[e.id] = gridiron_startover_available(e, week)

    # A game whose kickoff is more than an hour out is still in `games`
    # (the pickable set); use that same set to decide which locked-in picks
    # are still removable, alongside the week's overall deadline.
    pickable_game_ids = {g.id for g in games}

    return render_template(
        "gridiron/pick.html",
        entries=entries,
        week=week,
        locked=locked,
        games=games,
        all_games=all_games,
        picks_this_week=picks_this_week,
        picks_results_this_week=picks_results_this_week,
        picks_ids_this_week=picks_ids_this_week,
        pick_limits=pick_limits,
        remaining_by_entry=remaining_by_entry,
        pickable_game_ids=pickable_game_ids,
        makeup_by_entry=makeup_by_entry,
        startover_by_entry=startover_by_entry,
        startover_picks=GRIDIRON_STARTOVER_PICKS,
        makeup_picks=GRIDIRON_MAKEUP_PICKS,
        makeup_penalty=GRIDIRON_MAKEUP_PENALTY_LOSSES,
    )


@bp.route("/picks/<int:pick_id>/remove", methods=["POST"])
@login_required
def remove_pick(pick_id):
    pick = Pick.query.get_or_404(pick_id)
    if pick.entry.user_id != current_user.id:
        flash("Not your pick.", "error")
        return redirect(url_for("gridiron.pick"))
    week = pick.week
    if deadline_passed(week):
        flash("The pick deadline for this week has passed.", "error")
        return redirect(url_for("gridiron.pick"))
    if not game_pickable(pick.game):
        flash("Too late to remove that pick — it locks 1 hour before kickoff.", "error")
        return redirect(url_for("gridiron.pick"))
    db.session.delete(pick)
    db.session.commit()
    flash("Pick removed — you can make a new selection.", "success")
    return redirect(url_for("gridiron.pick"))


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    rows = standings_gridiron(season_year)
    return render_template("gridiron/standings.html", rows=rows)
