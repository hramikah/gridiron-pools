"""Preseason weeks are a trial run.

Picks are made, graded and shown -- but nothing about a preseason week reaches
the season: no standings points, no Drop Dead elimination, no team spent, no
penalty for forgetting. Commissioner's decision, 2026-08-26.

`Week.is_preseason` had said as much since it was added and was honoured
nowhere, so a preseason loss really did end a Drop Dead entry before the season
started. These lock the behaviour down.
"""

from models import PRESEASON_OFFSET, Entry, Game, GridironMiss, LoserPoolPoints, Pick, Team, Week, db
from helpers import short_week_label, week_label
from scoring import (
    counts_for_season,
    process_due_weeks,
    score_dropdead_pick,
    standings_gridiron,
    standings_loser,
)

SEASON = 2026


def _preseason_week(make_week, number=1, pool="gridiron"):
    return make_week(PRESEASON_OFFSET + number, pool=pool, is_preseason=True)


def _teams():
    home = Team(name="Homers", city="Home City")
    away = Team(name="Awayers", city="Away City")
    db.session.add_all([home, away])
    db.session.commit()
    return home, away


def test_counts_for_season(app, make_week):
    assert counts_for_season(make_week(1)) is True
    assert counts_for_season(_preseason_week(make_week)) is False
    assert counts_for_season(None) is False


def test_a_losing_preseason_pick_is_graded_but_does_not_eliminate(app, make_week, make_entry):
    week = _preseason_week(make_week, pool="dropdead")
    entry = make_entry("survivor", pool="dropdead")
    home, away = _teams()
    game = Game.query.filter_by(week_id=week.id).first()
    game.home_team_id, game.away_team_id = home.id, away.id
    game.home_score, game.away_score, game.is_final = 10, 24, True  # away won
    pick = Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=home.id)
    db.session.add(pick)
    db.session.commit()

    score_dropdead_pick(pick, game)

    assert pick.result == "loss", "the pick is still graded -- the player sees they were wrong"
    assert entry.is_active is True, "a preseason loss must not end the season before it starts"
    assert entry.eliminated_week is None


def test_a_losing_regular_season_pick_still_eliminates(app, make_week, make_entry):
    """The guard must be specific to preseason, not a blanket switch-off."""
    week = make_week(1, pool="dropdead")
    entry = make_entry("survivor", pool="dropdead")
    home, away = _teams()
    game = Game.query.filter_by(week_id=week.id).first()
    game.home_team_id, game.away_team_id = home.id, away.id
    game.home_score, game.away_score, game.is_final = 10, 24, True
    pick = Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=home.id)
    db.session.add(pick)
    db.session.commit()

    score_dropdead_pick(pick, game)

    assert entry.is_active is False
    assert entry.eliminated_week == 1


def test_a_preseason_pick_does_not_spend_the_team(app, make_week, make_entry):
    week = _preseason_week(make_week, pool="dropdead")
    entry = make_entry("survivor", pool="dropdead")
    home, _ = _teams()
    db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=home.id))
    db.session.commit()

    assert entry.used_team_ids() == set(), "everyone starts Week 1 with all 32 available"


def test_a_regular_pick_does_spend_the_team(app, make_week, make_entry):
    week = make_week(1, pool="dropdead")
    entry = make_entry("survivor", pool="dropdead")
    home, _ = _teams()
    db.session.add(Pick(entry_id=entry.id, week_id=week.id, pool="dropdead", team_id=home.id))
    db.session.commit()

    assert entry.used_team_ids() == {home.id}


def test_preseason_gridiron_results_do_show_in_the_standings(app, make_week, make_entry, submit):
    pre = _preseason_week(make_week)
    regular = make_week(1)
    entry = make_entry("player")
    submit(entry, pre, 5, result="win")
    submit(entry, regular, 5, result="loss")

    (_, _, wins, losses, _), = standings_gridiron(SEASON)

    assert wins == 5, "preseason wins now show in the record"
    assert losses == 5


def test_a_preseason_week_still_never_penalises(app, make_week, make_entry, submit):
    """Showing preseason results must not drag the penalties along with them:
    an entry that part-filled a preseason week is charged nothing for the
    empty slots, where a regular week would cost one loss per slot."""
    pre = _preseason_week(make_week)
    entry = make_entry("player")
    submit(entry, pre, 2, result="win")

    (_, _, wins, losses, _), = standings_gridiron(SEASON)

    assert wins == 2
    assert losses == 0, "3 unfilled preseason slots must cost nothing"


def test_preseason_loser_points_do_show(app, make_week, make_entry):
    pre = _preseason_week(make_week, pool="loser")
    entry = make_entry("player", pool="loser")
    home, _ = _teams()
    db.session.add(
        Pick(entry_id=entry.id, week_id=pre.id, pool="loser", team_id=home.id,
             result="win", points=41)
    )
    db.session.commit()

    (_, _, total), = standings_loser(SEASON)

    assert total == 41


def test_forgetting_a_preseason_week_costs_nothing(app, make_week, make_entry):
    pre_g = _preseason_week(make_week)
    pre_d = _preseason_week(make_week, pool="dropdead")
    gridiron = make_entry("g")
    survivor = make_entry("d", pool="dropdead")

    settled = process_due_weeks(SEASON)

    assert set(w.id for w in settled) == {pre_g.id, pre_d.id}, "settled, so they stop showing as owed"
    assert GridironMiss.query.filter_by(entry_id=gridiron.id).count() == 0
    assert survivor.is_active is True, "a preseason no-show must not eliminate"


def test_a_preseason_loser_week_settles_without_a_monday_night_game(app, make_week, make_entry):
    """A regular Loser week waits for its MNF game so the no-show auto-pick can
    be assigned. A preseason one has no auto-pick to assign, so waiting would
    leave it owed forever."""
    pre = _preseason_week(make_week, pool="loser")
    make_entry("player", pool="loser")

    process_due_weeks(SEASON)

    assert pre.missed_processed is True
    assert Pick.query.filter_by(week_id=pre.id).count() == 0, "no auto-pick in a trial week"


def test_week_labels_never_show_the_stored_number(app):
    assert week_label(PRESEASON_OFFSET + 1) == "Preseason Week 1"
    assert week_label(PRESEASON_OFFSET + 2) == "Preseason Week 2"
    assert week_label(5) == "Week 5"
    assert week_label(None) == ""
    assert short_week_label(PRESEASON_OFFSET + 2) == "Pre 2"
    assert short_week_label(5) == "Wk5"


def test_week_row_label(app, make_week):
    pre = _preseason_week(make_week, number=2)
    assert pre.number == 102, "still stored offset past the regular season"
    assert pre.display_number == 2
    assert pre.label == "Preseason Week 2"
