"""Empty the roster on the database about to go live, and create the first account.

    GP_USERNAME=... GP_EMAIL=... GP_PASSWORD=... GP_MAX_TEAMS=3 \
        venv/bin/python3 scripts/fresh_roster.py instance/pools.db --apply

Everyone else arrives by invitation, so the live site starts with exactly one
account: yours, an admin, so there is someone to send those invitations.

What is deleted: every user row, and the rows that only make sense with one --
activity log lines, password-reset tokens, message-board threads, and unused
invitations. Teams, weeks, the Loser Pool point table and the settings are all
left alone; they describe the season, not the people.

It REFUSES if any entry or pick exists. Once members have joined, deleting the
roster would delete their entries with it, and that is real money.

The password is read from the environment rather than the command line so it
does not sit in shell history or in the process list.
"""

import os
import sqlite3
import sys
from datetime import datetime

WIPE_TABLES = ("activity_log", "password_reset", "contact_message", "invite", "user")


def fresh(path, username, email, password, max_teams=1, apply=False):
    # method= is pinned to match models.User.set_password. Werkzeug's own
    # default is scrypt, which the Mac's Python 3.9 cannot do at all --
    # hashlib.scrypt is missing in that build and it raises AttributeError.
    try:
        from werkzeug.security import generate_password_hash
    except ImportError:
        print("werkzeug is not importable -- run this with the app's venv python.")
        return 1

    conn = sqlite3.connect(path)

    for table in ("entry", "pick"):
        try:
            n = conn.execute(f"select count(*) from {table}").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
        if n:
            print(f"REFUSING: {path} holds {n} {table} rows.")
            print("Wiping the roster would delete real entries. Nothing was changed.")
            return 1

    counts = {}
    for table in WIPE_TABLES:
        try:
            counts[table] = conn.execute(f"select count(*) from {table}").fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = 0

    print(f"\nStarting fresh in {path}\n")
    print("  to delete:")
    for table, n in counts.items():
        if n:
            print(f"    {n:>4}  {table.replace('_', ' ')}")
    if not any(counts.values()):
        print("    (nothing -- the roster is already empty)")

    print("\n  first account:")
    print(f"    username   {username}")
    print(f"    email      {email}")
    print(f"    role       admin")
    print(f"    teams      up to {max_teams} on this address")
    print(f"    password   {'set (' + str(len(password)) + ' characters)' if password else 'MISSING'}")

    if not password or len(password) < 8:
        print("\nREFUSING: give it a password of at least 8 characters.")
        return 1
    if not username or not email or "@" not in email:
        print("\nREFUSING: a username and a real e-mail address are both required.")
        return 1

    if not apply:
        print("\nDry run -- pass --apply to write.")
        conn.close()
        return 0

    for table in WIPE_TABLES:
        try:
            conn.execute(f"delete from {table}")
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "insert into user (username, email, password_hash, is_admin, max_teams, created_at) "
        "values (?, ?, ?, 1, ?, ?)",
        (username, email, generate_password_hash(password, method="pbkdf2:sha256"), max_teams,
         datetime.now().isoformat(sep=" ")),
    )
    conn.commit()

    row = conn.execute(
        "select id, username, email, is_admin, max_teams from user"
    ).fetchall()
    print(f"\nDone. The roster is now:")
    for uid, name, mail, is_admin, limit in row:
        print(f"    #{uid}  {name} <{mail}>  {'admin' if is_admin else 'player'}, up to {limit} teams")
    conn.close()
    return 0


if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "instance/pools.db"
    if "--apply" in flags:
        import shutil
        backup = f"{path}.before-fresh-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy(path, backup)
        print(f"Backed up to {backup}")
    sys.exit(fresh(
        path,
        username=os.environ.get("GP_USERNAME", "").strip(),
        email=os.environ.get("GP_EMAIL", "").strip(),
        password=os.environ.get("GP_PASSWORD", ""),
        max_teams=int(os.environ.get("GP_MAX_TEAMS", "1") or 1),
        apply="--apply" in flags,
    ))
