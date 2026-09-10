"""A tie outranks no record at all.

Gridiron scores a push as neither a win nor a loss, so an entry that pushed
a slot and an entry with nothing graded yet both read wins=0, losses=0. The
standings sorted on (-wins, losses) alone, which made those two keys
identical -- the rows landed in whatever order the query returned and both
printed the same place number. Most ties now breaks a level W-L.

Every week here is CLOSED (deadline passed) with every slot filled and only
some picks graded. That is the window this is visible in: from Saturday
noon, when the week's results are released to the standings, until the last
game goes final on Monday night. Before the deadline the whole week is held
back (standings_visible_weeks), and once every game is final wins + losses
+ ties is pinned to the pick allowance, so a level W-L forces a level tie
count and the ordering can no longer be observed.
"""

from conftest import SEASON
from models import Pick, db

ALLOWANCE = 5  # GRIDIRON_NORMAL_PICKS -- fill every slot, or empties score as losses


def place(entry, week, results):
    """Fill all five slots, each pick graded as given. None means pending."""
    assert len(results) == ALLOWANCE, "fill the week, or empty slots become losses"
    for game, result in zip(week.games, results):
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


def pending(n):
    return [None] * n


def names(rows):
    return [r[1].user.username for r in rows]


def records(rows):
    return {r[1].user.username: (r[2], r[3], r[4]) for r in rows}


def ranks(rows):
    return {r[1].user.username: r[0] for r in rows}


def test_a_tie_ranks_above_no_record(app, make_week, make_entry):
    """0-0-1 sits above 0-0-0, and takes a distinct place number."""
    from scoring import standings_gridiron

    week = make_week(1)
    # Created in the order that used to leave them shuffled: the two entries
    # with nothing graded bracket the one that pushed.
    nothing_a = make_entry("nothing_a")
    pushed = make_entry("pushed")
    nothing_b = make_entry("nothing_b")
    place(pushed, week, ["push"] + pending(4))
    place(nothing_a, week, pending(5))
    place(nothing_b, week, pending(5))

    rows = standings_gridiron(SEASON)

    assert records(rows)["pushed"] == (0, 0, 1)
    assert records(rows)["nothing_a"] == (0, 0, 0)
    assert names(rows)[0] == "pushed", names(rows)
    r = ranks(rows)
    assert r["pushed"] < r["nothing_a"] == r["nothing_b"]


def test_ties_break_a_level_record_generally(app, make_week, make_entry):
    """Not just at 0-0: 5-3-1 outranks 5-3-0 too."""
    from scoring import standings_gridiron

    w1, w2 = make_week(1), make_week(2)
    plain = make_entry("plain")
    tied = make_entry("tied")
    for e in (plain, tied):
        place(e, w1, ["win"] * 5)
    place(plain, w2, ["loss"] * 3 + pending(2))
    place(tied, w2, ["loss"] * 3 + ["push"] + pending(1))

    rows = standings_gridiron(SEASON)

    assert records(rows)["tied"] == (5, 3, 1)
    assert records(rows)["plain"] == (5, 3, 0)
    assert ranks(rows)["tied"] < ranks(rows)["plain"]


def test_wins_and_losses_still_outrank_ties(app, make_week, make_entry):
    """Ties are the last tiebreak, never ahead of the record itself."""
    from scoring import standings_gridiron

    week = make_week(1)
    winner = make_entry("winner")
    tie_only = make_entry("tie_only")
    loser = make_entry("loser")
    place(winner, week, ["win"] + pending(4))
    place(tie_only, week, ["push"] * 3 + pending(2))
    place(loser, week, ["loss"] + pending(4))

    assert names(standings_gridiron(SEASON)) == ["winner", "tie_only", "loser"]


def test_matrix_agrees_with_the_standings_table(app, make_week, make_entry):
    """The All Weeks grid ranks the same way the season table does."""
    from scoring import gridiron_matrix, standings_gridiron

    week = make_week(1)
    nothing = make_entry("nothing")
    pushed = make_entry("pushed")
    place(nothing, week, pending(5))
    place(pushed, week, ["push", "push"] + pending(3))

    table = ranks(standings_gridiron(SEASON))
    grid = {r["entry"].user.username: r["rank"] for r in gridiron_matrix(SEASON, [1])}
    assert grid == table
    assert grid["pushed"] < grid["nothing"]


def test_through_week_view_agrees_too(app, make_week, make_entry):
    """The per-week history view uses the same ordering."""
    from scoring import gridiron_record_through_week

    week = make_week(1)
    nothing = make_entry("nothing")
    pushed = make_entry("pushed")
    place(nothing, week, pending(5))
    place(pushed, week, ["push"] + pending(4))

    rows = gridiron_record_through_week(SEASON, 1)
    assert [r[0].user.username for r in rows] == ["pushed", "nothing"]
