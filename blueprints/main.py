from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from helpers import week_unlocked
from models import POOL_LABELS, POOLS, Entry, Game, User, Week
from pdf_report import build_week_pdf
from scoring import (
    GRIDIRON_GRID_COLUMNS,
    dropdead_matrix,
    dropdead_status_through_week,
    gridiron_awards,
    gridiron_first_miss_week,
    gridiron_matrix,
    gridiron_picks_grid,
    gridiron_record_through_week,
    gridiron_week_records,
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


@bp.route("/help/<pool>")
def help_page(pool):
    if pool not in POOLS:
        flash("Unknown pool.", "error")
        return redirect(url_for("main.index"))
    return render_template("help.html", pool=pool, pool_label=POOL_LABELS[pool])


@bp.route("/standings")
def standings():
    season_year = current_app.config["CURRENT_SEASON"]
    weeks = Week.query.filter_by(season_year=season_year).order_by(Week.number).all()
    # Weeks are per-pool now; a week number is "unlocked" for a pool once that
    # pool's deadline for it has passed. Column headers show the union, but
    # each pool's matrix only fills columns unlocked in that pool (no leak).
    unlocked_by_pool = {
        pool: sorted({w.number for w in weeks if w.pool == pool and week_unlocked(w)})
        for pool in POOLS
    }
    union_numbers = sorted(set().union(*unlocked_by_pool.values())) if weeks else []
    rep_week = {}
    for w in weeks:
        if week_unlocked(w) and w.number not in rep_week:
            rep_week[w.number] = w
    unlocked_weeks = [rep_week[n] for n in union_numbers]

    history_week = request.args.get("week", type=int)
    history_data = None
    if history_week is not None and history_week in union_numbers:
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

    unlocked_week_numbers = union_numbers
    all_weeks_data = {
        "dropdead": dropdead_matrix(season_year, unlocked_by_pool["dropdead"]),
        "loser": loser_matrix(season_year, unlocked_by_pool["loser"]),
        "gridiron": gridiron_matrix(season_year, unlocked_by_pool["gridiron"]),
    }

    # Last completed Gridiron week, for the "last week" column on the standings.
    gridiron_last_week = max(unlocked_by_pool["gridiron"]) if unlocked_by_pool["gridiron"] else None
    gridiron_last_week_records = (
        gridiron_week_records(season_year, gridiron_last_week)
        if gridiron_last_week is not None else {}
    )

    # The week each entry first missed its picks, for the Penalties column.
    # One entry per season: a second miss adds nothing to read here.
    gridiron_first_miss = {
        e.id: gridiron_first_miss_week(e)
        for e in Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    }

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
        gridiron_last_week=gridiron_last_week,
        gridiron_last_week_records=gridiron_last_week_records,
        gridiron_first_miss=gridiron_first_miss,
        awards=gridiron_awards(season_year),
    )


@bp.route("/scores")
def scores():
    season_year = current_app.config["CURRENT_SEASON"]
    weeks = Week.query.filter_by(season_year=season_year).all()

    # Weeks are per-pool, so a given week number spans up to three Week rows.
    # Group final games by week number and dedupe by matchup (same real game
    # across pools has one real score).
    weeks_by_number = {}
    for w in weeks:
        weeks_by_number.setdefault(w.number, []).append(w)

    by_week = []
    for number in sorted(weeks_by_number, reverse=True):
        seen = set()
        deduped = []
        for w in weeks_by_number[number]:
            for g in (
                Game.query.filter_by(week_id=w.id, is_final=True)
                .order_by(Game.sport, Game.away_team)
                .all()
            ):
                key = (g.sport, g.away_team, g.home_team)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(g)
        if deduped:
            by_week.append((weeks_by_number[number][0], deduped))

    return render_template("scores.html", by_week=by_week)


@bp.route("/reports")
@login_required
def reports():
    season_year = current_app.config["CURRENT_SEASON"]
    weeks_by_pool = {}
    for pool in POOLS:
        weeks = (
            Week.query.filter_by(season_year=season_year, pool=pool)
            .order_by(Week.number)
            .all()
        )
        weeks_by_pool[pool] = [(w, week_unlocked(w)) for w in weeks]
    return render_template(
        "reports.html",
        weeks_by_pool=weeks_by_pool,
        pool_labels=POOL_LABELS,
    )


@bp.route("/weeks/<int:week_id>/report")
@login_required
def week_report(week_id):
    week = Week.query.get_or_404(week_id)
    if not week_unlocked(week):
        flash("This week's picks report unlocks once the pick deadline has passed.", "error")
        return redirect(url_for("main.reports"))

    if week.pool == "gridiron":
        picks_grid, max_slots = gridiron_picks_grid(week)
        return render_template(
            "week_report.html",
            week=week,
            pool_label=POOL_LABELS[week.pool],
            picks_grid=picks_grid,
            max_slots=max_slots,
            # The report is a fixed 5 columns wide -- entries with a bigger
            # allowance wrap onto a second line rather than stretching it.
            columns=min(GRIDIRON_GRID_COLUMNS, max_slots) or GRIDIRON_GRID_COLUMNS,
        )

    entries = Entry.query.filter_by(pool=week.pool, season_year=week.season_year).join(User).order_by(User.username).all()
    rows = []
    for e in entries:
        pick = next((p for p in e.picks if p.week_id == week.id), None)
        rows.append({"entry": e, "pick": pick})

    return render_template(
        "week_report.html",
        week=week,
        pool_label=POOL_LABELS[week.pool],
        rows=rows,
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
