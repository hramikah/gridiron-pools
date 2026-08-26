"""Fix the things check_live_db.py flags, on the database about to go live.

    venv/bin/python3 scripts/prepare_live_db.py instance/pools.db            # dry run
    venv/bin/python3 scripts/prepare_live_db.py instance/pools.db --apply

Three jobs, none of which touch play data:

1. **Loser Pool points.** Copied from instance/testbed-demo.db, whose 32 rows
   were checked against the printed sheet on 2026-08-14 (10 Cardinals through
   41 Rams). Matched by team name, not by id, because ids differ between
   databases -- matching on id is how a point table ends up describing the
   wrong teams. Refuses to run unless all 32 match.

2. **The pinned current week.** A leftover active_week overrides auto-detection
   for every pool, for everyone, so the site freezes on that week. Cleared.

3. **Placeholder accounts** (--delete-test-users). Test logins are real logins
   on a live site. Only accounts whose e-mail looks fake are candidates, never
   an account that holds entries or picks, and never the account named by
   --keep-admin.

Run it with the live site stopped. Writing to a SQLite file another process
has open is what corrupted a database here once already.
"""

import shutil
import sqlite3
import sys
from datetime import datetime

PLACEHOLDER_HINTS = ("@example.com", "@mockpool.test", "@test.")
SOURCE_DEFAULT = "instance/testbed-demo.db"


def prepare(path, source=SOURCE_DEFAULT, season_year=2026, apply=False,
            delete_test_users=False, keep_admin="admin"):
    conn = sqlite3.connect(path)
    changes = []

    # 1. Loser Pool points -------------------------------------------------
    have = conn.execute(
        "select count(*) from loser_pool_points where season_year = ?", (season_year,)
    ).fetchone()[0]
    if have == 32:
        print(f"loser points: already 32 rows for {season_year}, leaving alone")
    else:
        src = sqlite3.connect(source)
        wanted = src.execute(
            "select t.city, t.name, lp.points from loser_pool_points lp "
            "join team t on t.id = lp.team_id where lp.season_year = ?",
            (season_year,),
        ).fetchall()
        src.close()
        if len(wanted) != 32:
            print(f"loser points: {source} has {len(wanted)} rows, expected 32 -- ABORTING")
            return 1
        here = {(c, n): i for i, c, n in conn.execute("select id, city, name from team")}
        missing = [(c, n) for c, n, _ in wanted if (c, n) not in here]
        if missing:
            print(f"loser points: no matching team row for {missing} -- ABORTING")
            return 1
        changes.append(f"loser points: insert 32 rows for {season_year}")
        if apply:
            conn.execute("delete from loser_pool_points where season_year = ?", (season_year,))
            conn.executemany(
                "insert into loser_pool_points (season_year, team_id, points) values (?, ?, ?)",
                [(season_year, here[(c, n)], p) for c, n, p in wanted],
            )

    # 2. The pinned current week ------------------------------------------
    pinned = conn.execute(
        "select value from setting where key = 'active_week'"
    ).fetchone()
    if pinned and (pinned[0] or "").strip():
        changes.append(f"current week: unpin from {pinned[0]} (back to automatic)")
        if apply:
            conn.execute("update setting set value = '' where key = 'active_week'")
    else:
        print("current week: already automatic")

    # 3. Placeholder accounts ---------------------------------------------
    users = conn.execute("select id, username, email, is_admin from user").fetchall()
    candidates = []
    for uid, username, email, is_admin in users:
        if username == keep_admin:
            continue
        if not any(h in (email or "") for h in PLACEHOLDER_HINTS):
            continue
        entries = conn.execute("select count(*) from entry where user_id = ?", (uid,)).fetchone()[0]
        picks = conn.execute(
            "select count(*) from pick where entry_id in (select id from entry where user_id = ?)",
            (uid,),
        ).fetchone()[0]
        if entries or picks:
            print(f"account {username}: looks like a placeholder but holds "
                  f"{entries} entries / {picks} picks -- left alone")
            continue
        candidates.append((uid, username, email, is_admin))

    if candidates:
        listing = ", ".join(f"{u[1]}{' (admin)' if u[3] else ''}" for u in candidates)
        if delete_test_users:
            changes.append(f"accounts: delete {len(candidates)} placeholder(s) -- {listing}")
            if apply:
                conn.executemany("delete from user where id = ?", [(u[0],) for u in candidates])
        else:
            print(f"accounts: {len(candidates)} placeholder(s) found -- {listing}")
            print("          pass --delete-test-users to remove them")
    else:
        print("accounts: no placeholder logins to remove")

    print()
    if not changes:
        print("Nothing to change.")
        conn.close()
        return 0
    for line in changes:
        print(f"  {line}")
    print()
    if apply:
        conn.commit()
        print(f"Applied to {path}.")
    else:
        print("Dry run -- pass --apply to write.")
    conn.close()
    return 0


if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "instance/pools.db"
    source = args[1] if len(args) > 1 else SOURCE_DEFAULT
    if "--apply" in flags:
        backup = f"{path}.before-prepare-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy(path, backup)
        print(f"Backed up to {backup}\n")
    sys.exit(prepare(
        path,
        source=source,
        apply="--apply" in flags,
        delete_test_users="--delete-test-users" in flags,
    ))
