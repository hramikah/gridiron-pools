from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from helpers import (
    deadline_passed,
    log_activity,
    game_pickable,
    get_current_week,
    gridiron_signup_deadline,
    gridiron_signups_open,
)
from team_colors import styles_for
from models import Entry, Game, Pick, db
from scoring import (
    GRIDIRON_MAKEUP_PENALTY_LOSSES,
    GRIDIRON_MAKEUP_PICKS,
    GRIDIRON_NORMAL_PICKS,
    process_due_weeks,
    gridiron_makeup_week,
    gridiron_penalty_slots,
    gridiron_pick_limit,
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
    return redirect(url_for("main.index") if request.form.get("from") == "home"
                    else url_for("gridiron.pick"))


@bp.route("/pick", methods=["GET", "POST"])
@login_required
def pick():
    season_year = current_app.config["CURRENT_SEASON"]
    entries = Entry.query.filter_by(user_id=current_user.id, pool="gridiron", season_year=season_year).all()
    week = get_current_week(season_year, "gridiron")
    # Catch up every past-deadline week, not just this one -- see
    # scoring.due_weeks for why the current-week-only call missed some.
    process_due_weeks(season_year, "gridiron")
    locked = deadline_passed(week)
    all_games = (
        Game.query.filter_by(week_id=week.id, pool="gridiron")
        .order_by(Game.kickoff.asc().nullslast())
        .all()
        if week
        else []
    )
    # `games` is the still-pickable subset, used for the pick limit and for
    # validating a submission. The page itself renders every game -- a game
    # past its 1-hour cutoff is shown disabled rather than removed, matching
    # Loser and Drop Dead. Silently dropping it left a player unable to tell
    # a missed game from one that was never offered.
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
        saved = []
        for game_id, market, side in selections:
            g = game_by_id.get(game_id)
            if not g:
                continue
            if market == "spread":
                saved.append(f"{g.away_team if side == 'away' else g.home_team} (spread)")
            else:
                saved.append(f"{side.capitalize()} {g.over_under} in {g.label}")
        log_activity(
            "pick_saved",
            f"{week.label}: saved {len(selections)} pick(s) -- " + "; ".join(saved),
            pool="gridiron",
        )
        flash("Picks saved and locked in.", "success")
        return redirect(url_for("gridiron.pick"))

    picks_this_week = {}
    picks_results_this_week = {}
    picks_ids_this_week = {}
    pick_limits = {}
    remaining_by_entry = {}
    makeup_by_entry = {}
    penalty_slots_by_entry = {}
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
            # ...and show those 2 losses as slots in the pick list, so the
            # week reads as 10 games from the start.
            penalty_slots_by_entry[e.id] = gridiron_penalty_slots(e, week)

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
        penalty_slots_by_entry=penalty_slots_by_entry,
        normal_picks=GRIDIRON_NORMAL_PICKS,
        makeup_picks=GRIDIRON_MAKEUP_PICKS,
        makeup_penalty=GRIDIRON_MAKEUP_PENALTY_LOSSES,
        team_styles=styles_for(
            [g.away_team for g in all_games] + [g.home_team for g in all_games]
        ),
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
    removed_game = pick.game.label if pick.game else "pick"
    removed_market = pick.market or "pick"
    removed_week = pick.week.label if pick.week else ""
    db.session.delete(pick)
    db.session.commit()
    log_activity(
        "pick_removed",
        f"{removed_week}: removed {removed_market} pick on {removed_game}",
        pool="gridiron",
    )
    flash("Pick removed — you can make a new selection.", "success")
    return redirect(url_for("gridiron.pick"))


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    rows = standings_gridiron(season_year)
    return render_template("gridiron/standings.html", rows=rows)
