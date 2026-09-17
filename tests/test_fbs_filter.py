"""The publisher's FBS-only college filter.

The NCAAF odds feed prices FBS-vs-FCS games alongside the real slate, and the
house rule is FBS vs FBS only. These tests pin both halves of that: the games
that must not reach the board, and the report that makes a whitelist gap
visible instead of silently dropping a real game.
"""

from datetime import datetime, timedelta

import pytest

import publisher
from fbs import is_fbs, unrecognized
from models import Game, Setting, Week, db

SEASON_START = datetime(2026, 9, 10).date()  # a Thursday


def test_known_fbs_schools_pass():
    assert is_fbs("Clemson Tigers")
    assert is_fbs("Ohio State Buckeyes")
    assert is_fbs("Miami (OH) RedHawks")


def test_fcs_schools_are_rejected():
    assert not is_fbs("Mercer Bears")
    assert not is_fbs("Alabama A&M Bulldogs")
    assert not is_fbs("Furman Paladins")


def test_spelling_variants_still_match():
    # Accents, punctuation and the longhand school names books sometimes use.
    assert is_fbs("Hawai'i Rainbow Warriors")
    assert is_fbs("Southern California Trojans")
    assert is_fbs("North Carolina State Wolfpack")


def test_unrecognized_reports_only_the_offenders():
    assert unrecognized(["Clemson Tigers", "Mercer Bears"]) == ["Mercer Bears"]
    assert unrecognized(["Clemson Tigers", "Duke Blue Devils"]) == []


def _event(away, home, kickoff, spread=7.5, total=52.5):
    return {
        "away_team": away,
        "home_team": home,
        "commence_time": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bookmakers": [{
            "key": "draftkings",
            "markets": [
                {"key": "spreads", "outcomes": [
                    {"name": home, "point": -spread},
                    {"name": away, "point": spread},
                ]},
                {"key": "totals", "outcomes": [{"name": "Over", "point": total}]},
            ],
        }],
    }


@pytest.fixture
def stub_feed(app, monkeypatch):
    """publish_week against a fixed slate: no network, no API key spent."""
    with app.app_context():
        db.session.add(Setting(key="odds_api_key", value="test-key"))
        db.session.add(Setting(key="season_start_thursday", value=SEASON_START.isoformat()))
        db.session.commit()

    saturday = datetime(2026, 9, 12, 16, 0)  # inside Week 1's Thu-Wed window

    college = [
        _event("Duke Blue Devils", "Clemson Tigers", saturday),
        _event("Mercer Bears", "Alabama Crimson Tide", saturday),        # FBS v FCS
        _event("Furman Paladins", "Wofford Terriers", saturday),         # FCS v FCS
    ]
    nfl = [_event("Dallas Cowboys", "Philadelphia Eagles", datetime(2026, 9, 13, 13, 0))]

    def fake_fetch(sport, api_key, preseason=False):
        return nfl if sport == "nfl" else college

    monkeypatch.setattr(publisher, "fetch_odds", fake_fetch)
    return app


def test_only_fbs_v_fbs_college_games_are_published(stub_feed):
    summary = publisher.publish_week(stub_feed, reference=datetime(2026, 9, 10, 8, 0))

    with stub_feed.app_context():
        week = Week.query.filter_by(number=1, pool="gridiron").first()
        college_games = Game.query.filter_by(week_id=week.id, sport="college").all()
        names = sorted(g.home_team for g in college_games)

    assert names == ["Clemson Tigers"]
    assert summary["skipped_college"] == 2
    assert "Mercer Bears" in summary["non_fbs_names"]
    assert "Furman Paladins" in summary["non_fbs_names"]
    # The FBS side of a mismatch is recognized, so it must not be reported as
    # a gap in the whitelist -- only the FCS names are.
    assert "Alabama Crimson Tide" not in summary["non_fbs_names"]


def test_nfl_is_untouched_by_the_filter(stub_feed):
    publisher.publish_week(stub_feed, reference=datetime(2026, 9, 10, 8, 0))

    with stub_feed.app_context():
        for pool in ("gridiron", "dropdead", "loser"):
            week = Week.query.filter_by(number=1, pool=pool).first()
            nfl = Game.query.filter_by(week_id=week.id, sport="nfl").all()
            assert [g.home_team for g in nfl] == ["Philadelphia Eagles"], pool


def test_republish_does_not_resurrect_a_skipped_game(stub_feed):
    ref = datetime(2026, 9, 10, 8, 0)
    publisher.publish_week(stub_feed, reference=ref)
    summary = publisher.publish_week(stub_feed, reference=ref)

    with stub_feed.app_context():
        week = Week.query.filter_by(number=1, pool="gridiron").first()
        college_games = Game.query.filter_by(week_id=week.id, sport="college").all()

    assert len(college_games) == 1
    assert summary["created"] == 0
