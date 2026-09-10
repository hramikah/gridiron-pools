"""Nothing from a week reaches a player-facing page until its deadline.

Games kick off Thursday; picks stay open until Saturday noon. Before this,
a Thursday-night final moved the standings, so anyone watching the table
could read how the field was doing while their own picks were still open.

Scoring is unchanged -- picks are still graded the moment a game goes final.
It is only the release to shared views that waits. (Commissioner's call,
2026-09-10.)
"""

from datetime import timedelta

import pytest
from helpers import now_eastern
from models import Entry, Game, Pick, Week, db

from conftest import SEASON


def in_progress(week):
    """Put a week in the live window: open, and inside its betting window.

    make_week(future=True) dates the deadline a month out, which is open but
    not yet STARTED -- and week_started is the boundary the makeup penalty
    and the on-page notice key off. A day out is Friday, the state this is
    all about.
    """
    week.pick_deadline = now_eastern() + timedelta(days=1)
    db.session.commit()


def close_week(week):
    week.pick_deadline = now_eastern() - timedelta(minutes=1)
    db.session.commit()


# --------------------------------------------------------------- gridiron


def test_gridiron_results_wait_for_the_deadline(app, make_week, make_entry, submit, record):
    """A win graded on Thursday night is not in the table until Saturday noon."""
    from scoring import standings_gridiron

    week = make_week(1, future=True)      # open: games kicking off, picks still live
    entry = make_entry("player")
    submit(entry, week, 5, result="win")  # all five already final and won

    assert record(entry) == (0, 0, 0), "held while picks are still open"

    close_week(week)
    assert record(entry) == (5, 0, 0), "released at the deadline"


def test_the_hold_is_per_pool(app, make_week, make_entry, submit, team):
    """Weeks are per-pool, so each pool releases on its own deadline."""
    from scoring import standings_gridiron, standings_loser

    g_week = make_week(1)                              # gridiron, closed
    l_week = make_week(1, pool="loser", future=True)   # loser, still open

    g_entry = make_entry("gplayer")
    submit(g_entry, g_week, 5, result="win")

    l_entry = make_entry("lplayer", pool="loser")
    t = team("Test", "Teamers")
    db.session.add(
        Pick(entry_id=l_entry.id, week_id=l_week.id, pool="loser",
             team_id=t.id, result="win", points=30)
    )
    db.session.commit()

    (_, _, wins, _, _), = standings_gridiron(SEASON)
    assert wins == 5, "gridiron's week is closed, so its results are out"

    (_, _, total), = standings_loser(SEASON)
    assert total == 0, "the loser week is still open, so its points are held"

    close_week(l_week)
    (_, _, total), = standings_loser(SEASON)
    assert total == 30


def test_the_makeup_penalty_waits_too(app, make_week, make_entry, record):
    """The 2-game penalty lands with the rest of its week, not on Thursday."""
    from scoring import gridiron_penalty_losses, gridiron_penalty_slots, process_due_weeks

    missed = make_week(1)                  # closed and sat out -> forgiven first miss
    makeup = make_week(2, future=True)
    in_progress(makeup)                    # open, and the league is in it
    entry = make_entry("player")
    process_due_weeks(SEASON)

    assert record(entry) == (0, 0, 0), "the penalty is held with its week"
    # The pick page still shows the two slots the entry cannot fill -- that is
    # the whole point of showing them, and it was never the leak.
    assert gridiron_penalty_slots(entry, makeup) == 2
    assert gridiron_penalty_losses(entry) == 2, "charged on the record from week_started"


# -------------------------------------------------------------- drop dead


@pytest.fixture
def dropdead_loss(app, make_entry, team):
    """An entry knocked out by a game that has already gone final."""

    def _setup(week):
        from scoring import score_game

        entry = make_entry("player", pool="dropdead")
        winner, loser = team("Win", "Ners"), team("Los", "Ers")
        game = Game(
            week_id=week.id, pool="dropdead", sport="nfl",
            home_team="Win Ners", away_team="Los Ers",
            home_team_id=winner.id, away_team_id=loser.id,
            home_score=24, away_score=10, is_final=True,
            kickoff=week.pick_deadline - timedelta(days=2),  # a Thursday game
        )
        db.session.add(game)
        db.session.flush()
        db.session.add(
            Pick(entry_id=entry.id, week_id=week.id, pool="dropdead",
                 team_id=loser.id)
        )
        db.session.commit()
        score_game(game)
        return entry

    return _setup


def test_an_elimination_is_held_but_the_player_is_told(app, make_week, dropdead_loss):
    """The table says Alive; the entry itself is out, so the home card and the
    buy-back offer still work."""
    from scoring import standings_dropdead

    week = make_week(1, pool="dropdead", future=True, games=0)
    entry = dropdead_loss(week)

    assert entry.is_active is False, "scored on the backside, straight away"
    assert entry.eliminated_week == week.number

    (rank, shown), = standings_dropdead(SEASON)
    assert shown.is_active is True, "the shared table waits for the deadline"
    assert shown.eliminated_week is None
    assert shown.id == entry.id, "still the same entry underneath"

    close_week(week)
    (rank, shown), = standings_dropdead(SEASON)
    assert shown.is_active is False
    assert shown.eliminated_week == week.number


def test_the_dropdead_table_does_not_leak_this_weeks_pick(app, make_week, dropdead_loss):
    """dropdead/standings.html prints every entry's picks -- the most direct
    way to read the field's current selections before the deadline."""
    from scoring import standings_dropdead

    week = make_week(1, pool="dropdead", future=True, games=0)
    entry = dropdead_loss(week)
    assert len(entry.picks) == 1, "the pick is really there"

    (_, shown), = standings_dropdead(SEASON)
    assert shown.picks == [], "but the standings cannot see it"

    close_week(week)
    (_, shown), = standings_dropdead(SEASON)
    assert len(shown.picks) == 1


def test_player_history_holds_the_current_week(app, make_week, make_entry, submit):
    """Any player can be selected on Master Standings; their open-week picks
    must not be listed."""
    from scoring import player_pick_history

    week = make_week(1, future=True)
    entry = make_entry("player")
    submit(entry, week, 5, result="win")

    assert player_pick_history(SEASON, entry.user_id) == []

    close_week(week)
    assert len(player_pick_history(SEASON, entry.user_id)) == 5


def test_a_held_week_is_named_so_the_page_can_say_so(app, make_week):
    """standings_hold_week drives the 'results appear Saturday noon' notice."""
    from scoring import standings_hold_week

    week = make_week(1, future=True)
    in_progress(week)
    held = standings_hold_week(SEASON, "gridiron")
    assert held is not None and held.id == week.id

    close_week(week)
    assert standings_hold_week(SEASON, "gridiron") is None, "nothing left to hold"
