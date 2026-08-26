"""Readiness report for the database that is about to go live.

Reads only -- it changes nothing. Run it against instance/pools.db before
sending that file to the droplet, and again after any fix, until every line
reads OK.

    venv/bin/python3 scripts/check_live_db.py instance/pools.db

Each check exists because it is something that has actually been wrong, or
would silently break a pool in week 1:

  season start   drives week numbering, every Saturday-noon deadline, the
                 auto-publisher's preseason/regular switch, and the Week 1
                 signup cutoff. Unset means publish_week() raises and nothing
                 is ever published.
  current week   a pinned active_week overrides every pool's auto-detection,
                 for everyone, forever. A number left pinned from testing
                 freezes the whole site on that week.
  loser points   the Loser Pool is scored entirely from this table. Empty
                 means every team is worth 0 and the rules page prints an
                 empty grid.
  weeks          18 per pool, no gaps: a missing week breaks the Drop Dead
                 buy-back, which needs the week AFTER an elimination to exist.
  buy-backs      Drop Dead weeks 1-4 open, everything else closed.
  teams          32, and every Loser point row pointing at one of them.
  accounts       leftover test logins are real logins on a live site.
  play data      entries/picks/games from testing must not ride along.
"""

import sqlite3
import sys

PLACEHOLDER_HINTS = ("@example.com", "@mockpool.test", "@test.")


def _rows(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        return [("ERROR", str(exc))]


def check(path, season_year=2026):
    conn = sqlite3.connect(path)
    problems, warnings = [], []

    def ok(label, detail=""):
        print(f"  OK      {label}{(' -- ' + detail) if detail else ''}")

    def bad(label, detail):
        problems.append(label)
        print(f"  PROBLEM {label} -- {detail}")

    def warn(label, detail):
        warnings.append(label)
        print(f"  CHECK   {label} -- {detail}")

    print(f"\nReadiness report for {path} (season {season_year})\n")

    settings = dict(_rows(conn, "select key, value from setting"))

    start = settings.get("season_start_thursday")
    if start:
        ok("season start", f"{start} -- Week 1 deadline is the Saturday after, noon Eastern")
    else:
        bad("season start", "not set: publish_week() will raise and nothing publishes")

    pinned = (settings.get("active_week") or "").strip()
    if pinned:
        bad("current week", f"pinned to {pinned}: overrides every pool's auto-detection. Set it to Automatic")
    else:
        ok("current week", "automatic (by deadline)")

    if settings.get("odds_api_key"):
        ok("odds api key", "set")
    else:
        bad("odds api key", "not set: no games can be published")

    teams = _rows(conn, "select count(*) from team")[0][0]
    if teams == 32:
        ok("teams", "32")
    else:
        bad("teams", f"{teams}, expected 32")

    lp = _rows(
        conn,
        "select count(*), min(points), max(points) from loser_pool_points where season_year = ?",
        (season_year,),
    )[0]
    if lp[0] == 32:
        ok("loser points", f"32 teams, {lp[1]}-{lp[2]}")
    else:
        bad("loser points", f"{lp[0]} rows for {season_year}, expected 32: every team scores 0 without them")

    orphan = _rows(
        conn,
        "select count(*) from loser_pool_points lp left join team t on t.id = lp.team_id where t.id is null",
    )[0][0]
    if orphan:
        bad("loser points", f"{orphan} rows point at a team that does not exist")

    for pool in ("gridiron", "loser", "dropdead"):
        nums = [r[0] for r in _rows(
            conn,
            "select number from week where season_year = ? and pool = ? and is_preseason = 0 order by number",
            (season_year, pool),
        )]
        missing = [n for n in range(1, 19) if n not in nums]
        if missing:
            bad(f"weeks ({pool})", f"missing {missing}: a gap breaks the Drop Dead buy-back")
        else:
            ok(f"weeks ({pool})", f"1-18, {len(nums)} rows")

    dd = _rows(
        conn,
        "select number, buyback_open from week where season_year = ? and pool = 'dropdead' and is_preseason = 0 order by number",
        (season_year,),
    )
    wrong = [n for n, flag in dd if (n <= 4) != bool(flag)]
    if wrong:
        warn("drop dead buy-backs", f"weeks {wrong} do not match 'open for 1-4 only'")
    else:
        ok("drop dead buy-backs", "open for weeks 1-4, closed after")

    deadlines = _rows(
        conn,
        "select number, pick_deadline from week where season_year = ? and pool = 'gridiron' and is_preseason = 0 order by number limit 3",
        (season_year,),
    )
    for number, when in deadlines:
        if when and not str(when).endswith(("12:00:00", "12:00:00.000000")):
            warn("deadlines", f"week {number} is {when}, not noon")

    users = _rows(conn, "select username, email, is_admin from user")
    admins = [u for u in users if u[2]]
    placeholders = [u for u in users if any(h in (u[1] or "") for h in PLACEHOLDER_HINTS)]
    print(f"\n  accounts: {len(users)} total, {len(admins)} admin")
    if placeholders:
        warn(
            "test accounts",
            f"{len(placeholders)} look like placeholders: "
            + ", ".join(f"{u[0]} <{u[1]}>" for u in placeholders[:8])
            + ("..." if len(placeholders) > 8 else ""),
        )
    if not admins:
        bad("admin account", "nobody can administer the site")

    for table, label in (("entry", "entries"), ("pick", "picks"), ("game", "games"), ("gridiron_miss", "misses")):
        n = _rows(conn, f"select count(*) from {table}")[0][0]
        if n:
            warn(f"leftover {label}", f"{n} rows -- fine only if this is real play data")

    print()
    if problems:
        print(f"NOT READY: {len(problems)} problem(s) -- {', '.join(problems)}")
    elif warnings:
        print(f"Ready, with {len(warnings)} thing(s) to eyeball above.")
    else:
        print("Ready.")
    conn.close()
    return 1 if problems else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "instance/pools.db"
    season = int(args[1]) if len(args) > 1 else 2026
    sys.exit(check(path, season))
