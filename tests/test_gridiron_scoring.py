"""Spread and over/under math for a single Gridiron pick.

Rule 4: picks are against the provided line. A pick that lands exactly on the
number is a push -- neither a win nor a loss -- and shows in the standings as
a tie. These are the calculations a wrong answer in costs somebody money, so
they're pinned here explicitly rather than inferred from a season sim.
"""

import pytest

from models import Entry, Game, Pick, db
from scoring import score_gridiron_pick

from conftest import SEASON


@pytest.fixture
def game(app, make_week):
    week = make_week(1, games=1)
    return Game.query.filter_by(week_id=week.id).first()


def played(game, home_score, away_score, favorite="home", spread=3.0, over_under=None):
    game.favorite = favorite
    game.spread = spread
    game.over_under = over_under
    game.home_score = home_score
    game.away_score = away_score
    game.is_final = True
    db.session.commit()
    return game


def judge(game, market, side):
    # result starts at "pending", the way a saved pick sits in the DB between
    # submission and the game going final.
    pick = Pick(
        week_id=game.week_id,
        pool="gridiron",
        game_id=game.id,
        market=market,
        side=side,
        result="pending",
    )
    score_gridiron_pick(pick, game)
    return pick.result


# --------------------------------------------------------------------------
# Spread
# --------------------------------------------------------------------------


def test_home_favorite_covers(game):
    played(game, 27, 20, favorite="home", spread=3.0)  # wins by 7, laying 3
    assert judge(game, "spread", "home") == "win"
    assert judge(game, "spread", "away") == "loss"


def test_home_favorite_wins_but_fails_to_cover(game):
    played(game, 23, 21, favorite="home", spread=3.0)  # wins by 2, laying 3
    assert judge(game, "spread", "home") == "loss"
    assert judge(game, "spread", "away") == "win"


def test_home_favorite_pushes_exactly_on_the_number(game):
    played(game, 24, 21, favorite="home", spread=3.0)  # wins by exactly 3
    assert judge(game, "spread", "home") == "push"
    assert judge(game, "spread", "away") == "push"


def test_away_favorite_covers(game):
    played(game, 14, 24, favorite="away", spread=6.5)  # away wins by 10, laying 6.5
    assert judge(game, "spread", "away") == "win"
    assert judge(game, "spread", "home") == "loss"


def test_away_favorite_pushes(game):
    played(game, 17, 24, favorite="away", spread=7.0)  # away wins by exactly 7
    assert judge(game, "spread", "away") == "push"
    assert judge(game, "spread", "home") == "push"


def test_pickem_has_no_favorite(game):
    played(game, 21, 17, favorite=None, spread=None)
    assert judge(game, "spread", "home") == "win"
    assert judge(game, "spread", "away") == "loss"


def test_pickem_tie_is_a_push(game):
    played(game, 20, 20, favorite=None, spread=None)
    assert judge(game, "spread", "home") == "push"
    assert judge(game, "spread", "away") == "push"


def test_half_point_spread_can_never_push(game):
    played(game, 24, 21, favorite="home", spread=3.5)
    assert judge(game, "spread", "home") == "loss"
    assert judge(game, "spread", "away") == "win"


# --------------------------------------------------------------------------
# Over / under
# --------------------------------------------------------------------------


def test_over_hits(game):
    played(game, 28, 24, over_under=44.5)  # 52 total
    assert judge(game, "total", "over") == "win"
    assert judge(game, "total", "under") == "loss"


def test_under_hits(game):
    played(game, 10, 6, over_under=44.5)  # 16 total
    assert judge(game, "total", "under") == "win"
    assert judge(game, "total", "over") == "loss"


def test_total_on_the_number_is_a_push(game):
    played(game, 24, 20, over_under=44.0)  # 44 total, exactly
    assert judge(game, "total", "over") == "push"
    assert judge(game, "total", "under") == "push"


def test_total_with_no_line_posted_stays_pending(game):
    played(game, 24, 20, over_under=None)
    assert judge(game, "total", "over") == "pending"


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def test_unfinished_game_scores_nothing(game):
    played(game, 24, 20, favorite="home", spread=3.0)
    game.is_final = False
    db.session.commit()
    assert judge(game, "spread", "home") == "pending"


def test_final_with_missing_score_scores_nothing(game):
    game.is_final = True
    game.home_score = None
    game.away_score = 20
    db.session.commit()
    assert judge(game, "spread", "home") == "pending"
