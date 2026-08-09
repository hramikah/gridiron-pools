"""Scoring logic for the three pools. Kept separate from routes so admin
actions and any future batch/cron processing can share the same code."""

from helpers import deadline_passed, week_is_complete
from models import Entry, Game, GridironMiss, LoserPoolPoints, Pick, User, Week, db

GRIDIRON_MISS_PENALTY_LOSSES = 5
GRIDIRON_MAKEUP_PICKS = 8
GRIDIRON_NORMAL_PICKS = 5
GRIDIRON_BENCH_AFTER_MISSES = 5


def score_dropdead_pick(pick, game):
    if game.winner is None:
        return
    team_won = (game.winner == "home" and pick.team_id == game.home_team_id) or (
        game.winner == "away" and pick.team_id == game.away_team_id
    )
    pick.result = "win" if team_won else "loss"
    pick.points = 1 if team_won else 0
    if not team_won:
        entry = pick.entry
        if entry.is_active:
            entry.is_active = False
            entry.eliminated_week = pick.week.number


def score_loser_pick(pick, game, points_for_team):
    if game.winner is None:
        return
    team_lost = (game.winner == "home" and pick.team_id == game.away_team_id) or (
        game.winner == "away" and pick.team_id == game.home_team_id
    )
    if team_lost:
        pick.result = "win"
        pick.points = points_for_team
    else:
        pick.result = "loss"
        pick.points = -points_for_team


def score_gridiron_pick(pick, game):
    if not game.is_final or game.home_score is None or game.away_score is None:
        return
    margin = game.home_score - game.away_score
    if pick.market == "spread":
        spread = game.spread or 0
        if game.favorite == "home":
            adj = margin - spread
        elif game.favorite == "away":
            adj = margin + spread
        else:
            adj = margin
        if adj == 0:
            pick.result, pick.points = "push", 0
        elif (adj > 0 and pick.side == "home") or (adj < 0 and pick.side == "away"):
            pick.result, pick.points = "win", 1
        else:
            pick.result, pick.points = "loss", 0
    elif pick.market == "total":
        if game.over_under is None:
            return
        total = game.home_score + game.away_score
        if total == game.over_under:
            pick.result, pick.points = "push", 0
        elif (total > game.over_under and pick.side == "over") or (
            total < game.over_under and pick.side == "under"
        ):
            pick.result, pick.points = "win", 1
        else:
            pick.result, pick.points = "loss", 0


def score_game(game):
    """Score the picks tied to this (now-final) game. Each pool owns its own
    lines, so a game scores only the picks belonging to its own pool."""
    if game.pool == "gridiron":
        for pick in Pick.query.filter_by(pool="gridiron", game_id=game.id).all():
            score_gridiron_pick(pick, game)

    elif game.pool in ("dropdead", "loser") and game.home_team_id and game.away_team_id:
        team_picks = Pick.query.filter(
            Pick.pool == game.pool,
            Pick.week_id == game.week_id,
            Pick.team_id.in_([game.home_team_id, game.away_team_id]),
        ).all()
        for pick in team_picks:
            if pick.pool == "dropdead":
                score_dropdead_pick(pick, game)
            else:
                lp = LoserPoolPoints.query.filter_by(
                    season_year=game.week.season_year, team_id=pick.team_id
                ).first()
                score_loser_pick(pick, game, lp.points if lp else 0)

    db.session.commit()


def enforce_dropdead_no_tie(week):
    """Drop Dead can never end in a tie for first place. If a week's results
    (or no-show eliminations) wipe out every remaining active entry at once,
    revive whichever entries share that week's elimination -- the tied
    leaders -- so they keep playing until a solo survivor emerges. Only acts
    once the week's games are all final, so it never fires on a partial
    slate, and never touches a genuine solo winner (a single entry left
    active, or a single entry eliminated last)."""
    if week is None or week.pool != "dropdead":
        return
    games = Game.query.filter_by(week_id=week.id, pool="dropdead").all()
    if not games or not all(g.is_final for g in games):
        return
    if Entry.query.filter_by(pool="dropdead", season_year=week.season_year, is_active=True).count() > 0:
        return
    tied = Entry.query.filter_by(
        pool="dropdead", season_year=week.season_year, eliminated_week=week.number
    ).all()
    if len(tied) <= 1:
        return
    for e in tied:
        e.is_active = True
        e.eliminated_week = None
    db.session.commit()


def process_missed_picks(week):
    """Handle entries that never submitted a pick for this pool's week.
    Weeks are per-pool, so this applies only the logic for ``week.pool``:
    - Drop Dead: active entries with no pick are eliminated.
    - Loser Pool: entries with no pick are auto-assigned the visiting team
      of the week's Monday Night Football game (per the printed rules).
    - Gridiron: entries with no pick are scored 0-5 for the week (via a
      GridironMiss record) and get an 8-pick makeup allowance the following
      week; an entry with more than 5 missed weeks is benched (is_active
      set to False).
    """
    if week.pool == "dropdead":
        picked_dropdead_entry_ids = {
            p.entry_id
            for p in Pick.query.filter_by(pool="dropdead", week_id=week.id).all()
        }
        for entry in Entry.query.filter_by(pool="dropdead", season_year=week.season_year, is_active=True).all():
            if entry.id not in picked_dropdead_entry_ids:
                entry.is_active = False
                entry.eliminated_week = week.number

    elif week.pool == "loser":
        mnf_game = Game.query.filter_by(week_id=week.id, is_mnf=True).first()
        if mnf_game and mnf_game.away_team_id:
            picked_loser_entry_ids = {
                p.entry_id for p in Pick.query.filter_by(pool="loser", week_id=week.id).all()
            }
            for entry in Entry.query.filter_by(pool="loser", season_year=week.season_year).all():
                if entry.id not in picked_loser_entry_ids:
                    db.session.add(
                        Pick(
                            entry_id=entry.id,
                            week_id=week.id,
                            pool="loser",
                            team_id=mnf_game.away_team_id,
                        )
                    )

    elif week.pool == "gridiron":
        picked_gridiron_entry_ids = {
            p.entry_id for p in Pick.query.filter_by(pool="gridiron", week_id=week.id).all()
        }
        for entry in Entry.query.filter_by(pool="gridiron", season_year=week.season_year, is_active=True).all():
            if entry.id in picked_gridiron_entry_ids:
                continue
            if GridironMiss.query.filter_by(entry_id=entry.id, week_id=week.id).first():
                continue
            db.session.add(GridironMiss(entry_id=entry.id, week_id=week.id))
            db.session.flush()
            total_misses = GridironMiss.query.filter_by(entry_id=entry.id).count()
            if total_misses > GRIDIRON_BENCH_AFTER_MISSES:
                entry.is_active = False
                entry.eliminated_week = week.number

    db.session.commit()


def ensure_missed_processed(week):
    """Lazily apply this week's missed-pick penalties once its deadline has
    passed. Runs at most once per week (the ``missed_processed`` flag), so it
    can be called freely from any page load. The Loser Pool waits until its
    Monday Night game is entered so the no-show auto-pick can be assigned."""
    if week is None or week.missed_processed or not deadline_passed(week):
        return
    if week.pool == "loser":
        mnf = Game.query.filter_by(week_id=week.id, is_mnf=True).first()
        if not (mnf and mnf.away_team_id):
            return  # retry on a later load, once the MNF game exists
    process_missed_picks(week)
    enforce_dropdead_no_tie(week)
    week.missed_processed = True
    db.session.commit()


def gridiron_pick_limit(entry, week):
    """5 picks normally; 8 the week immediately after a missed week."""
    prior_miss = (
        GridironMiss.query.join(Week, GridironMiss.week_id == Week.id)
        .filter(
            GridironMiss.entry_id == entry.id,
            Week.season_year == week.season_year,
            Week.pool == "gridiron",
            Week.number == week.number - 1,
        )
        .first()
    )
    return GRIDIRON_MAKEUP_PICKS if prior_miss else GRIDIRON_NORMAL_PICKS


def gridiron_picks_grid(week):
    """Per-player picks grid for a Gridiron week: username + up to N pick
    slots. Shared between the admin editor and the read-only player report."""
    entries = (
        Entry.query.filter_by(pool="gridiron", season_year=week.season_year)
        .join(User)
        .order_by(User.username, Entry.id)
        .all()
    )
    picks_grid = []
    max_slots = 0
    for e in entries:
        eps = sorted(
            (p for p in e.picks if p.week_id == week.id and p.pool == "gridiron"),
            key=lambda p: p.id,
        )
        limit = gridiron_pick_limit(e, week)
        max_slots = max(max_slots, limit, len(eps))
        picks_grid.append({"entry": e, "picks": eps, "limit": limit})
    max_slots = max_slots or 5
    return picks_grid, max_slots


def _gridiron_missed_week_numbers(entry, through_week=None):
    q = GridironMiss.query.join(Week, GridironMiss.week_id == Week.id).filter(GridironMiss.entry_id == entry.id)
    if through_week is not None:
        q = q.filter(Week.number <= through_week)
    return {m.week.number for m in q.all()}


def _gridiron_week_empty_losses(entry, week):
    """After a week's deadline, each pick slot a Gridiron entry left empty
    counts as a loss. Capped at what was actually pickable that week (number
    of games x available markets), so an entry is never penalized for slots it
    couldn't fill. Zero while the deadline hasn't passed (picks still open)."""
    if week is None or not deadline_passed(week):
        return 0
    games = Game.query.filter_by(week_id=week.id, pool="gridiron").all()
    available = sum(1 + (1 if g.over_under is not None else 0) for g in games)
    if available == 0:
        return 0
    made = sum(1 for p in entry.picks if p.week_id == week.id and p.pool == "gridiron")
    expected = min(gridiron_pick_limit(entry, week), available)
    return max(0, expected - made)


def _gridiron_empty_losses(entry, through_week=None):
    """Total empty-slot losses across the entry's locked Gridiron weeks."""
    q = Week.query.filter_by(season_year=entry.season_year, pool="gridiron")
    if through_week is not None:
        q = q.filter(Week.number <= through_week)
    return sum(_gridiron_week_empty_losses(entry, w) for w in q.all())


def _assign_ranks(items, key_func):
    """Standard competition ranking (1224): entries with an equal key share
    the same place, and the next distinct key's place is its 1-based
    position in the list (so 1, 1, 1, 4 -- not 1, 1, 1, 2)."""
    ranked = []
    prev_key = object()
    prev_rank = 0
    for i, item in enumerate(items, start=1):
        key = key_func(item)
        if key != prev_key:
            prev_rank = i
            prev_key = key
        ranked.append((prev_rank,) + (item if isinstance(item, tuple) else (item,)))
    return ranked


def standings_dropdead(season_year):
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    entries.sort(key=lambda e: (not e.is_active, -(e.eliminated_week or 999)))
    return _assign_ranks(entries, key_func=lambda e: (not e.is_active, e.eliminated_week))


def standings_loser(season_year):
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).all()
    totals = []
    for e in entries:
        total = sum(p.points or 0 for p in e.picks)
        totals.append((e, total))
    totals.sort(key=lambda t: -t[1])
    return _assign_ranks(totals, key_func=lambda t: t[1])


def standings_gridiron(season_year):
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    rows = []
    for e in entries:
        wins = sum(1 for p in e.picks if p.result == "win")
        losses = sum(1 for p in e.picks if p.result == "loss")
        losses += _gridiron_empty_losses(e)  # empty slots after deadline = losses
        pushes = sum(1 for p in e.picks if p.result == "push")
        rows.append((e, wins, losses, pushes))
    rows.sort(key=lambda r: (-r[1], r[2]))
    return _assign_ranks(rows, key_func=lambda r: (r[1], r[2]))


def dropdead_status_through_week(season_year, week_number):
    """Drop Dead status/pick as it stood after a given past week."""
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    rows = []
    for e in entries:
        # A buy-back always lands on the same week number as the elimination
        # it reversed (the route only allows same-week buy-backs), so if
        # eliminated_week still equals buyback_week, nothing has eliminated
        # this entry since -- it's been active from that week forward. If
        # eliminated_week has since moved past buyback_week, that's a later,
        # separate elimination and should show as "out" from that week on.
        bought_back_by_then = e.buyback_week is not None and e.buyback_week <= week_number
        revived = e.buyback_week is not None and e.eliminated_week == e.buyback_week
        eliminated_by_then = (
            not revived and e.eliminated_week is not None and e.eliminated_week <= week_number
        )
        week_pick = next((p for p in e.picks if p.week.number == week_number), None)
        rows.append(
            {
                "entry": e,
                "is_active": not eliminated_by_then,
                "eliminated_week": e.eliminated_week if eliminated_by_then else None,
                "week_pick": week_pick,
                "buyback_week": e.buyback_week if bought_back_by_then else None,
            }
        )
    rows.sort(key=lambda r: (not r["is_active"], -(r["eliminated_week"] or 999)))
    return rows


def loser_totals_through_week(season_year, week_number):
    """Loser Pool point totals accumulated through a given past week."""
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).all()
    rows = []
    for e in entries:
        picks_through = [p for p in e.picks if p.week.number <= week_number]
        total = sum(p.points or 0 for p in picks_through)
        week_pick = next((p for p in e.picks if p.week.number == week_number), None)
        rows.append((e, total, week_pick))
    rows.sort(key=lambda r: -r[1])
    return rows


def gridiron_record_through_week(season_year, week_number):
    """Gridiron win/loss/tie record accumulated through a given past week,
    plus the entry's actual picks (team/side + result) for that specific
    week, so a per-week view can show what was picked, not just the tally."""
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    rows = []
    for e in entries:
        picks_through = [p for p in e.picks if p.week.number <= week_number]
        wins = sum(1 for p in picks_through if p.result == "win")
        losses = sum(1 for p in picks_through if p.result == "loss")
        losses += _gridiron_empty_losses(e, week_number)  # empty slots after deadline = losses
        ties = sum(1 for p in picks_through if p.result == "push")

        week_picks = []
        for p in e.picks:
            if p.week.number != week_number:
                continue
            if p.market == "spread":
                label = p.game.home_team if p.side == "home" else p.game.away_team
            else:
                label = f"{p.side.capitalize()} {p.game.over_under} ({p.game.home_team})"
            week_picks.append({"label": label, "result": p.result})

        rows.append((e, wins, losses, ties, week_picks))
    rows.sort(key=lambda r: (-r[1], r[2]))
    return rows


def player_pick_history(season_year, user_id):
    """Every team/selection a given user has picked this season, across all
    three pools, with each pick's win/loss result (once its week is fully
    complete -- otherwise shown as pending)."""
    rows = []
    for pool_name in ("dropdead", "loser", "gridiron"):
        entries = Entry.query.filter_by(pool=pool_name, season_year=season_year, user_id=user_id).all()
        for e in entries:
            for p in sorted(e.picks, key=lambda p: p.week.number):
                if pool_name in ("dropdead", "loser"):
                    team_label = f"{p.team.city} {p.team.name}" if p.team else "—"
                elif p.market == "spread":
                    team_label = p.game.home_team if p.side == "home" else p.game.away_team
                else:
                    team_label = f"{p.side.capitalize()} {p.game.over_under} ({p.game.home_team})"

                result = p.result if week_is_complete(p.week) else "pending"
                rows.append(
                    {
                        "pool": pool_name,
                        "entry_label": e.label,
                        "week": p.week.number,
                        "team": team_label,
                        "result": result,
                        "points": p.points,
                    }
                )
            if pool_name == "gridiron":
                for w in Week.query.filter_by(season_year=season_year, pool="gridiron").order_by(Week.number).all():
                    empty = _gridiron_week_empty_losses(e, w)
                    if empty > 0:
                        rows.append(
                            {
                                "pool": pool_name,
                                "entry_label": e.label,
                                "week": w.number,
                                "team": ("NO PICK ×%d" % empty) if empty > 1 else "NO PICK",
                                "result": "loss",
                                "points": None,
                            }
                        )
            elif pool_name == "dropdead" and e.eliminated_week is not None:
                # Elimination with no Pick row for that week means the entry
                # never submitted a pick that week (auto-eliminated for a
                # no-show, same as picking a loser) -- show it explicitly
                # rather than silently having no Drop Dead row at all.
                picked_weeks = {p.week.number for p in e.picks}
                if e.eliminated_week not in picked_weeks:
                    rows.append(
                        {
                            "pool": pool_name,
                            "entry_label": e.label,
                            "week": e.eliminated_week,
                            "team": "NO PICK SUBMITTED",
                            "result": "loss",
                            "points": None,
                        }
                    )
    rows.sort(key=lambda r: (r["pool"], r["week"]))
    return rows


def dropdead_matrix(season_year, week_numbers):
    """Every entry's pick for every unlocked week, side by side."""
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    rows = []
    for e in entries:
        cells = {wn: next((p for p in e.picks if p.week.number == wn), None) for wn in week_numbers}
        rows.append({"entry": e, "cells": cells})
    rows.sort(key=lambda r: (not r["entry"].is_active, -(r["entry"].eliminated_week or 999)))
    return rows


def loser_matrix(season_year, week_numbers):
    """Every entry's pick + running point total for every unlocked week."""
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).all()
    rows = []
    for e in entries:
        cells = {wn: next((p for p in e.picks if p.week.number == wn), None) for wn in week_numbers}
        total = sum(p.points or 0 for p in e.picks)
        rows.append({"entry": e, "cells": cells, "total": total})
    rows.sort(key=lambda r: -r["total"])
    return rows


def gridiron_matrix(season_year, week_numbers):
    """Every entry's per-week W-L-T record for every unlocked week, plus a
    season total. A missed week shows as a 0-5 penalty cell."""
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    weeks_by_num = {
        w.number: w
        for w in Week.query.filter_by(season_year=season_year, pool="gridiron").all()
    }
    rows = []
    for e in entries:
        cells = {}
        for wn in week_numbers:
            week = weeks_by_num.get(wn)
            week_picks = [p for p in e.picks if p.week.number == wn]
            empty = _gridiron_week_empty_losses(e, week)
            if not week_picks and empty == 0:
                cells[wn] = None
                continue
            cells[wn] = {
                "wins": sum(1 for p in week_picks if p.result == "win"),
                "losses": sum(1 for p in week_picks if p.result == "loss") + empty,
                "ties": sum(1 for p in week_picks if p.result == "push"),
                "missed": not week_picks and empty > 0,
            }
        wins = sum(1 for p in e.picks if p.result == "win")
        losses = sum(1 for p in e.picks if p.result == "loss") + _gridiron_empty_losses(e)
        ties = sum(1 for p in e.picks if p.result == "push")
        rows.append({"entry": e, "cells": cells, "wins": wins, "losses": losses, "ties": ties})
    rows.sort(key=lambda r: (-r["wins"], r["losses"]))
    return rows
