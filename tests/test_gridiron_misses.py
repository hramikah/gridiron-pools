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
from helpers import deadline_passed, gridiron_buyback_deadline
from models import GridironMiss, Pick, db
from scoring import (
    GRIDIRON_BENCH_AFTER_MISSES,
    GRIDIRON_BUYBACK_WEEK,
    gridiron_buyback_available,
    gridiron_first_miss_week,
    gridiron_makeup_week,
    gridiron_matrix,
    gridiron_penalty_losses,
    gridiron_penalty_slots,
    gridiron_pick_limit,
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
    assert record(entry) == (0, 0, 0), "a first miss costs nothing to begin with"

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


# --------------------------------------------------------------------------
# The buy-back window: Thursday 7:00 PM Eastern, not the Saturday deadline
# --------------------------------------------------------------------------


def test_buyback_deadline_is_that_weeks_thursday_at_seven(make_week):
    """A Saturday-noon pick deadline puts the buy-back cutoff on the Thursday
    two days earlier, at 7pm -- before Thursday night kicks off."""
    week2 = make_week(GRIDIRON_BUYBACK_WEEK, buyback_open=True, future=True)
    week2.pick_deadline = datetime(2026, 9, 19, 12, 0)  # a Saturday
    db.session.commit()

    assert gridiron_buyback_deadline(week2) == datetime(2026, 9, 17, 19, 0)


def test_buyback_closes_thursday_evening_not_saturday(make_week, make_entry, monkeypatch):
    """The offer is gone on Thursday night even though picks for the week are
    still open until Saturday noon."""
    entry = make_entry("clockwatcher")
    week2 = make_week(GRIDIRON_BUYBACK_WEEK, buyback_open=True, future=True)
    week2.pick_deadline = datetime(2026, 9, 19, 12, 0)  # Saturday noon
    db.session.commit()

    monkeypatch.setattr(helpers, "now_eastern", lambda: datetime(2026, 9, 17, 18, 59))
    assert gridiron_buyback_available(entry, week2) is True, "still Thursday afternoon"

    monkeypatch.setattr(helpers, "now_eastern", lambda: datetime(2026, 9, 17, 19, 1))
    assert gridiron_buyback_available(entry, week2) is False, "past 7pm Thursday"

    # ...and the week itself is still open for ordinary picks.
    assert deadline_passed(week2) is False


def test_buyback_open_to_a_five_and_oh_entry(make_week, make_entry, submit):
    """Any record qualifies -- the offer isn't limited to entries that
    struggled or sat the week out."""
    weeks = {1: make_week(1), 2: make_week(2, future=True, buyback_open=True)}
    perfect = make_entry("five_and_oh")
    submit(perfect, weeks[1], 5, result="win")
    process_missed_picks(weeks[1])

    assert gridiron_buyback_available(perfect, weeks[2]) is True


# --------------------------------------------------------------------------
# Buy back, then no-show the catch-up week
# --------------------------------------------------------------------------


def test_rebuy_then_no_show_costs_five_and_moves_the_makeup_to_week_three(
    make_week, make_entry, submit, record
):
    """Hunter's scenario end to end: pay $100 in week 2, then don't pick.

    The 10-slot allowance evaporates -- a sat-out week is a flat 0-5, never
    0-10 -- and the one-time makeup lands on week 3: 8 picks plus the 2-game
    penalty.
    """
    # Week 3 is still open at this point -- otherwise it would count as a
    # second sat-out week and bill another 5 on top.
    weeks = {1: make_week(1), 2: make_week(2), 3: make_week(3, future=True)}
    entry = make_entry("paid_then_vanished")

    submit(entry, weeks[1], 5, result="loss")
    process_missed_picks(weeks[1])
    _buy_back(entry, weeks[2])

    # Week 2 comes and goes with nothing submitted.
    process_missed_picks(weeks[2])

    assert record(entry) == (0, 5, 0), "flat 0-5 for the week, not 0-10"
    assert gridiron_first_miss_week(entry) == 2, "week 1 was bought out of the way"
    assert gridiron_makeup_week(entry) == 3
    assert gridiron_pick_limit(entry, weeks[3]) == 8
    assert gridiron_penalty_slots(entry, weeks[3]) == 2, "shown while picks are open"

    # Eight wins in week 3, and the 2 penalty losses ride along once it closes.
    submit(entry, weeks[3], 8, result="win")
    weeks[3].pick_deadline = LONG_PAST + timedelta(days=21)
    db.session.commit()

    assert gridiron_penalty_losses(entry) == 2
    assert record(entry) == (8, 7, 0), "5 for the missed week + 2 penalty"
    assert gridiron_week_records(SEASON, 3)[entry.id] == (8, 2, 0)


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


def test_rebuy_no_show_is_five_but_blowing_the_makeup_after_it_is_ten(
    make_week, make_entry, submit, record
):
    """The two rulings of 2026-08-21 side by side.

    A buy-back week sat out is a flat 0-5 -- it is an ordinary week that the
    fee bought the right to play, not a slate owed from a week already lost.
    The makeup week it then unlocks is charged in full if that is sat out
    too: 8 unused picks plus the 2-game penalty.
    """
    weeks = {1: make_week(1), 2: make_week(2), 3: make_week(3)}
    entry = make_entry("paid_and_vanished_twice")

    submit(entry, weeks[1], 5, result="loss")
    process_missed_picks(weeks[1])
    _buy_back(entry, weeks[2])

    process_missed_picks(weeks[2])
    assert gridiron_week_records(SEASON, 2)[entry.id] == (0, 5, 0), "buy-back week: flat 5"
    assert gridiron_makeup_week(entry) == 3
    assert gridiron_pick_limit(entry, weeks[3]) == 8

    process_missed_picks(weeks[3])
    assert gridiron_week_records(SEASON, 3)[entry.id] == (0, 10, 0), "makeup week: 8 + 2"
    assert record(entry) == (0, 15, 0), "week 1 was bought out; 5 + 10 remain"


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
