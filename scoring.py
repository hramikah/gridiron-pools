"""Scoring logic for the three pools. Kept separate from routes so admin
actions and any future batch/cron processing can share the same code."""

from helpers import week_is_complete
from models import Entry, Game, GridironMiss, LoserPoolPoints, Pick, Week, db

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
    """Score every pick tied to this (now-final) game, across all pools."""
    for pick in Pick.query.filter_by(pool="gridiron", game_id=game.id).all():
        score_gridiron_pick(pick, game)

    if game.home_team_id and game.away_team_id:
        team_picks = Pick.query.filter(
            Pick.pool.in_(["dropdead", "loser"]),
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


def process_missed_picks(week):
    """Handle entries that never submitted a pick for this week:
    - Drop Dead: active entries with no pick are eliminated.
    - Loser Pool: entries with no pick are auto-assigned the visiting team
      of the week's Monday Night Football game (per the printed rules).
    - Gridiron: entries with no pick are scored 0-5 for the week (via a
      GridironMiss record) and get an 8-pick makeup allowance the following
      week; an entry with more than 5 missed weeks is benched (is_active
      set to False).
    """
    picked_dropdead_entry_ids = {
        p.entry_id
        for p in Pick.query.filter_by(pool="dropdead", week_id=week.id).all()
    }
    for entry in Entry.query.filter_by(pool="dropdead", season_year=week.season_year, is_active=True).all():
        if entry.id not in picked_dropdead_entry_ids:
            entry.is_active = False
            entry.eliminated_week = week.number

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


def gridiron_pick_limit(entry, week):
    """5 picks normally; 8 the week immediately after a missed week."""
    prior_miss = (
        GridironMiss.query.join(Week, GridironMiss.week_id == Week.id)
        .filter(
            GridironMiss.entry_id == entry.id,
            Week.season_year == week.season_year,
            Week.number == week.number - 1,
        )
        .first()
    )
    return GRIDIRON_MAKEUP_PICKS if prior_miss else GRIDIRON_NORMAL_PICKS


def _gridiron_missed_week_numbers(entry, through_week=None):
    q = GridironMiss.query.join(Week, GridironMiss.week_id == Week.id).filter(GridironMiss.entry_id == entry.id)
    if through_week is not None:
        q = q.filter(Week.number <= through_week)
    return {m.week.number for m in q.all()}


def standings_dropdead(season_year):
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    entries.sort(key=lambda e: (not e.is_active, -(e.eliminated_week or 999)))
    return entries


def standings_loser(season_year):
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).all()
    totals = []
    for e in entries:
        total = sum(p.points or 0 for p in e.picks)
        totals.append((e, total))
    totals.sort(key=lambda t: -t[1])
    return totals


def standings_gridiron(season_year):
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    rows = []
    for e in entries:
        wins = sum(1 for p in e.picks if p.result == "win")
        losses = sum(1 for p in e.picks if p.result == "loss")
        losses += GRIDIRON_MISS_PENALTY_LOSSES * len(e.gridiron_misses)
        pushes = sum(1 for p in e.picks if p.result == "push")
        rows.append((e, wins, losses, pushes))
    rows.sort(key=lambda r: (-r[1], r[2]))
    return rows


def dropdead_status_through_week(season_year, week_number):
    """Drop Dead status/pick as it stood after a given past week."""
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    rows = []
    for e in entries:
        eliminated_by_then = e.eliminated_week is not None and e.eliminated_week <= week_number
        week_pick = next((p for p in e.picks if p.week.number == week_number), None)
        rows.append(
            {
                "entry": e,
                "is_active": not eliminated_by_then,
                "eliminated_week": e.eliminated_week if eliminated_by_then else None,
                "week_pick": week_pick,
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
        misses_through = len(_gridiron_missed_week_numbers(e, week_number))
        losses += GRIDIRON_MISS_PENALTY_LOSSES * misses_through
        ties = sum(1 for p in picks_through if p.result == "push")

        week_picks = []
        for p in e.picks:
            if p.week.number != week_number:
                continue
            if p.market == "spread":
                label = p.game.home_team if p.side == "home" else p.game.away_team
            else:
                label = f"{p.side.capitalize()} {p.game.over_under}"
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
                    team_label = f"{p.side.capitalize()} {p.game.over_under}"

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
                for wn in sorted(_gridiron_missed_week_numbers(e)):
                    rows.append(
                        {
                            "pool": pool_name,
                            "entry_label": e.label,
                            "week": wn,
                            "team": "MISSED WEEK",
                            "result": "loss",
                            "points": -GRIDIRON_MISS_PENALTY_LOSSES,
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
    rows = []
    for e in entries:
        missed = _gridiron_missed_week_numbers(e)
        cells = {}
        for wn in week_numbers:
            if wn in missed:
                cells[wn] = {"wins": 0, "losses": GRIDIRON_MISS_PENALTY_LOSSES, "ties": 0, "missed": True}
                continue
            week_picks = [p for p in e.picks if p.week.number == wn]
            if week_picks:
                cells[wn] = {
                    "wins": sum(1 for p in week_picks if p.result == "win"),
                    "losses": sum(1 for p in week_picks if p.result == "loss"),
                    "ties": sum(1 for p in week_picks if p.result == "push"),
                    "missed": False,
                }
            else:
                cells[wn] = None
        wins = sum(1 for p in e.picks if p.result == "win")
        losses = sum(1 for p in e.picks if p.result == "loss") + GRIDIRON_MISS_PENALTY_LOSSES * len(missed)
        ties = sum(1 for p in e.picks if p.result == "push")
        rows.append({"entry": e, "cells": cells, "wins": wins, "losses": losses, "ties": ties})
    rows.sort(key=lambda r: (-r["wins"], r["losses"]))
    return rows
