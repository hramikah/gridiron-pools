"""Re-link the sibling accounts in the TESTBED database.

Accounts are tied together by sharing an e-mail address: Billing totals every
account on the address, and the Hi menu lists them for switching. When the
testbed addresses were redirected for the SendGrid testing, each account got
its *own* alias (adventurecoastmarketing+gentlemanjack2@gmail.com and so on),
and the four commissioner accounts were blanked outright. Either way the
families came apart, which is why a player with three teams sees three
separate Billing pages.

This gives every account in a family the SAME alias -- the family's primary
account name -- so the link is back and test mail still lands in the one
inbox. Families are rebuilt from instance/testbed-demo.db, which still has the
original addresses.

Real-mail test accounts and the seeded @mockpool.test players are left alone.

Dry run by default; pass --apply to write. The site must be STOPPED first --
writing to a SQLite file another process has open is what corrupted this
database once already.
"""
import re, sqlite3, sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
TESTBED = args[0] if args else "testbed/pools.db"
SEED = args[1] if len(args) > 1 else "instance/testbed-demo.db"
APPLY = "--apply" in sys.argv

REDIRECT = "adventurecoastmarketing@gmail.com"
KEEP = {"hunterhramika@yahoo.com", REDIRECT}

seed = {u.lower(): (e or "").lower()
        for u, e in sqlite3.connect(SEED).execute("select username, email from user")}

db = sqlite3.connect(TESTBED)
rows = db.execute("select id, username, email from user order by id").fetchall()

families = {}
for uid, name, email in rows:
    cur = (email or "").lower()
    if cur == "hunterhramika@yahoo.com" or cur.endswith("@mockpool.test"):
        continue
    key = seed.get(name.lower()) or cur or f"user:{uid}"
    families.setdefault(key, []).append([uid, name, cur])

changes = []
for members in families.values():
    members.sort()
    # The admin family keeps the plain address it already uses.
    if any(m[2] == REDIRECT for m in members):
        target = REDIRECT
    else:
        slug = re.sub(r"[^a-z0-9]+", "", members[0][1].lower()) or f"user{members[0][0]}"
        target = f"adventurecoastmarketing+{slug}@gmail.com"
    for uid, name, cur in members:
        if cur != target:
            changes.append((uid, name, cur, target))

for uid, name, cur, new in changes:
    print(f"  {name:<16} {cur or '(blank)':<48} -> {new}")
print(f"{len(changes)} of {len(rows)} accounts change.")

if APPLY:
    db.executemany("update user set email=? where id=?",
                   [(new, uid) for uid, _, _, new in changes])
    db.commit()
    print("applied.")
else:
    print("(dry run -- pass --apply to write)")
