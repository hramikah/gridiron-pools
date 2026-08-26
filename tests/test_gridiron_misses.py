"""Gridiron missed-week, makeup and buy-back accounting.

The numbers asserted here come from rule 8 of the printed rules:

    "With the exception of the last week, any entrant who fails for the first
    time to get his or her picks in by the weekly deadline will have the
    opportunity to pick eight (8) games the following week, with a two (2)
    game penalty. A second and/or subsequent failure, or a failure for the
    last week will result in the loss of all games for that week."

What a sat-out week costs depends on which week it is:

  the first one       nothing. Rule 8 gives a first-time failure the 8-pick
                      makeup "with a two (2) game penalty" and stops there --
                      the 2 games are the price, the week itself is a wash.
                      "The loss of all games for that week" is attached to
                      "a second and/or subsequent failure".
  the makeup week     10: the 8 picks that went unused plus those 2.
  a buy-back week     5, even as a first miss -- $100 was paid to play it.
  any other week      5.

So two missed weeks in a row cost 0 + 10 = 10.

    Commissioner's rulings, 2026-08-21 and 2026-08-22:
      "If you fail to not pick your makeup games, all eight of those picks
       that you get to do plus the two penalty picks count as losses."
      "In that week four, since it was their very first offense, basically,
       nothing happens for them in the standings."
"""

from datetime import datetime, timedelta

import pytest

import helpers
from helpers import deadline_passed, gridiron_signup_deadline
from models import GridironMiss, Pick, db
from scoring import (
    GRIDIRON_BENCH_AFTER_MISSES,
    gridiron_counted_picks,
    gridiron_first_miss_week,
    gridiron_makeup_week,
    gridiron_matrix,
    gridiron_penalty_losses,
    gridiron_penalty_slots,
    gridiron_pick_limit,
    gridiron_week_counts,
    gridiron_week_penalty_losses,
    gridiron_week_records,
    process_missed_picks,
)

from conftest import LONG_PAST, SEASON


def play_season(weeks, entries_plan, submit):
    """Run weeks in order: everyone submits per plan, then the week's
    missed-pick penalties are applied, exactly as the site does once a
    deadline passes."""
    for number in sorted(weeks):
        week = weeks[number]
        for entry, picks_by_week in entries_plan:
            count = picks_by_week.get(number)
            if count == "limit":
                count = gridiron_pick_limit(entry, week)
            if count:
                submit(entry, week, count)
        process_missed_picks(week)


# --------------------------------------------------------------------------
# A single missed week
# --------------------------------------------------------------------------


def test_first_missed_week_costs_only_the_makeup_penalty(make_week, make_entry, submit, record):
    """A first failure is priced at the 2-game penalty and nothing else."""
    weeks = {n: make_week(n) for n in (1, 2)}
    entry = make_entry("misser")
    # Sits out week 1, plays its 8-pick makeup in full and wins them all.
    play_season(weeks, [(entry, {2: "limit"})], submit)

    assert gridiron_first_miss_week(entry) == 1
    assert gridiron_makeup_week(entry) == 2
    assert gridiron_pick_limit(entry, weeks[2]) == 8
    assert gridiron_week_records(SEASON, 1)[entry.id] == (0, 0, 0), "the miss is a wash"
    # 8 wins, and the only losses are the 2-game makeup penalty.
    assert record(entry) == (8, 2, 0)


def test_makeup_week_is_eight_picks_only_once(make_week, make_entry, submit):
    weeks = {n: make_week(n) for n in (1, 2, 3)}
    entry = make_entry("misser")
    play_season(weeks, [(entry, {2: "limit", 3: "limit"})], submit)

    assert gridiron_pick_limit(entry, weeks[2]) == 8
    assert gridiron_pick_limit(entry, weeks[3]) == 5, "the makeup is a one-off"


def test_partly_filled_makeup_week_still_pays_the_penalty(
    make_week, make_entry, submit, record
):
    weeks = {n: make_week(n) for n in (1, 2)}
    entry = make_entry("half_hearted")
    # Turns up for the makeup week but only fills 6 of its 8 slots.
    play_season(weeks, [(entry, {2: 6})], submit)

    # 6 wins; week 1 is free, so 2 unfilled slots + the 2-game penalty.
    assert record(entry) == (6, 4, 0)


# --------------------------------------------------------------------------
# The regression this suite exists for
# --------------------------------------------------------------------------


@pytest.mark.parametrize("first,second", [(1, 2), (2, 3), (7, 8)])
def test_two_missed_weeks_in_a_row_cost_ten(
    make_week, make_entry, submit, record, first, second
):
    """The makeup week is always the week straight after the first miss, so
    back-to-back misses mean sitting out the makeup week: nothing for the
    first, then the 8-pick allowance plus the 2-game penalty for the
    second."""
    weeks = {n: make_week(n) for n in range(1, second + 2)}
    entry = make_entry("double_misser")
    plan = {n: "limit" for n in range(1, second + 2) if n not in (first, second)}
    play_season(weeks, [(entry, plan)], submit)

    wins, losses, _ties = record(entry)
    missed_cost = losses  # every submitted pick was a win, so losses are all penalty
    assert missed_cost == 10, "nothing for the first week, 8 + 2 for the makeup week"
    assert wins == 5 * len(plan)


def test_blown_makeup_week_still_pays_the_two_game_penalty(make_week, make_entry, submit, record):
    """Sitting out the makeup week costs all 10 games it was worth: the 8
    picks that went unused and the 2 penalties that came with them."""
    weeks = {n: make_week(n) for n in (1, 2)}
    entry = make_entry("no_show_twice")
    play_season(weeks, [(entry, {})], submit)

    assert sorted(m.week.number for m in GridironMiss.query.filter_by(entry_id=entry.id)) == [1, 2]
    assert gridiron_penalty_losses(entry) == 2, "charged even though nothing was picked"
    assert gridiron_week_records(SEASON, 1)[entry.id] == (0, 0, 0), "the first miss is free"
    assert gridiron_week_records(SEASON, 2)[entry.id] == (0, 10, 0), "the week itself is 0-10"
    assert record(entry) == (0, 10, 0)


def test_three_missed_weeks_cost_fifteen(make_week, make_entry, submit, record):
    weeks = {n: make_week(n) for n in (1, 2, 3, 4)}
    entry = make_entry("serial")
    play_season(weeks, [(entry, {4: "limit"})], submit)

    # 5 wins in week 4. Week 1 is free, week 2 is the blown makeup week at
    # 10, week 3 is a flat 5 -- the makeup is a one-off.
    assert record(entry) == (5, 15, 0)


def test_missed_week_capped_at_what_was_pickable(make_week, make_entry, submit, record):
    # Two-game weeks with no over/unders offer 2 slots each. Week 1 is a free
    # first miss; week 2 is the makeup, where the 8-pick allowance is capped
    # at the 2 slots that existed -- plus the 2-game penalty, which is not
    # capped because it is not a pick.
    weeks = {n: make_week(n, games=2, over_under=None) for n in (1, 2)}
    entry = make_entry("short_slate")
    play_season(weeks, [(entry, {})], submit)

    assert record(entry) == (0, 4, 0)


# --------------------------------------------------------------------------
# Benching
# --------------------------------------------------------------------------


def test_benched_after_six_misses(make_week, make_entry, submit):
    weeks = {n: make_week(n) for n in range(1, 8)}
    entry = make_entry("ghost")
    play_season(weeks, [(entry, {})], submit)

    assert GridironMiss.query.filter_by(entry_id=entry.id).count() == GRIDIRON_BENCH_AFTER_MISSES + 1
    assert entry.is_active is False
    assert entry.eliminated_week == GRIDIRON_BENCH_AFTER_MISSES + 1


def test_benched_entry_record_freezes(make_week, make_entry, submit, record):
    """A benched entry stops collecting losses. It used to keep taking 5 a
    week to the end of the schedule -- a never-shows entry finished 0-95."""
    weeks = {n: make_week(n) for n in range(1, 19)}
    entry = make_entry("ghost")
    play_season(weeks, [(entry, {})], submit)

    assert entry.is_active is False
    wins, losses, ties = record(entry)
    # Week 1 is free. Week 2, the blown makeup week: 10. Weeks 3-6: 5 each.
    # Nothing after the benching week.
    assert (wins, losses, ties) == (0, 30, 0)


# --------------------------------------------------------------------------
# Gridiron has no buy-back (removed August 2026)
# --------------------------------------------------------------------------


def test_gridiron_never_grants_more_than_the_makeup_allowance(make_week, make_entry, submit):
    """Nothing can push a Gridiron week past 8 picks now that the 10-pick
    buy-back catch-up week is gone."""
    entry = make_entry("nobuyback")
    weeks = {n: make_week(n, buyback_open=True, future=True) for n in (1, 2, 3)}
    assert gridiron_pick_limit(entry, weeks[1]) == 5
    assert gridiron_pick_limit(entry, weeks[2]) == 5
    # A first miss still grants the 8-pick makeup, and nothing grants 10.
    submit(entry, weeks[1], 0)
    process_missed_picks(weeks[1])
    assert gridiron_pick_limit(entry, weeks[2]) == 8
    assert gridiron_pick_limit(entry, weeks[3]) == 5


def test_gridiron_weeks_are_never_voided(make_week, make_entry, submit, record):
    """A week always counts. buyback_week is Drop Dead's column now; even if a
    stale value is sitting on a Gridiron row, week 1 still counts."""
    entry = make_entry("stalebuyback")
    weeks = {1: make_week(1)}
    submit(entry, weeks[1], 5)
    entry.buyback_week = 1  # legacy data from before the buy-back was removed
    db.session.commit()
    assert gridiron_week_counts(entry, 1) is True
    assert len(gridiron_counted_picks(entry)) == 5
    assert record(entry) == (5, 0, 0)


def test_gridiron_signups_close_at_the_week_one_deadline(make_week, app):
    """Entries used to close at week 2, back when a late entrant could buy
    back into week 1. That window is gone."""
    week1 = make_week(1)
    make_week(2)
    assert gridiron_signup_deadline(SEASON) == week1.pick_deadline




# --------------------------------------------------------------------------
# Buy back, then no-show the catch-up week
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# The 2 penalty losses, shown where they happened
# --------------------------------------------------------------------------


def test_penalty_losses_land_in_the_makeup_weeks_own_column(
    make_week, make_entry, submit
):
    """The weekly record has to add up to the season total: week 3 reads 8-2,
    not 8-0 with two losses appearing from nowhere in the season line."""
    weeks = {1: make_week(1), 2: make_week(2)}
    entry = make_entry("makeup_player")
    process_missed_picks(weeks[1])  # week 1 sat out
    submit(entry, weeks[2], 8, result="win")

    assert gridiron_week_penalty_losses(entry, 2) == 2
    assert gridiron_week_records(SEASON, 1)[entry.id] == (0, 0, 0), "free first miss"
    assert gridiron_week_records(SEASON, 2)[entry.id] == (8, 2, 0)

    cells = {r["entry"].id: r["cells"] for r in gridiron_matrix(SEASON, [1, 2])}
    assert cells[entry.id][2]["losses"] == 2
    assert cells[entry.id][2]["wins"] == 8


def test_penalty_slots_show_while_the_makeup_week_is_still_open(
    make_week, make_entry
):
    """The 2 automatic losses are visible on the pick page from the moment
    the week opens, not only once the deadline has passed."""
    weeks = {1: make_week(1), 2: make_week(2, future=True)}
    entry = make_entry("early_bird")
    process_missed_picks(weeks[1])

    assert gridiron_penalty_slots(entry, weeks[2]) == 2
    assert gridiron_penalty_losses(entry) == 0, "not charged until picks are in"
    assert gridiron_penalty_slots(entry, weeks[1]) == 0


def test_no_penalty_slots_without_a_miss(make_week, make_entry):
    weeks = {1: make_week(1), 2: make_week(2, future=True)}
    entry = make_entry("clean")
    assert gridiron_penalty_slots(entry, weeks[2]) == 0


# --------------------------------------------------------------------------
# Loser Pool: the no-show auto-pick has to be scored
# --------------------------------------------------------------------------


def test_loser_no_show_autopick_is_scored(make_week, make_entry, app, team):
    """The auto-assigned MNF team is scored even when the result was already
    in before the assignment happened.

    A Pick is scored when its game is scored, and these rows are created
    afterwards -- by a page load or the admin's Process Missed Picks button,
    either of which can land after Monday night's score. Left alone they stay
    at "pending": grey in the reports, and worth nothing in a pool where a
    no-show is meant to cost you points.
    """
    from models import Game, LoserPoolPoints, Pick
    from scoring import score_game

    home = team("Tampa Bay", "Buccaneers")
    away = team("Denver", "Broncos")
    db.session.add(LoserPoolPoints(season_year=SEASON, team_id=away.id, points=29))
    week = make_week(1, games=0, pool="loser")
    game = Game(
        week_id=week.id, pool="loser", sport="nfl",
        away_team=away.name, home_team=home.name,
        away_team_id=away.id, home_team_id=home.id,
        is_mnf=True, is_final=True, away_score=13, home_score=10,
    )
    db.session.add(game)
    entry = make_entry("forgot", pool="loser")
    db.session.commit()

    # The week is scored first, and only then does anyone notice the no-show.
    score_game(game)
    process_missed_picks(week)

    pick = Pick.query.filter_by(entry_id=entry.id, pool="loser").one()
    assert pick.team_id == away.id, "the MNF visitor is what gets assigned"
    assert pick.result == "loss", "Broncos won, so a pick of them to lose is a loss"
    assert pick.points == -29
