"""Simulate a whole 18-week season on the local test copy.

Six players ('phone' plus five mocks), all entered in all three pools, with
10 NFL and 10 college games every week. Every player takes the maximum
buy-backs: their Drop Dead pick is forced to lose in each of weeks 1-4, and
each elimination is bought back (4 buy-backs, $120, the most the rules
allow). From week 5 the results are genuine coin flips, so the field thins
out the way a real season does.

Wipes existing play data first; user accounts, teams and Loser Pool point
values are kept. Local use only.
"""

import random
from datetime import datetime, timedelta

from app import app
from helpers import set_setting
from models import (Entry, Game, GridironMiss, LoserPoolPoints, Pick, Team,
                    User, Week, db)
from scoring import score_game

SEASON = 2026
ME = "phone"
MOCKS = ["dana", "marcus", "ray", "sam", "jo"]
POOLS = ("dropdead", "loser", "gridiron")

NFL_GAMES_PER_WEEK = 10
CFB_GAMES_PER_WEEK = 10
GRIDIRON_PICKS_PER_WEEK = 5
BUYBACK_WEEKS = 4          # weeks 1-4 allow a buy-back, per the printed rules

COLLEGE_TEAMS = [
    "Alabama Crimson Tide", "Georgia Bulldogs", "Ohio State Buckeyes",
    "Michigan Wolverines", "Texas Longhorns", "Oklahoma Sooners",
    "LSU Tigers", "Clemson Tigers", "Notre Dame Fighting Irish",
    "Penn State Nittany Lions", "Oregon Ducks", "Washington Huskies",
    "Florida State Seminoles", "Tennessee Volunteers", "USC Trojans",
    "Ole Miss Rebels", "Utah Utes", "Wisconsin Badgers",
    "Missouri Tigers", "Iowa Hawkeyes",
]


def draw_week_matchups(nfl_teams, rng):
    """Pick this week's matchups once. All three pools then get their own Game
    rows built from the same fixtures, so a result decided on one pool's copy
    can be mirrored onto the others."""
    teams = list(nfl_teams)
    rng.shuffle(teams)
    nfl = [(teams[i * 2], teams[i * 2 + 1]) for i in range(NFL_GAMES_PER_WEEK)]

    cfb_names = list(COLLEGE_TEAMS)
    rng.shuffle(cfb_names)
    cfb = [(cfb_names[i * 2], cfb_names[i * 2 + 1]) for i in range(CFB_GAMES_PER_WEEK)]

    lines = {}
    for away, home in nfl:
        lines[(f"{away.city} {away.name}", f"{home.city} {home.name}")] = (
            rng.choice([1.5, 2.5, 3.0, 4.5, 6.0, 7.5, 9.5]),
            rng.choice([38.5, 41.0, 44.5, 47.0, 49.5, 52.0]),
        )
    for away, home in cfb:
        lines[(away, home)] = (
            rng.choice([3.0, 6.5, 10.5, 14.0, 17.5]),
            rng.choice([45.5, 51.0, 55.5, 61.0, 66.5]),
        )
    return nfl, cfb, lines


def make_week_slate(week_row, nfl, cfb, lines):
    """10 NFL games (real Team rows, so Drop Dead and Loser can use them) plus,
    for Gridiron only, 10 college games -- there are no Team rows for college
    sides, which is exactly why the other two pools never see them."""
    games = []
    pool = week_row.pool

    for i, (away, home) in enumerate(nfl):
        away_name, home_name = f"{away.city} {away.name}", f"{home.city} {home.name}"
        spread, ou = lines[(away_name, home_name)]
        games.append(Game(
            week_id=week_row.id, pool=pool, sport="nfl",
            away_team=away_name, home_team=home_name,
            away_team_id=away.id, home_team_id=home.id,
            favorite="home", spread=spread, over_under=ou,
            kickoff=week_row.pick_deadline + timedelta(days=1),
            is_mnf=(i == NFL_GAMES_PER_WEEK - 1),
        ))

    if pool == "gridiron":
        for away, home in cfb:
            spread, ou = lines[(away, home)]
            games.append(Game(
                week_id=week_row.id, pool=pool, sport="college",
                away_team=away, home_team=home,
                favorite="home", spread=spread, over_under=ou,
                kickoff=week_row.pick_deadline + timedelta(days=1),
            ))

    db.session.add_all(games)
    db.session.commit()
    return games


def run():
    if "/root/" in app.config["SQLALCHEMY_DATABASE_URI"]:
        raise SystemExit("Refusing to run against the live database.")
    rng = random.Random(2026)

    with app.app_context():
        # --- clean slate (accounts, teams and points survive) ---------------
        for model in (Pick, GridironMiss, Game, Entry, Week):
            model.query.delete(synchronize_session=False)
        db.session.commit()

        nfl_teams = Team.query.all()
        if len(nfl_teams) < NFL_GAMES_PER_WEEK * 2:
            raise SystemExit("Not enough teams -- run seed.py first.")

        players = []
        for name in [ME] + MOCKS:
            u = User.query.filter(db.func.lower(User.username) == name).first()
            if not u:
                u = User(username=name, email=f"{name}@example.com")
                u.set_password("test1234")
                db.session.add(u)
                db.session.commit()
            players.append(u)

        entries = {}
        for u in players:
            entries[u.id] = {}
            for pool in POOLS:
                e = Entry(user_id=u.id, pool=pool, season_year=SEASON,
                          label="Entry 1", paid=True)
                db.session.add(e)
                db.session.commit()
                entries[u.id][pool] = e

        # Anchor the season so it reads as played: week 18 is the current week
        # with its deadline a couple of days out, and weeks 1-17 are behind us.
        # Dating it in the future instead leaves every week "still open", which
        # correctly hides every pick from the Weekly Picks report.
        today = datetime.now().date()
        week18_thursday = today - timedelta(days=(today.weekday() - 3) % 7)
        season_start = week18_thursday - timedelta(weeks=17)
        buybacks = {u.id: 0 for u in players}

        for number in range(1, 19):
            thursday = season_start + timedelta(weeks=number - 1)
            deadline = datetime.combine(thursday + timedelta(days=2), datetime.min.time()) + timedelta(hours=12)

            nfl, cfb, lines = draw_week_matchups(nfl_teams, rng)
            weeks, games = {}, {}
            for pool in POOLS:
                w = Week(season_year=SEASON, number=number, pool=pool,
                         pick_deadline=deadline,
                         buyback_open=(pool == "dropdead" and number <= BUYBACK_WEEKS))
                db.session.add(w)
                db.session.commit()
                weeks[pool] = w
                games[pool] = make_week_slate(w, nfl, cfb, lines)

            # --- picks ------------------------------------------------------
            dd_picks = {}
            # In the buy-back weeks every pick has to lose, so no two players
            # may sit on opposite sides of one game -- one of them would have
            # to win, and they'd miss a buy-back. Hand each player their own
            # game those weeks; afterwards it doesn't matter.
            claimed_games = set()
            for u in players:
                dd = entries[u.id]["dropdead"]
                if not dd.is_active:
                    continue
                used = dd.used_team_ids()
                options = []
                for g in games["dropdead"]:
                    if number <= BUYBACK_WEEKS and g.id in claimed_games:
                        continue
                    for t in (g.home_team_id, g.away_team_id):
                        if t and t not in used:
                            options.append((g.id, t))
                if not options:
                    continue
                game_id, team_id = rng.choice(options)
                claimed_games.add(game_id)
                db.session.add(Pick(entry_id=dd.id, week_id=weeks["dropdead"].id,
                                    pool="dropdead", team_id=team_id))
                dd_picks[u.id] = team_id

            # Loser and Gridiron run independently of Drop Dead: being knocked
            # out of one pool doesn't stop you playing the other two.
            for u in players:
                lp = entries[u.id]["loser"]
                g = rng.choice(games["loser"])
                db.session.add(Pick(entry_id=lp.id, week_id=weeks["loser"].id, pool="loser",
                                    team_id=rng.choice([g.home_team_id, g.away_team_id])))

                gi = entries[u.id]["gridiron"]
                slate = list(games["gridiron"])
                rng.shuffle(slate)
                for g in slate[:GRIDIRON_PICKS_PER_WEEK - 1]:
                    db.session.add(Pick(entry_id=gi.id, week_id=weeks["gridiron"].id, pool="gridiron",
                                        game_id=g.id, market="spread",
                                        side=rng.choice(["home", "away"])))
                db.session.add(Pick(entry_id=gi.id, week_id=weeks["gridiron"].id, pool="gridiron",
                                    game_id=slate[GRIDIRON_PICKS_PER_WEEK - 1].id, market="total",
                                    side=rng.choice(["over", "under"])))
            db.session.commit()

            # --- results ----------------------------------------------------
            # Weeks 1-4: every Drop Dead pick loses, so every player is forced
            # through the full four buy-backs. From week 5 it's a coin flip.
            losing_teams = set(dd_picks.values()) if number <= BUYBACK_WEEKS else set()
            outcomes = {}
            for g in games["dropdead"]:
                if g.home_team_id in losing_teams:
                    home_wins = False
                elif g.away_team_id in losing_teams:
                    home_wins = True
                else:
                    home_wins = rng.random() < 0.5
                outcomes[(g.away_team, g.home_team)] = (17, 24) if home_wins else (24, 17)

            for pool in POOLS:
                for g in games[pool]:
                    if g.sport == "college":
                        g.away_score, g.home_score = rng.randint(10, 45), rng.randint(10, 45)
                    else:
                        a, h = outcomes[(g.away_team, g.home_team)]
                        g.away_score, g.home_score = a, h
                    g.is_final = True
                weeks[pool].missed_processed = True
            db.session.commit()

            for pool in POOLS:
                for g in games[pool]:
                    score_game(g)

            # --- buy-backs (weeks 1-4 only), exactly what the route does ----
            if number <= BUYBACK_WEEKS:
                for u in players:
                    dd = entries[u.id]["dropdead"]
                    if not dd.is_active and dd.eliminated_week == number:
                        dd.is_active = True
                        dd.buy_backs_used += 1
                        dd.buyback_week = number
                        buybacks[u.id] += 1
                db.session.commit()

        set_setting("active_week", "18")
        set_setting("season_start_thursday", str(season_start))

        # --- report -----------------------------------------------------------
        print(f"18 weeks played. {Game.query.count()} games, {Pick.query.count()} picks.\n")
        print(f"{'player':9} {'drop dead':28} {'buy-backs':10} {'loser pts':10} gridiron W-L-P")
        for u in players:
            dd = entries[u.id]["dropdead"]
            status = "alive" if dd.is_active else f"out in week {dd.eliminated_week}"
            lp_pts = sum(p.points or 0 for p in Pick.query.filter_by(entry_id=entries[u.id]["loser"].id))
            gp = Pick.query.filter_by(entry_id=entries[u.id]["gridiron"].id).all()
            w = sum(1 for p in gp if p.result == "win")
            l = sum(1 for p in gp if p.result == "loss")
            ps = sum(1 for p in gp if p.result == "push")
            print(f"{u.username:9} {status:28} {buybacks[u.id]} ($" + f"{buybacks[u.id]*30})".ljust(8)
                  + f" {lp_pts:>8.0f}   {w}-{l}-{ps}")


if __name__ == "__main__":
    run()
