from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from helpers import get_current_week, pool_signup_deadline, pool_signups_open, week_unlocked
from models import ActivityLog, POOL_ENTRY_FEES, POOL_LABELS, POOLS, Entry, Game, User, Week, db
from pdf_report import build_week_pdf
from scoring import (
    DROPDEAD_BUYBACK_FEE,
    GRIDIRON_GRID_COLUMNS,
    dropdead_buyback_available,
    dropdead_matrix,
    dropdead_status_through_week,
    gridiron_awards,
    gridiron_first_miss_week,
    gridiron_picks_grid,
    gridiron_record_through_week,
    gridiron_week_records,
    loser_totals_through_week,
    player_pick_history,
    standings_dropdead,
    standings_gridiron,
    standings_loser,
)

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    """The home page is also the join page.

    Every pool is shown to every signed-in player: the ones they are in carry
    their links, the ones they are not carry a Join button quoting the price.
    There is no separate tick-box screen any more -- see auth.choose_pools.
    """
    season_year = current_app.config["CURRENT_SEASON"]
    my_entries = {}
    if current_user.is_authenticated:
        for e in Entry.query.filter_by(user_id=current_user.id, season_year=season_year).all():
            my_entries.setdefault(e.pool, []).append(e)

    # Where this player stands, keyed by entry id, for the badge on each card
    # they are in. Built from the same standings functions the standings pages
    # use, so a place here can never disagree with the place there. Only
    # computed for the pools this player has actually joined.
    standing = {}
    if my_entries:
        if "gridiron" in my_entries:
            rows = standings_gridiron(season_year)
            standing.update({
                r[1].id: {"place": r[0], "field": len(rows)} for r in rows
            })
        if "loser" in my_entries:
            rows = standings_loser(season_year)
            standing.update({
                r[1].id: {"place": r[0], "field": len(rows)} for r in rows
            })
        if "dropdead" in my_entries:
            # Drop Dead is survivor, not a table: everyone still alive shares
            # first place, so a place number would read the same for all of
            # them and mean nothing. What matters is alive / out, and whether
            # a buy-back is on the table right now.
            dd_week = get_current_week(season_year, "dropdead")
            alive = 0
            total = 0
            for rank, entry in standings_dropdead(season_year):
                total += 1
                if entry.is_active:
                    alive += 1
            for e in my_entries["dropdead"]:
                if e.is_active:
                    standing[e.id] = {"alive": True, "left": alive, "field": total}
                else:
                    standing[e.id] = {
                        "alive": False,
                        "eliminated_week": e.eliminated_week,
                        "buyback": dropdead_buyback_available(e, dd_week),
                    }

    return render_template(
        "home.html",
        my_entries=my_entries,
        standing=standing,
        season_year=season_year,
        pool_count=len(POOLS),
        fees=POOL_ENTRY_FEES,
        pool_labels=POOL_LABELS,
        # Every pool closes to new entries at its Week 1 deadline; past it the
        # card shows a closed badge instead of a Join button.
        signups_open={p: pool_signups_open(season_year, p) for p in POOLS},
        signup_deadline={p: pool_signup_deadline(season_year, p) for p in POOLS},
    )


@bp.route("/billing")
@login_required
def billing():
    """What this player owes, and when each charge and payment happened.

    Covers every account sharing this email, not just the one signed in: a
    player with extra lines pays for all of them out of one pocket, and one
    check usually covers the lot, so making them switch accounts to add the
    numbers up would be busywork.

    Deliberately built from the same numbers the admin Payments page bills
    from -- models.POOL_ENTRY_FEES and the buy-back fees in scoring.py -- and
    from the same paid flags the admin toggles there, so marking something
    paid on the Payments page shows up here on the next page load with no
    second place to update.
    """
    season_year = current_app.config["CURRENT_SEASON"]

    # Accounts are linked by sharing an email address (see auth.add_account).
    # An account with no address on file has no siblings by definition.
    if current_user.email:
        accounts = (
            User.query.filter(db.func.lower(User.email) == current_user.email.lower())
            .order_by(User.id)
            .all()
        )
    else:
        accounts = [current_user]

    # Drop Dead is the only pool with a buy-back.
    buyback_fees = {"dropdead": DROPDEAD_BUYBACK_FEE, "gridiron": 0, "loser": 0}

    groups = []
    for account in accounts:
        entries = (
            Entry.query.filter_by(user_id=account.id, season_year=season_year)
            .order_by(Entry.pool, Entry.created_at)
            .all()
        )

        # When each buy-back was taken. dropdead.buyback() writes an activity
        # row at the moment the button is pressed, which is the only record of
        # the date -- the entry itself keeps just a count and a week number.
        # Oldest first so they line up with what is billed.
        buyback_dates = {}
        for row in (
            ActivityLog.query.filter_by(user_id=account.id, action="buyback")
            .order_by(ActivityLog.created_at)
            .all()
        ):
            buyback_dates.setdefault(row.pool, []).append(row.created_at)

        charges = []
        for e in entries:
            charges.append({
                "pool": e.pool,
                "label": e.label or "Entry 1",
                "what": f"{POOL_LABELS[e.pool]} entry fee",
                "amount": POOL_ENTRY_FEES.get(e.pool, 0),
                "billed_at": e.created_at,
                "paid": bool(e.paid),
                "paid_at": e.paid_at,
            })

        for pool in ("dropdead", "loser", "gridiron"):
            if not buyback_fees.get(pool, 0):
                continue
            dates = list(buyback_dates.get(pool, []))
            taken = 0
            for e in [x for x in entries if x.pool == pool]:
                # One row per buy-back, each with its own paid flag and its
                # own settled date -- Payments marks them individually.
                for b in sorted(e.buy_backs, key=lambda x: x.id):
                    charges.append({
                        "pool": pool,
                        "label": e.label or "Entry 1",
                        "what": f"{POOL_LABELS[pool]} buy-back",
                        "amount": b.fee,
                        "billed_at": b.created_at or (dates[taken] if taken < len(dates) else None),
                        "paid": bool(b.paid),
                        "paid_at": b.paid_at,
                    })
                    taken += 1

        charges.sort(key=lambda c: (c["billed_at"] is None, c["billed_at"] or 0))
        if not charges:
            continue
        groups.append({
            "account": account,
            "is_current": account.id == current_user.id,
            "charges": charges,
            "owed": sum(c["amount"] for c in charges if not c["paid"]),
            "paid": sum(c["amount"] for c in charges if c["paid"]),
        })

    owed = sum(g["owed"] for g in groups)
    settled_total = sum(g["paid"] for g in groups)
    charge_count = sum(len(g["charges"]) for g in groups)

    return render_template(
        "billing.html",
        groups=groups,
        multi_account=len(accounts) > 1,
        account_count=len(accounts),
        charge_count=charge_count,
        owed=owed,
        settled_total=settled_total,
        total=owed + settled_total,
        pool_labels=POOL_LABELS,
    )


@bp.route("/help")
def help_index():
    """The Help tab: pick a pool's walkthrough, plus how to pay in one place."""
    return render_template("help_index.html", pool_labels=POOL_LABELS)


@bp.route("/help/<pool>")
def help_page(pool):
    if pool not in POOLS:
        flash("Unknown pool.", "error")
        return redirect(url_for("main.index"))
    # A pool with its own written walkthrough gets it; the others fall back to
    # the placeholder until theirs is written.
    template = f"help_{pool}.html"
    if template not in current_app.jinja_env.list_templates():
        template = "help.html"
    return render_template(template, pool=pool, pool_label=POOL_LABELS[pool])


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
    # Only Drop Dead still shows a week-by-week grid on this page. The Gridiron
    # and Loser grids were dropped (the per-pool standings pages and Week
    # History cover the same ground), so their matrices are not built either --
    # each one walked every pick of every entry for every unlocked week.
    all_weeks_data = {
        "dropdead": dropdead_matrix(season_year, unlocked_by_pool["dropdead"]),
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
