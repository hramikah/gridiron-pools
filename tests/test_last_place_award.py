"""Last Place Award: worst overall record, not merely fewest wins.

Rule 12 decides the award on fewest wins and breaks a tie on the MOST
losses. A 1-8-1 and a 1-9-0 both have one win, so keying on wins alone made
them share the award; only the 1-9-0 should have it.
"""

from models import Game, Pick, db
from scoring import gridiron_awards

from conftest import SEASON


def _give(entry, week, results):
    """Save one gridiron pick per result string, each on its own game."""
    games = Game.query.filter_by(week_id=week.id, pool="gridiron").all()
    assert len(results) <= len(games)
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


def _last_place(labels=None):
    awards = gridiron_awards(SEASON)
    return {
        (l["entry"].label, l["detail"]) for l in awards["last_place"]["leaders"]
    }


def test_more_losses_breaks_the_tie(app, make_week, make_entry):
    wk1 = make_week(1)
    wk2 = make_week(2)

    pusher = make_entry("pusher")      # 1-8-1
    loser = make_entry("loser")        # 1-9-0
    middle = make_entry("middle")      # 2-8-0

    _give(pusher, wk1, ["win", "loss", "loss", "loss", "loss"])
    _give(pusher, wk2, ["loss", "loss", "loss", "loss", "push"])

    _give(loser, wk1, ["win", "loss", "loss", "loss", "loss"])
    _give(loser, wk2, ["loss", "loss", "loss", "loss", "loss"])

    _give(middle, wk1, ["win", "win", "loss", "loss", "loss"])
    _give(middle, wk2, ["loss", "loss", "loss", "loss", "loss"])

    assert _last_place() == {("loser", "1-9-0")}


def test_still_shared_on_an_identical_record(app, make_week, make_entry):
    wk1 = make_week(1)

    a = make_entry("a")
    b = make_entry("b")
    _give(a, wk1, ["win", "loss", "loss", "loss", "loss"])
    _give(b, wk1, ["win", "loss", "loss", "loss", "loss"])

    assert {label for label, _ in _last_place()} == {"a", "b"}
