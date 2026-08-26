"""Carry the real member accounts into the database that is about to go live.

    venv/bin/python3 scripts/import_real_users.py instance/pools.db            # dry run
    venv/bin/python3 scripts/import_real_users.py instance/pools.db --apply

The demo database holds two kinds of account: the real members who had signed
up before the August data loss (Crusher, GentlemanJack, Pigfoot, TOPHAT, Lord
Abbott, Moneyline and the rest, with their real e-mail addresses), and 135
invented players on @mockpool.test used to simulate a full season. Only the
first kind is copied. Everything else about those accounts -- their simulated
entries, picks and eliminations -- is left behind, because the live season has
not started.

Usernames, e-mail addresses, admin rights and the per-email team limit all
carry over exactly, so the sibling accounts that share one address stay
grouped: that is what makes the combined Billing page and the "Hi" menu's
account switching work.

**Every imported account gets a new random password.** In the demo database
all 149 accounts share one hash -- the word "Password" -- and copying that
onto a public site would hand anyone who has seen the demo a way into a real
member's account. Nobody is told the random one; each member gets in with
"Forgot password", which e-mails them a link, or an admin resets them from the
Players page. Tell people to type their USERNAME on that form rather than
their address: several of these accounts share an address, and the form picks
one of them arbitrarily.

An account whose username already exists in the target is left completely
alone, so this is safe to run twice.
"""

import secrets
import sqlite3
import sys
from datetime import datetime

MOCK_HINTS = ("@mockpool.test", "@example.com", "@test.")


def _is_real(email):
    email = (email or "").strip().lower()
    return bool(email) and not any(h in email for h in MOCK_HINTS)


def import_users(target, source, apply=False):
    # method= is pinned to match models.User.set_password. Werkzeug's own
    # default is scrypt, which the Mac's Python 3.9 cannot do at all --
    # hashlib.scrypt is missing in that build and it raises AttributeError.
    try:
        from werkzeug.security import generate_password_hash
    except ImportError:
        print("werkzeug is not importable -- run this with the app's venv python.")
        return 1

    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)

    incoming = [
        row for row in src.execute(
            "select username, email, is_admin, max_teams, created_at from user order by id"
        )
        if _is_real(row[1])
    ]
    src.close()

    if not incoming:
        print(f"No real accounts found in {source}.")
        return 1

    existing = {r[0].lower(): (r[1], r[2]) for r in
                dst.execute("select username, email, id from user")}
    to_add = [r for r in incoming if r[0].lower() not in existing]

    # An account that exists in both: leave it alone, EXCEPT when the one here
    # still carries a placeholder address and the demo has the member's real
    # one. That is the seeded 'admin' account, and leaving it on
    # admin@example.com would strand it outside its own family -- accounts are
    # linked by sharing an address, so admin and admin2 would bill separately.
    to_update, skipped = [], []
    for username, email, is_admin, max_teams, _ in incoming:
        here = existing.get(username.lower())
        if here is None:
            continue
        here_email, here_id = here
        if _is_real(here_email):
            skipped.append((username,))
        else:
            to_update.append((username, here_email, email, is_admin, max_teams, here_id))

    by_email = {}
    for username, email, is_admin, max_teams, _ in to_add:
        by_email.setdefault(email.lower(), []).append(
            f"{username}{' (admin)' if is_admin else ''}"
        )

    print(f"\n{len(incoming)} real accounts in {source}; "
          f"{len(to_add)} to add, {len(skipped)} already present.\n")
    for email, names in sorted(by_email.items()):
        limit = max(r[3] or 1 for r in to_add if (r[1] or "").lower() == email)
        print(f"  {email:<38} {', '.join(names)}"
              + (f"   [up to {limit} teams]" if limit > 1 else ""))
    if to_update:
        print()
        for username, was, now_email, is_admin, _limit, _id in to_update:
            print(f"  {username}: address {was or '(none)'} -> {now_email}"
                  + (" , made admin" if is_admin else ""))
        print("    (password left as it is -- change it yourself after going live)")
    if skipped:
        print(f"\n  already there, untouched: {', '.join(r[0] for r in skipped)}")

    print("\nEach one gets a NEW RANDOM password. Members sign in for the first")
    print("time via Forgot password (typing their USERNAME, not their address --")
    print("several of these share an address), or an admin resets them.")

    if not apply:
        print("\nDry run -- pass --apply to write.")
        dst.close()
        return 0

    now = datetime.now().isoformat(sep=" ")
    dst.executemany(
        "insert into user (username, email, password_hash, is_admin, max_teams, created_at) "
        "values (?, ?, ?, ?, ?, ?)",
        [
            (
                username,
                email,
                generate_password_hash(secrets.token_urlsafe(16), method="pbkdf2:sha256"),
                1 if is_admin else 0,
                max_teams or 1,
                created_at or now,
            )
            for username, email, is_admin, max_teams, created_at in to_add
        ],
    )
    for username, _was, now_email, is_admin, max_teams, uid in to_update:
        dst.execute(
            "update user set email = ?, is_admin = ?, max_teams = ? where id = ?",
            (now_email, 1 if is_admin else 0, max_teams or 1, uid),
        )
    dst.commit()
    total = dst.execute("select count(*) from user").fetchone()[0]
    admins = dst.execute("select count(*) from user where is_admin = 1").fetchone()[0]
    print(f"\nAdded {len(to_add)}, updated {len(to_update)}. "
          f"{target} now holds {total} accounts, {admins} admin.")
    dst.close()
    return 0


if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = args[0] if args else "instance/pools.db"
    source = args[1] if len(args) > 1 else "instance/testbed-demo.db"
    if "--apply" in flags:
        import shutil
        backup = f"{target}.before-import-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy(target, backup)
        print(f"Backed up to {backup}")
    sys.exit(import_users(target, source, apply="--apply" in flags))
