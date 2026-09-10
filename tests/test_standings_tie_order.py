"""A tie outranks no record at all.

Gridiron scores a push as neither a win nor a loss, so an entry that pushed
a slot and an entry with nothing graded yet both read wins=0, losses=0. The
standings sorted on (-wins, losses) alone, which made those two keys
identical -- the rows landed in whatever order the query returned and both
printed the same place number. Most ties now breaks a level W-L.

Every week here is left OPEN on purpose (future=True). That is the state
this actually shows up in: mid-week, some games final and some not, empty
slots not yet charged as losses. Once a week closes, wins + losses + ties
is fixed at the pick allowance, so a level W-L forces a level tie count and
the ordering can no longer be observed.
"""

from conftest import SEASON
from models import Pick, db


def place(entry, week, results):
    """Give an entry one pick per entry in `results`, each on its own game."""
    games = week.games
    assert len(results) <= len(games), "week needs more games than picks"
    for game, result in zip(games, results):
        db.session.add(
            Pick(
                entry_id=entry.id,
                week_id=week.id,
                pool="gridiron",
                game_id=game.id,
                market="spread",
                side="home",
                result=result,
                points=1 if result == "win" else 0,
            )
        )
    db.session.commit()


def names(rows):
    return [r[1].user.username for r in rows]


def records(rows):
    return {r[1].user.username: (r[2], r[3], r[4]) for r in rows}


def ranks(rows):
    return {r[1].user.username: r[0] for r in rows}


def test_a_tie_ranks_above_no_record(app, make_week, make_entry):
    """0-0-1 sits above 0-0-0, and takes a distinct place number."""
    from scoring import standings_gridiron

    week = make_week(1, future=True)
    # Created in the order that used to leave them shuffled: the two entries
    # with nothing graded bracket the one that pushed.
    make_entry("nothing_a")
    pushed = make_entry("pushed")
    make_entry("nothing_b")
    place(pushed, week, ["push"])

    rows = standings_gridiron(SEASON)

    assert records(rows)["pushed"] == (0, 0, 1)
    assert records(rows)["nothing_a"] == (0, 0, 0)
    assert names(rows)[0] == "pushed", names(rows)
    r = ranks(rows)
    assert r["pushed"] < r["nothing_a"] == r["nothing_b"]


def test_ties_break_a_level_record_generally(app, make_week, make_entry):
    """Not just at 0-0: 5-3-1 outranks 5-3-0 too."""
    from scoring import standings_gridiron

    week = make_week(1, games=10, future=True)
    plain = make_entry("plain")
    tied = make_entry("tied")
    place(plain, week, ["win"] * 5 + ["loss"] * 3)
    place(tied, week, ["win"] * 5 + ["loss"] * 3 + ["push"])

    rows = standings_gridiron(SEASON)

    assert records(rows)["tied"] == (5, 3, 1)
    assert records(rows)["plain"] == (5, 3, 0)
    assert ranks(rows)["tied"] < ranks(rows)["plain"]


def test_wins_and_losses_still_outrank_ties(app, make_week, make_entry):
    """Ties are the last tiebreak, never ahead of the record itself."""
    from scoring import standings_gridiron

    week = make_week(1, future=True)
    winner = make_entry("winner")
    tie_only = make_entry("tie_only")
    loser = make_entry("loser")
    place(winner, week, ["win"])
    place(tie_only, week, ["push"] * 3)
    place(loser, week, ["loss"])

    assert names(standings_gridiron(SEASON)) == ["winner", "tie_only", "loser"]


def test_matrix_agrees_with_the_standings_table(app, make_week, make_entry):
    """The All Weeks grid ranks the same way the season table does."""
    from scoring import gridiron_matrix, standings_gridiron

    week = make_week(1, future=True)
    make_entry("nothing")
    pushed = make_entry("pushed")
    place(pushed, week, ["push", "push"])

    table = ranks(standings_gridiron(SEASON))
    grid = {r["entry"].user.username: r["rank"] for r in gridiron_matrix(SEASON, [1])}
    assert grid == table
    assert grid["pushed"] < grid["nothing"]


def test_through_week_view_agrees_too(app, make_week, make_entry):
    """The per-week history view uses the same ordering."""
    from scoring import gridiron_record_through_week

    week = make_week(1, future=True)
    make_entry("nothing")
    pushed = make_entry("pushed")
    place(pushed, week, ["push"])

    rows = gridiron_record_through_week(SEASON, 1)
    assert [r[0].user.username for r in rows] == ["pushed", "nothing"]
