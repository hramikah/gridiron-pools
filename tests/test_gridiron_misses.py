"""Gridiron missed-week, makeup and buy-back accounting.

The numbers asserted here come from rule 8 of the printed rules:

    "With the exception of the last week, any entrant who fails for the first
    time to get his or her picks in by the weekly deadline will have the
    opportunity to pick eight (8) games the following week, with a two (2)
    game penalty. A second and/or subsequent failure, or a failure for the
    last week will result in the loss of all games for that week."

So a week sat out costs a flat 5. The makeup week's extra picks are an
opportunity, and its 2-game penalty is the price of taking that opportunity
-- an entrant who doesn't turn up for the makeup week has simply missed a
second week, and pays 5 for it like any other. Two missed weeks in a row cost
10, not 15.
"""

import pytest

from models import GridironMiss, Pick, db
from scoring import (
    GRIDIRON_BENCH_AFTER_MISSES,
    GRIDIRON_BUYBACK_WEEK,
    gridiron_buyback_available,
    gridiron_first_miss_week,
    gridiron_makeup_week,
    gridiron_penalty_losses,
    gridiron_pick_limit,
    process_missed_picks,
)

from conftest import SEASON


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


def test_missed_week_costs_five(make_week, make_entry, submit, record):
    weeks = {n: make_week(n) for n in (1, 2)}
    entry = make_entry("misser")
    # Sits out week 1, plays its 8-pick makeup in full and wins them all.
    play_season(weeks, [(entry, {2: "limit"})], submit)

    assert gridiron_first_miss_week(entry) == 1
    assert gridiron_makeup_week(entry) == 2
    assert gridiron_pick_limit(entry, weeks[2]) == 8
    # 8 wins; 5 losses for week 1, plus the 2-game makeup penalty.
    assert record(entry) == (8, 7, 0)


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

    # 6 wins; 5 (week 1) + 2 unfilled slots + the 2-game penalty.
    assert record(entry) == (6, 9, 0)


# --------------------------------------------------------------------------
# The regression this suite exists for
# --------------------------------------------------------------------------


@pytest.mark.parametrize("first,second", [(1, 2), (2, 3), (7, 8)])
def test_two_missed_weeks_in_a_row_cost_ten_not_fifteen(
    make_week, make_entry, submit, record, first, second
):
    """Rule 8 prices a second failure at 'the loss of all games for that
    week'. Because the makeup week is always the week straight after the
    first miss, back-to-back misses used to be charged the 8-slot allowance
    plus the 2-game penalty -- 10 for the second week alone, 15 across the
    two."""
    weeks = {n: make_week(n) for n in range(1, second + 2)}
    entry = make_entry("double_misser")
    plan = {n: "limit" for n in range(1, second + 2) if n not in (first, second)}
    play_season(weeks, [(entry, plan)], submit)

    wins, losses, _ties = record(entry)
    missed_cost = losses  # every submitted pick was a win, so losses are all penalty
    assert missed_cost == 10, "two missed weeks must cost 5 + 5"
    assert wins == 5 * len(plan)


def test_blown_makeup_week_drops_the_two_game_penalty(make_week, make_entry, submit, record):
    weeks = {n: make_week(n) for n in (1, 2)}
    entry = make_entry("no_show_twice")
    play_season(weeks, [(entry, {})], submit)

    assert sorted(m.week.number for m in GridironMiss.query.filter_by(entry_id=entry.id)) == [1, 2]
    assert record(entry) == (0, 10, 0)


def test_three_missed_weeks_cost_fifteen(make_week, make_entry, submit, record):
    weeks = {n: make_week(n) for n in (1, 2, 3, 4)}
    entry = make_entry("serial")
    play_season(weeks, [(entry, {4: "limit"})], submit)

    # 5 wins in week 4; weeks 1-3 at a flat 5 each.
    assert record(entry) == (5, 15, 0)


def test_missed_week_capped_at_what_was_pickable(make_week, make_entry, submit, record):
    # A two-game week with no over/unders offers only 2 slots, so a no-show
    # can't be charged more than 2 losses.
    weeks = {1: make_week(1, games=2, over_under=None)}
    entry = make_entry("short_slate")
    play_season(weeks, [(entry, {})], submit)

    assert record(entry) == (0, 2, 0)


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
    # 5 losses a week through the benching week (6), and nothing after.
    assert (wins, losses, ties) == (0, 30, 0)


# --------------------------------------------------------------------------
# The paid week-2 buy-back
# --------------------------------------------------------------------------


def _buy_back(entry, week):
    """What blueprints.gridiron.buyback writes, without the HTTP layer."""
    entry.buyback_week = week.number - 1
    entry.buy_backs_used = (entry.buy_backs_used or 0) + 1
    db.session.commit()


def test_buyback_voids_week_one_after_a_miss(make_week, make_entry, submit, record):
    # Week 2 is still open -- that's when the buy-back is offered.
    weeks = {1: make_week(1), 2: make_week(2, future=True, buyback_open=True)}
    entry = make_entry("forgot_week_one")
    play_season({1: weeks[1]}, [(entry, {})], submit)
    assert record(entry) == (0, 5, 0), "the miss lands before the buy-back"

    assert gridiron_buyback_available(entry, weeks[2]) is True
    _buy_back(entry, weeks[2])

    # Week 1 stops counting outright, and the makeup allowance goes back in
    # the bank rather than being spent on week 1.
    assert gridiron_first_miss_week(entry) is None
    assert gridiron_makeup_week(entry) is None
    assert record(entry) == (0, 0, 0)
    # A double slate in the week it was bought: the 5 games the fee erased
    # plus the 5 this week is worth, so the entry ends week 2 level on games
    # played with everyone who never missed.
    assert gridiron_pick_limit(entry, weeks[2]) == 10
    assert gridiron_penalty_losses(entry) == 0, "no makeup penalty -- it wasn't the makeup"


def test_buyback_preserves_the_makeup_for_a_later_miss(make_week, make_entry, submit, record):
    # Week 2 is the 10-pick catch-up slate, so these weeks need >8 games.
    weeks = {n: make_week(n, games=12) for n in range(1, 6)}
    entry = make_entry("forgot_twice")
    play_season({1: weeks[1]}, [(entry, {})], submit)
    _buy_back(entry, weeks[2])

    # Plays weeks 2 and 3, then misses week 4.
    play_season(
        {n: weeks[n] for n in (2, 3, 4, 5)},
        [(entry, {2: "limit", 3: "limit", 5: "limit"})],
        submit,
    )

    assert gridiron_first_miss_week(entry) == 4, "week 1's miss was bought out"
    assert gridiron_makeup_week(entry) == 5
    assert gridiron_pick_limit(entry, weeks[5]) == 8, "the banked makeup is still there"


def test_buyback_erases_a_bad_week_that_was_actually_played(
    make_week, make_entry, submit, record
):
    """The other half of the fee's purpose: an entrant who did submit and
    hated the result pays to wipe it."""
    weeks = {1: make_week(1), 2: make_week(2, future=True, buyback_open=True)}
    entry = make_entry("played_badly")
    submit(entry, weeks[1], 5, result="loss")
    process_missed_picks(weeks[1])
    assert record(entry) == (0, 5, 0)

    _buy_back(entry, weeks[2])
    assert record(entry) == (0, 0, 0)

    submit(entry, weeks[2], 5, result="win")
    assert record(entry) == (5, 0, 0)


def test_buyback_wipes_wins_too(make_week, make_entry, submit, record):
    """The slate is clean, not cherry-picked -- a 3-2 week 1 loses its 3
    wins along with its 2 losses."""
    weeks = {1: make_week(1), 2: make_week(2, future=True, buyback_open=True)}
    entry = make_entry("mixed")
    submit(entry, weeks[1], 3, result="win")
    process_missed_picks(weeks[1])
    _buy_back(entry, weeks[2])

    assert record(entry) == (0, 0, 0)


def test_buyback_offer_window(make_week, make_entry):
    entry = make_entry("shopper")
    week1 = make_week(1, buyback_open=True, future=True)
    week2 = make_week(GRIDIRON_BUYBACK_WEEK, buyback_open=True, future=True)
    week3 = make_week(3, buyback_open=True, future=True)

    assert gridiron_buyback_available(entry, week2) is True
    assert gridiron_buyback_available(entry, week1) is False, "week 2 only"
    assert gridiron_buyback_available(entry, week3) is False, "week 2 only"
    assert gridiron_buyback_available(entry, None) is False


def test_buyback_needs_the_admin_flag(make_week, make_entry):
    entry = make_entry("shopper")
    closed = make_week(GRIDIRON_BUYBACK_WEEK, buyback_open=False, future=True)
    assert gridiron_buyback_available(entry, closed) is False


def test_buyback_not_offered_in_preseason(make_week, make_entry):
    entry = make_entry("shopper")
    pre = make_week(GRIDIRON_BUYBACK_WEEK, buyback_open=True, future=True, is_preseason=True)
    assert gridiron_buyback_available(entry, pre) is False


def test_buyback_is_once_per_entry(make_week, make_entry):
    entry = make_entry("repeat_shopper")
    week2 = make_week(GRIDIRON_BUYBACK_WEEK, buyback_open=True, future=True)
    assert gridiron_buyback_available(entry, week2) is True

    _buy_back(entry, week2)
    assert gridiron_buyback_available(entry, week2) is False


def test_buyback_closes_at_the_deadline(make_week, make_entry):
    entry = make_entry("late")
    past_week2 = make_week(GRIDIRON_BUYBACK_WEEK, buyback_open=True)  # deadline long gone
    assert gridiron_buyback_available(entry, past_week2) is False


def test_buyback_week_is_worth_ten_games_played(make_week, make_entry, submit, record):
    """The catch-up slate is real games, not bookkeeping: an entry that buys
    back and wins all ten finishes week 2 on ten wins -- level with a player
    who went 5-0 in each of the first two weeks and never paid a thing."""
    # 12 games a week, so a 10-pick allowance has somewhere to land.
    weeks = {1: make_week(1, games=12), 2: make_week(2, games=12)}
    bought_back = make_entry("bought_back")
    never_missed = make_entry("never_missed")

    play_season(
        {1: weeks[1]},
        [(bought_back, {}), (never_missed, {1: "limit"})],
        submit,
    )
    _buy_back(bought_back, weeks[2])
    assert gridiron_pick_limit(bought_back, weeks[2]) == 10

    play_season(
        {2: weeks[2]},
        [(bought_back, {2: "limit"}), (never_missed, {2: "limit"})],
        submit,
    )

    assert record(bought_back) == (10, 0, 0)
    assert record(never_missed) == (10, 0, 0)
