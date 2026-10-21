"""Weekly Wrapped -- the Data page.

A Spotify-Wrapped style recap of one week across all three pools, built
entirely from picks and results that are already in the database.

READ-ONLY BY DESIGN. Nothing in this module adds, changes or deletes a row:
it only runs SELECTs and does arithmetic on what comes back. If a stat here
is ever wrong, the worst case is a wrong number on the Data page -- never a
changed pick, record, payment or elimination.

When a week publishes
---------------------
A week's Wrapped goes public once the week is over, meaning for every pool
that has that week number:

  * the pick deadline has passed (so no pick is still open), and
  * every game posted for that week has a final score.

That is normally the Tuesday morning after Monday Night Football, whenever
the two-hourly score job marks the last game final. Nobody has to press
anything. If one game never gets a final score (a postponement), the week
publishes anyway WRAPPED_FALLBACK_DAYS after its deadline, so a single stuck
game can't hold the recap back for good.

Admins can preview a week as soon as its deadline has passed -- they can
already see every pick in the Pool Manager, so nothing leaks.
"""

from collections import Counter, defaultdict
from datetime import timedelta
import threading
import time

from helpers import now_eastern, week_is_complete
from models import (
    PRESEASON_OFFSET,
    BuyBack,
    Entry,
    Game,
    Pick,
    Team,
    User,
    Week,
)
from scoring import (
    dropdead_status_through_week,
    gridiron_record_through_week,
    gridiron_week_records,
    loser_totals_through_week,
)
from team_colors import PRIMARY, team_style

# A week with a game that never goes final still publishes this long after
# its pick deadline.
WRAPPED_FALLBACK_DAYS = 7

# How many usernames a card spells out before it says "+N more".
NAME_LIMIT = 4

POOL_ORDER = ("gridiron", "dropdead", "loser")


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

def _rows_for_number(season_year, number):
    return Week.query.filter_by(season_year=season_year, number=number).all()


def week_publish_state(season_year, number):
    """('published' | 'preview' | 'pending' | None, the Week rows).

    published -- over in every pool; everyone can see it.
    preview   -- every deadline has passed but a game is still going;
                 admins only.
    pending   -- picks still open somewhere.
    None      -- no such week.
    """
    rows = _rows_for_number(season_year, number)
    if not rows:
        return None, rows
    now = now_eastern()
    if any(now <= w.pick_deadline for w in rows):
        return "pending", rows
    with_games = [w for w in rows if Game.query.filter_by(week_id=w.id).first()]
    if not with_games:
        return "pending", rows
    if all(week_is_complete(w) for w in with_games):
        return "published", rows
    latest = max(w.pick_deadline for w in rows)
    if now >= latest + timedelta(days=WRAPPED_FALLBACK_DAYS):
        return "published", rows
    return "preview", rows


def _week_order(number):
    # Preseason (101+) sorts ahead of the regular season.
    return (0, number) if number > PRESEASON_OFFSET else (1, number)


def week_menu(season_year, include_preview=False):
    """[(number, label, state)] for every week that can be shown, oldest first."""
    numbers = sorted(
        {w.number for w in Week.query.filter_by(season_year=season_year).all()},
        key=_week_order,
    )
    out = []
    for n in numbers:
        state, rows = week_publish_state(season_year, n)
        if state == "published" or (include_preview and state == "preview"):
            out.append((n, rows[0].label, state))
    return out


def next_pending_week(season_year):
    """The first week not yet published, for the 'next Wrapped drops...' line."""
    numbers = sorted(
        {w.number for w in Week.query.filter_by(season_year=season_year).all()},
        key=_week_order,
    )
    for n in numbers:
        state, rows = week_publish_state(season_year, n)
        if state != "published":
            return rows[0]
    return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _nick(full_name):
    """'Kansas City Chiefs' -> 'Chiefs'; college names come back whole."""
    if not full_name:
        return ""
    last = full_name.split()[-1]
    return last if last in PRIMARY else full_name


def _chip(full_name):
    """A team's display name plus its brand colours (None for college)."""
    style = team_style(full_name) if full_name else None
    return {
        "name": _nick(full_name),
        "fill": style["fill"] if style else None,
        "ink": style["ink"] if style else None,
    }


def _plain_pick(p, teams):
    """A Drop Dead / Loser pick as plain data. Everything league_week returns
    is cached across requests, and a database object kept past the request
    that loaded it can no longer load anything -- the cause of the 500s on
    the first live deploy. So nothing cached may be a database object."""
    return {
        "team_id": p.team_id,
        "chip": _team_chip(teams.get(p.team_id)),
        "result": p.result,
        "points": int(p.points or 0),
    }


def _team_chip(team):
    if team is None:
        return {"name": "?", "fill": None, "ink": None}
    full = f"{team.city or ''} {team.name}".strip()
    chip = _chip(full)
    chip["name"] = team.name
    return chip


def _names(usernames, limit=NAME_LIMIT):
    """{'shown': [...], 'more': n} with names in a stable, case-blind order."""
    names = sorted(set(usernames), key=lambda s: (s or "").lower())
    return {"shown": names[:limit], "more": max(0, len(names) - limit), "count": len(names)}


def _pct(part, whole):
    return round(100 * part / whole) if whole else 0


def _rec(counter):
    w, l, p = counter["win"], counter["loss"], counter["push"]
    graded = w + l
    return {"w": w, "l": l, "p": p, "pct": _pct(w, graded), "n": w + l + p}


def _gridiron_selection(pick, game):
    """What a Gridiron pick was, as a card shows it."""
    if pick.market == "spread":
        team_full = game.home_team if pick.side == "home" else game.away_team
        if not game.favorite or not game.spread:
            line = "PK"
        elif game.favorite == pick.side:
            line = f"-{game.spread:g}"
        else:
            line = f"+{game.spread:g}"
        chip = _chip(team_full)
        return {
            "kind": "spread",
            "title": f"{chip['name']} {line}",
            "chip": chip,
            "line": line,
            "matchup": f"{_nick(game.away_team)} @ {_nick(game.home_team)}",
        }
    total = f"{game.over_under:g}" if game.over_under is not None else "?"
    return {
        "kind": "total",
        "title": f"{pick.side.capitalize()} {total}",
        "chip": None,
        "line": total,
        "matchup": f"{_nick(game.away_team)} @ {_nick(game.home_team)}",
    }


def _score_line(game):
    if not game or not game.is_final or game.home_score is None:
        return None
    return (f"{_nick(game.away_team)} {game.away_score}, "
            f"{_nick(game.home_team)} {game.home_score}")


def _ranked(items, key):
    """[(place, item)] with standard competition ranking (1, 1, 3)."""
    out, prev, place = [], object(), 0
    for i, item in enumerate(items, start=1):
        k = key(item)
        if k != prev:
            place, prev = i, k
        out.append((place, item))
    return out


def _team_game(week_id, team_id):
    return (
        Game.query.filter_by(week_id=week_id)
        .filter((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
        .first()
    )


# ---------------------------------------------------------------------------
# Gridiron
# ---------------------------------------------------------------------------

def _gridiron(season_year, week):
    picks = Pick.query.filter_by(pool="gridiron", week_id=week.id).all()
    if not picks:
        return None
    entries = {e.id: e for e in Entry.query.filter_by(pool="gridiron", season_year=season_year)}
    users = {u.id: u.username for u in User.query.all()}
    games = {g.id: g for g in Game.query.filter_by(week_id=week.id)}

    def uname(entry_id):
        e = entries.get(entry_id)
        return users.get(e.user_id, "?") if e else "?"

    overall = Counter()
    by_sel = {}
    fav, dog, over, under = Counter(), Counter(), Counter(), Counter()
    nfl = college = 0
    game_action = Counter()
    for p in picks:
        g = games.get(p.game_id)
        if g is None:
            continue
        overall[p.result] += 1
        game_action[g.id] += 1
        if g.sport == "college":
            college += 1
        else:
            nfl += 1
        key = (g.id, p.market, p.side)
        sel = by_sel.get(key)
        if sel is None:
            sel = by_sel[key] = {
                **_gridiron_selection(p, g),
                "game_id": g.id,
                "result": p.result,
                "score": _score_line(g),
                "users": [],
            }
        sel["users"].append(uname(p.entry_id))
        if p.market == "spread" and g.favorite and g.spread:
            (fav if p.side == g.favorite else dog)[p.result] += 1
        elif p.market == "total":
            (over if p.side == "over" else under)[p.result] += 1

    sels = list(by_sel.values())
    for s in sels:
        s["count"] = len(s["users"])
        s["names"] = _names(s["users"])
        s["key"] = f"{s['game_id']}:{s['kind']}:{s['title']}"
    by_count = sorted(sels, key=lambda s: (-s["count"], s["title"]))

    popular = by_count[0] if by_count else None
    busts = [s for s in by_count if s["result"] == "loss" and s["count"] >= 2]
    bust = busts[0] if busts and busts[0] is not popular else (busts[1] if len(busts) > 1 else None)
    winners = sorted(
        (s for s in sels if s["result"] == "win"),
        key=lambda s: (s["count"], s["title"]),
    )
    # Least-picked side that won. "Lone Wolf" when three or fewer took it,
    # otherwise "the road less traveled".
    lone_wolf = winners[0] if winners and winners[0] is not popular else None
    if lone_wolf:
        lone_wolf = {**lone_wolf, "solo": lone_wolf["count"] <= 3}

    # Biggest upset: an underdog winning outright by the most. If no dog won
    # outright, the biggest cover against the number instead.
    upset = None
    best_margin = 0
    cover = None
    for g in games.values():
        if not g.is_final or g.home_score is None or not g.favorite or not g.spread:
            continue
        dog_side = "away" if g.favorite == "home" else "home"
        dog_pts = g.home_score if dog_side == "home" else g.away_score
        fav_pts = g.away_score if dog_side == "home" else g.home_score
        margin = dog_pts - fav_pts
        sel = by_sel.get((g.id, "spread", dog_side))
        if margin > best_margin:
            best_margin = margin
            upset = {"kind": "upset", "game": g, "side": dog_side, "margin": margin, "sel": sel}
        ats = (g.home_score - g.away_score) + (g.spread if g.favorite == "away" else -g.spread)
        side = "home" if ats > 0 else "away"
        if ats and (cover is None or abs(ats) > cover["margin"]):
            cover = {"kind": "cover", "game": g, "side": side, "margin": abs(ats),
                     "sel": by_sel.get((g.id, "spread", side))}
    shock = upset or cover
    if shock:
        g = shock["game"]
        team_full = g.home_team if shock["side"] == "home" else g.away_team
        other_full = g.away_team if shock["side"] == "home" else g.home_team
        line = ("+" if g.favorite != shock["side"] else "-") + f"{g.spread:g}"
        sel = shock["sel"]
        other = by_sel.get((g.id, "spread", "away" if shock["side"] == "home" else "home"))
        shock = {
            "kind": shock["kind"],
            "chip": _chip(team_full),
            "line": line,
            "opponent": _nick(other_full),
            "margin": shock["margin"] if shock["kind"] == "upset" else round(shock["margin"], 1),
            "score": _score_line(g),
            "count": sel["count"] if sel else 0,
            "names": sel["names"] if sel else _names([]),
            "against": other["count"] if other else 0,
        }

    # Game with the most action, and how the money split on it.
    hot = None
    if game_action:
        gid, n = game_action.most_common(1)[0]
        g = games[gid]
        split = sorted(
            (s for s in sels if s["game_id"] == gid), key=lambda s: -s["count"]
        )
        hot = {
            "matchup": f"{_nick(g.away_team)} @ {_nick(g.home_team)}",
            "count": n,
            "score": _score_line(g),
            "split": [{"title": s["title"], "count": s["count"], "result": s["result"],
                       "pct": _pct(s["count"], n)} for s in split],
        }

    # Records for the week, per entry (includes empty slots and penalties,
    # exactly as the standings count them).
    records = gridiron_week_records(season_year, week.number)
    rows = []
    for eid, (w, l, t) in records.items():
        if eid in entries:
            rows.append({"entry_id": eid, "user": uname(eid), "w": w, "l": l, "t": t})
    rows.sort(key=lambda r: (-r["w"], r["l"], -r["t"], r["user"].lower()))
    ranked = _ranked(rows, key=lambda r: (r["w"], r["l"], r["t"]))
    week_place = {r["entry_id"]: place for place, r in ranked}
    best = None
    if rows:
        top = rows[0]
        leaders = [r["user"] for r in rows if (r["w"], r["l"], r["t"]) == (top["w"], top["l"], top["t"])]
        best = {"w": top["w"], "l": top["l"], "t": top["t"], "names": _names(leaders, 6)}
    perfect = _names([r["user"] for r in rows if r["l"] == 0 and r["w"] >= 5], 6)
    goose = _names([r["user"] for r in rows if r["w"] == 0 and r["l"] >= 5], 6)

    season = _gridiron_season(season_year, week.number)

    return {
        "week_id": week.id,
        "players": len({p.entry_id for p in picks}),
        "pick_count": len(picks),
        "overall": _rec(overall),
        "nfl": nfl,
        "college": college,
        "nfl_pct": _pct(nfl, nfl + college),
        "popular": popular,
        "bust": bust,
        "lone_wolf": lone_wolf,
        "shock": shock,
        "hot": hot,
        "fav": _rec(fav),
        "dog": _rec(dog),
        "fav_share": _pct(sum(fav.values()), sum(fav.values()) + sum(dog.values())),
        "over": _rec(over),
        "under": _rec(under),
        "over_share": _pct(sum(over.values()), sum(over.values()) + sum(under.values())),
        "best": best,
        "perfect": perfect,
        "goose": goose,
        "field": len(rows),
        "movers": season["movers"],
        "leaders": season["leaders"],
        # For the personal card.
        "_sel_lookup": by_sel,
        "_week_place": week_place,
        "_records": {r["entry_id"]: r for r in rows},
    }


def _previous_number(season_year, pool, number):
    numbers = sorted(
        {w.number for w in Week.query.filter_by(season_year=season_year, pool=pool)},
        key=_week_order,
    )
    if number not in numbers:
        return None
    i = numbers.index(number)
    return numbers[i - 1] if i > 0 else None


def _mover_summary(before, after, username_of, label):
    """Biggest climb and fall between two {entry_id: place} maps."""
    deltas = [
        (before[eid] - after[eid], eid) for eid in after if eid in before
    ]
    if not deltas:
        return None
    up = max(deltas, key=lambda d: (d[0], -after[d[1]]))
    down = min(deltas, key=lambda d: (d[0], after[d[1]]))
    out = {}
    if up[0] > 0:
        out["up"] = {"user": username_of(up[1]), "by": up[0],
                     "from": before[up[1]], "to": after[up[1]], "label": label(up[1])}
    if down[0] < 0:
        out["down"] = {"user": username_of(down[1]), "by": -down[0],
                       "from": before[down[1]], "to": after[down[1]], "label": label(down[1])}
    return out or None


def _leaderboard(ranked, label, limit=6, places=3):
    """Top `places` places of a ranked [(place, row)] list, at most `limit`
    rows shown; the rest of the tied crowd is reported as a count."""
    top = [(pl, r) for pl, r in ranked if pl <= places]
    shown = [{"place": pl, "user": r[0].user.username, "label": label(r)} for pl, r in top[:limit]]
    return {"rows": shown, "more": max(0, len(top) - limit)} if shown else None


def _gridiron_season(season_year, number):
    """Season standings as they stood after this week: the leaderboard, and
    who moved most compared with the week before."""
    label = lambda r: f"{r[1]}-{r[2]}" + (f"-{r[3]}" if r[3] else "")

    def places(n):
        rows = gridiron_record_through_week(season_year, n)
        ranked = _ranked(rows, key=lambda r: (r[1], r[2], r[3]))
        return {r[0].id: place for place, r in ranked}, {r[0].id: r for r in rows}, ranked

    after, rows_after, ranked = places(number)
    out = {"leaders": _leaderboard(ranked, label), "movers": None}
    prev = _previous_number(season_year, "gridiron", number)
    if prev is not None:
        before, _r, _k = places(prev)
        out["movers"] = _mover_summary(
            before, after,
            lambda eid: rows_after[eid][0].user.username,
            lambda eid: label(rows_after[eid]),
        )
    return out


# ---------------------------------------------------------------------------
# Drop Dead
# ---------------------------------------------------------------------------

def _dropdead(season_year, week):
    picks = Pick.query.filter_by(pool="dropdead", week_id=week.id).all()
    entries = {e.id: e for e in Entry.query.filter_by(pool="dropdead", season_year=season_year)}
    if not picks and not entries:
        return None
    users = {u.id: u.username for u in User.query.all()}
    teams = {t.id: t for t in Team.query.all()}

    def uname(entry_id):
        e = entries.get(entry_id)
        return users.get(e.user_id, "?") if e else "?"

    picked_ids = {p.entry_id for p in picks}
    # Preseason never eliminates, so it has no no-shows to count.
    no_shows = [] if week.is_preseason else [
        e for e in entries.values()
        if e.eliminated_week == week.number and e.id not in picked_ids
    ]

    by_team = defaultdict(lambda: {"users": [], "result": "pending"})
    for p in picks:
        slot = by_team[p.team_id]
        slot["users"].append(uname(p.entry_id))
        slot["result"] = p.result
    teams_list = []
    for tid, slot in by_team.items():
        game = _team_game(week.id, tid)
        teams_list.append({
            "chip": _team_chip(teams.get(tid)),
            "count": len(slot["users"]),
            "names": _names(slot["users"]),
            "result": slot["result"],
            "score": _score_line(game),
        })
    teams_list.sort(key=lambda t: (-t["count"], t["chip"]["name"]))

    survived = sum(1 for p in picks if p.result == "win")
    fell = sum(1 for p in picks if p.result == "loss")
    in_play = len(picks) + len(no_shows)
    losers = [t for t in teams_list if t["result"] == "loss"]
    winners = sorted((t for t in teams_list if t["result"] == "win"),
                     key=lambda t: (t["count"], t["chip"]["name"]))

    status = dropdead_status_through_week(season_year, week.number)
    standing = sum(1 for r in status if r["is_active"])

    # What the survivors have already spent: teams used so far this season by
    # the entries still standing after this week.
    burned = Counter()
    if not week.is_preseason:
        for r in status:
            if not r["is_active"]:
                continue
            used = {
                p.team_id for p in r["entry"].picks
                if p.team_id and p.week and not p.week.is_preseason
                and p.week.number <= week.number
            }
            burned.update(used)
    burned_list = [
        {"chip": _team_chip(teams.get(tid)), "count": n, "pct": _pct(n, standing)}
        for tid, n in sorted(burned.items(), key=lambda kv: (-kv[1], teams[kv[0]].name if kv[0] in teams else ""))[:5]
    ]
    untouched = sorted(
        (t for tid, t in teams.items() if tid not in burned), key=lambda t: t.name
    ) if standing else []
    buybacks = BuyBack.query.join(Entry).filter(
        Entry.season_year == season_year, BuyBack.week_number == week.number
    ).count()

    return {
        "week_id": week.id,
        "in_play": in_play,
        "survived": survived,
        "fell": fell,
        "no_shows": _names([uname(e.id) for e in no_shows]),
        "survival_pct": _pct(survived, in_play),
        "standing": standing,
        "field": len(entries),
        "popular": teams_list[0] if teams_list else None,
        "graveyard": losers[0] if losers else None,
        "gutsy": winners[0] if winners and len(winners) > 1 else None,
        "teams_used": len(teams_list),
        "burned": burned_list,
        "untouched": len(untouched),
        "untouched_chips": [_team_chip(t) for t in untouched[:6]],
        "buybacks": buybacks,
        # Under the no-tie rule a week that knocks out everyone left revives them.
        "wipeout": bool(picks) and survived == 0 and not week.is_preseason,
        "preseason": week.is_preseason,
        "_pick_by_entry": {p.entry_id: _plain_pick(p, teams) for p in picks},
        "_team_counts": {tid: len(s["users"]) for tid, s in by_team.items()},
    }


# ---------------------------------------------------------------------------
# Loser Pool
# ---------------------------------------------------------------------------

def _loser(season_year, week):
    picks = Pick.query.filter_by(pool="loser", week_id=week.id).all()
    if not picks:
        return None
    entries = {e.id: e for e in Entry.query.filter_by(pool="loser", season_year=season_year)}
    users = {u.id: u.username for u in User.query.all()}
    teams = {t.id: t for t in Team.query.all()}

    def uname(entry_id):
        e = entries.get(entry_id)
        return users.get(e.user_id, "?") if e else "?"

    by_team = defaultdict(lambda: {"users": [], "result": "pending", "points": 0})
    for p in picks:
        slot = by_team[p.team_id]
        slot["users"].append(uname(p.entry_id))
        slot["result"] = p.result
        slot["points"] = p.points or 0
    teams_list = []
    for tid, slot in by_team.items():
        teams_list.append({
            "chip": _team_chip(teams.get(tid)),
            "count": len(slot["users"]),
            "names": _names(slot["users"]),
            "result": slot["result"],
            "points": int(slot["points"]),
            "score": _score_line(_team_game(week.id, tid)),
        })
    teams_list.sort(key=lambda t: (-t["count"], t["chip"]["name"]))

    correct = sum(1 for p in picks if p.result == "win")
    net = sum(p.points or 0 for p in picks)
    hits = [t for t in teams_list if t["result"] == "win"]
    misses = [t for t in teams_list if t["result"] == "loss"]
    jackpot = max(hits, key=lambda t: (t["points"], -t["count"])) if hits else None
    ouch = min(misses, key=lambda t: (t["points"], -t["count"])) if misses else None

    pts = sorted(((p.points or 0, p.entry_id) for p in picks), reverse=True)
    week_place = {eid: place for place, (_, eid) in _ranked(pts, key=lambda x: x[0])}

    return {
        "week_id": week.id,
        "players": len(picks),
        "correct": correct,
        "correct_pct": _pct(correct, len(picks)),
        "net": int(net),
        "popular": teams_list[0] if teams_list else None,
        "jackpot": jackpot,
        "ouch": ouch,
        "board": teams_list[:5],
        "avg": round(net / len(picks), 1) if picks else 0,
        **_loser_season(season_year, week.number),
        "_pick_by_entry": {p.entry_id: _plain_pick(p, teams) for p in picks},
        "_team_counts": {tid: len(s["users"]) for tid, s in by_team.items()},
        "_week_place": week_place,
    }


def _loser_season(season_year, number):
    label = lambda r: f"{int(r[1])} pts"

    def places(n):
        rows = loser_totals_through_week(season_year, n)
        ranked = _ranked(rows, key=lambda r: r[1])
        return {r[0].id: place for place, r in ranked}, {r[0].id: r for r in rows}, ranked

    after, rows_after, ranked = places(number)
    out = {"leaders": _leaderboard(ranked, label), "movers": None}
    prev = _previous_number(season_year, "loser", number)
    if prev is not None:
        before, _r, _k = places(prev)
        out["movers"] = _mover_summary(
            before, after,
            lambda eid: rows_after[eid][0].user.username,
            lambda eid: label(rows_after[eid]),
        )
    return out


# ---------------------------------------------------------------------------
# Across the pools
# ---------------------------------------------------------------------------

def _hot_team(weeks_by_pool):
    """The NFL team that showed up most across all three pools."""
    tally = defaultdict(lambda: {"gridiron": 0, "dropdead": 0, "loser": 0})
    gw = weeks_by_pool.get("gridiron")
    if gw:
        for p in Pick.query.filter_by(pool="gridiron", week_id=gw.id, market="spread").all():
            g = p.game
            if g is None or g.sport == "college":
                continue
            tid = g.home_team_id if p.side == "home" else g.away_team_id
            if tid:
                tally[tid]["gridiron"] += 1
    for pool in ("dropdead", "loser"):
        w = weeks_by_pool.get(pool)
        if not w:
            continue
        for p in Pick.query.filter_by(pool=pool, week_id=w.id).all():
            if p.team_id:
                tally[p.team_id][pool] += 1
    if not tally:
        return None
    tid, counts = max(tally.items(), key=lambda kv: sum(kv[1].values()))
    return {"chip": _team_chip(Team.query.filter_by(id=tid).first()), "total": sum(counts.values()), **counts}


# ---------------------------------------------------------------------------
# The whole week, cached
# ---------------------------------------------------------------------------

_cache = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 600


def league_week(season_year, number, use_cache=True):
    """Every league-wide stat for one week. Cached briefly: a published week
    hardly changes, and this walks every pick in all three pools."""
    key = (season_year, number)
    if use_cache:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]

    rows = _rows_for_number(season_year, number)
    weeks_by_pool = {w.pool: w for w in rows}
    data = {
        "number": number,
        "label": rows[0].label if rows else f"Week {number}",
        "gridiron": _gridiron(season_year, weeks_by_pool["gridiron"]) if "gridiron" in weeks_by_pool else None,
        "dropdead": _dropdead(season_year, weeks_by_pool["dropdead"]) if "dropdead" in weeks_by_pool else None,
        "loser": _loser(season_year, weeks_by_pool["loser"]) if "loser" in weeks_by_pool else None,
        "hot_team": _hot_team(weeks_by_pool),
    }
    week_ids = [w.id for w in rows]
    picks = Pick.query.filter(Pick.week_id.in_(week_ids)).all() if week_ids else []
    entry_ids = {p.entry_id for p in picks}
    user_ids = {e.user_id for e in Entry.query.filter(Entry.id.in_(entry_ids)).all()} if entry_ids else set()
    game_keys = {
        (g.sport, g.away_team, g.home_team)
        for g in Game.query.filter(Game.week_id.in_(week_ids)).all()
    } if week_ids else set()
    data["totals"] = {"picks": len(picks), "players": len(user_ids), "games": len(game_keys)}

    with _cache_lock:
        _cache[key] = (time.monotonic(), data)
    return data


def your_week(season_year, league, user):
    """The signed-in account's own week, built on top of the league numbers."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    mine = Entry.query.filter_by(user_id=user.id, season_year=season_year).all()
    out = {}

    gd = league.get("gridiron")
    for e in (x for x in mine if x.pool == "gridiron"):
        if not gd:
            break
        rec = gd["_records"].get(e.id)
        my_picks = Pick.query.filter_by(entry_id=e.id, week_id=gd["week_id"]).all()
        if not rec and not my_picks:
            continue
        items = []
        for p in my_picks:
            g = p.game
            if g is None:
                continue
            sel = gd["_sel_lookup"].get((g.id, p.market, p.side))
            count = sel["count"] if sel else 1
            items.append({
                **_gridiron_selection(p, g),
                "result": p.result,
                "count": count,
                "pct": _pct(count, gd["players"]),
            })
        items.sort(key=lambda i: -i["count"])
        contrarian = min(items, key=lambda i: i["count"]) if items else None
        out["gridiron"] = {
            "record": rec,
            "place": gd["_week_place"].get(e.id),
            "field": gd["field"],
            "picks": items,
            "contrarian": contrarian if contrarian and contrarian["count"] <= max(3, gd["players"] // 20) else None,
        }
        break

    dd = league.get("dropdead")
    for e in (x for x in mine if x.pool == "dropdead"):
        if not dd:
            break
        p = dd["_pick_by_entry"].get(e.id)
        if p is None:
            continue
        same = dd["_team_counts"].get(p["team_id"], 1)
        out["dropdead"] = {
            "chip": p["chip"],
            "result": p["result"],
            "same": same - 1,
            "pct": _pct(same, len(dd["_pick_by_entry"])),
        }
        break

    ld = league.get("loser")
    for e in (x for x in mine if x.pool == "loser"):
        if not ld:
            break
        p = ld["_pick_by_entry"].get(e.id)
        if p is None:
            continue
        same = ld["_team_counts"].get(p["team_id"], 1)
        out["loser"] = {
            "chip": p["chip"],
            "result": p["result"],
            "points": p["points"],
            "same": same - 1,
            "place": ld["_week_place"].get(e.id),
            "field": ld["players"],
        }
        break

    return out or None
