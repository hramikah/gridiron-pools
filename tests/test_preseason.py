"""Preseason weeks are a trial run.

Picks are made, graded and shown. What a preseason week may and may not do has
been settled twice:

- 2026-08-26: nothing about it reaches the season at all.
- 2026-08-29: its RESULTS do count -- preseason standings are shown.
- 2026-08-30: a Gridiron preseason week behaves like a regular one end to end.
  Empty slots are charged, and a week sat out is RECORDED as a miss -- which
  is what makes a first one forgiven (0-0-0, not 0-5), nameable in the
  standings' Penalties column, and worth the 8-pick makeup week after it.

What a preseason week still cannot do: eliminate a Drop Dead entry, spend a
Drop Dead team, auto-pick in the Loser Pool, or bench a Gridiron entry.

`Week.is_preseason` had said as much since it was added and was honoured
nowhere, so a preseason loss really did end a Drop Dead entry before the season
started. These lock the behaviour down.
"""

from datetime import timedelta

from models import PRESEASON_OFFSET, Entry, Game, GridironMiss, LoserPoolPoints, Pick, Team, Week, db
from helpers import now_eastern, short_week_label, week_label
from scoring import (
    counts_for_season,
    gridiron_first_miss_week,
    gridiron_makeup_week,
    gridiron_pick_limit,
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


def test_a_part_filled_preseason_week_is_charged_like_a_regular_one(app, make_week, make_entry, submit):
    """Empty slots cost a loss each in a preseason week too, as they always
    have everywhere except the season Won/Lost columns."""
    pre = _preseason_week(make_week)
    entry = make_entry("player")
    submit(entry, pre, 2, result="win")

    (_, _, wins, losses, _), = standings_gridiron(SEASON)

    assert wins == 2
    assert losses == 3, "3 unfilled preseason slots cost a loss each"


def test_playing_a_preseason_week_never_ranks_below_sitting_it_out(app, make_week, make_entry, submit):
    """The reason for the 2026-08-30 change: 0-5 must not rank behind 0-0."""
    pre = _preseason_week(make_week)
    played = make_entry("played")
    sat_out = make_entry("sat_out")
    submit(played, pre, 5, result="loss")

    rows = {r[1].id: (r[0], r[2], r[3]) for r in standings_gridiron(SEASON)}

    assert rows[played.id][1:] == (0, 5)
    assert rows[sat_out.id][1:] == (0, 5), "the no-show is charged the same 0-5"
    assert rows[played.id][0] == rows[sat_out.id][0], "so they tie, instead of the no-show ranking above"


def test_a_sat_out_preseason_week_is_a_forgiven_first_miss(app, make_week, make_entry):
    """A preseason week an entry sat out behaves exactly like a regular one.

    Rule 8 forgives a FIRST failure and pays for it with the makeup week, so
    the record does not move (0-0-0, not 0-5), the week is recorded so the
    standings' Penalties column can name it, and the 8-pick makeup follows.
    """
    pre = _preseason_week(make_week)
    regular = make_week(1, future=True)   # still open, so only the preseason week is due
    entry = make_entry("player")

    process_due_weeks(SEASON)

    assert GridironMiss.query.filter_by(entry_id=entry.id).count() == 1
    assert gridiron_first_miss_week(entry) == pre.number, "so Penalties can name it"
    assert entry.is_active is True, "a trial week can never bench anyone"

    # The sat-out week itself costs nothing -- no 0-5 -- and the makeup week is
    # still a month out, so nothing is charged yet either.
    (_, _, wins, losses, ties), = standings_gridiron(SEASON)
    assert (wins, losses, ties) == (0, 0, 0), "a first miss costs nothing on its own"

    # Once the league enters the makeup week, its 2-game penalty is already on
    # the record -- the entry starts that week 0-2-0.
    regular.pick_deadline = now_eastern() + timedelta(days=1)
    db.session.commit()
    (_, _, wins, losses, ties), = standings_gridiron(SEASON)
    assert (wins, losses, ties) == (0, 2, 0)

    assert gridiron_makeup_week(entry) == regular.number, "the makeup is the next week on the schedule"
    assert gridiron_pick_limit(entry, regular) == 8


def test_the_makeup_week_after_a_preseason_miss_is_week_one(app, make_week, make_entry):
    """Preseason weeks are numbered 101+, so first_miss + 1 would name a week
    105 that will never exist and the makeup allowance would go unusable."""
    pre = _preseason_week(make_week, number=4)   # 104
    week1 = make_week(1, future=True)
    entry = make_entry("player")

    process_due_weeks(SEASON)

    assert pre.number == 104
    assert gridiron_makeup_week(entry) == 1
    assert gridiron_pick_limit(entry, week1) == 8
    assert gridiron_pick_limit(entry, pre) == 5


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


def test_forgetting_a_preseason_week_records_the_gridiron_miss_only(app, make_week, make_entry):
    pre_g = _preseason_week(make_week)
    pre_d = _preseason_week(make_week, pool="dropdead")
    gridiron = make_entry("g")
    survivor = make_entry("d", pool="dropdead")

    settled = process_due_weeks(SEASON)

    assert set(w.id for w in settled) == {pre_g.id, pre_d.id}, "settled, so they stop showing as owed"
    assert GridironMiss.query.filter_by(entry_id=gridiron.id).count() == 1, "gridiron records the miss"
    assert survivor.is_active is True, "but a Drop Dead no-show must not eliminate"


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
