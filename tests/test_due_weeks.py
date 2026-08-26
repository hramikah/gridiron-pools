"""Catch-up processing for weeks whose deadline has passed.

The bug these lock down: ensure_missed_processed() only ever settles the week
it is handed, and every player route handed it the *current* week. A week that
stopped being current before anyone loaded a page after its deadline was never
settled at all -- no 0-5, no Gridiron makeup, no Drop Dead no-show elimination
-- and nothing ever came back for it.
"""

from models import Entry, GridironMiss, Week, db
from scoring import due_weeks, ensure_missed_processed, process_due_weeks

SEASON = 2026


def test_old_behaviour_leaves_earlier_weeks_unsettled(app, make_week, make_entry):
    """The shape of the original bug, spelled out: settling only the current
    week leaves every skipped week owed forever."""
    w1 = make_week(1)
    w2 = make_week(2)
    make_entry("noshow")

    ensure_missed_processed(w2)  # what the routes used to do: current week only

    assert w2.missed_processed is True
    assert w1.missed_processed is False
    assert [w.number for w in due_weeks(SEASON)] == [1]


def test_process_due_weeks_settles_every_past_deadline_week(app, make_week, make_entry):
    w1 = make_week(1)
    w2 = make_week(2)
    w3 = make_week(3)
    entry = make_entry("noshow")

    settled = process_due_weeks(SEASON)

    assert [w.number for w in settled] == [1, 2, 3]
    assert all(w.missed_processed for w in (w1, w2, w3))
    # The entry that never picked is charged for each missed week, which is the
    # whole point of settling them.
    assert GridironMiss.query.filter_by(entry_id=entry.id).count() == 3
    assert due_weeks(SEASON) == []


def test_future_weeks_are_left_alone(app, make_week, make_entry):
    past = make_week(1)
    upcoming = make_week(2, future=True)
    make_entry("noshow")

    process_due_weeks(SEASON)

    assert past.missed_processed is True
    assert upcoming.missed_processed is False, "a week still open for picks must not be settled"


def test_oldest_first(app, make_week, make_entry):
    """Week N has to settle before N+1: the Gridiron makeup week is the week
    straight after the first miss, so judging them out of order misprices it."""
    make_week(3)
    make_week(1)
    make_week(2)
    make_entry("noshow")

    assert [w.number for w in due_weeks(SEASON)] == [1, 2, 3]


def test_pool_filter(app, make_week, make_entry):
    g = make_week(1, pool="gridiron")
    d = make_week(1, pool="dropdead")
    make_entry("noshow")

    process_due_weeks(SEASON, "gridiron")

    assert g.missed_processed is True
    assert d.missed_processed is False
    assert [w.pool for w in due_weeks(SEASON)] == ["dropdead"]


def test_is_idempotent(app, make_week, make_entry):
    make_week(1)
    entry = make_entry("noshow")

    process_due_weeks(SEASON)
    second_run = process_due_weeks(SEASON)

    assert second_run == [], "a settled week must not be settled twice"
    assert GridironMiss.query.filter_by(entry_id=entry.id).count() == 1


def test_a_stuck_loser_week_does_not_block_other_pools(app, make_week, make_entry):
    """A Loser week waits for its Monday night game before it can assign the
    no-show auto-pick. That wait must not hold up anything else."""
    loser = make_week(1, pool="loser")
    gridiron = make_week(1, pool="gridiron")
    make_entry("noshow", pool="loser")
    make_entry("noshow2")

    settled = process_due_weeks(SEASON)

    assert loser.missed_processed is False, "no MNF game entered yet"
    assert gridiron.missed_processed is True
    assert [w.pool for w in settled] == ["gridiron"]
    assert [w.pool for w in due_weeks(SEASON)] == ["loser"]
