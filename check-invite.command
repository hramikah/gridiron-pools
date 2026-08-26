#!/bin/bash
# ---------------------------------------------------------------------------
# What happened to ONE person's invite? Asks the live site about one address:
# was an invite created, has it been used, do they have an account yet, and did
# the message fall back to the dry-run log instead of being sent.
#
# Reads only. It cannot tell you whether the e-mail reached their inbox -- only
# what this site did. For delivery, use SendGrid's Activity Feed, which shows
# delivered / bounced / blocked per address.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null
[ -f "$KEY" ] || { echo "Missing $KEY."; read -n1 -s -p "Press any key..."; echo; exit 1; }

read -p "E-mail address to look up: " ADDR
[ -n "$ADDR" ] || { echo "Nothing entered."; read -n1 -s -p "Press any key..."; echo; exit 1; }
echo

# The address is quoted for the remote shell rather than pasted into it.
ssh -i "$KEY" $SSHOPTS "$HOST" "ADDR=$(printf '%q' "$ADDR") bash -s" <<'REMOTE'
cd /root/gridiron-pools || exit 1

venv/bin/python3 - "$ADDR" <<'PY'
import sqlite3, sys
addr = sys.argv[1].strip().lower()
c = sqlite3.connect("instance/pools.db")
print("Address:", addr)
print()

rows = c.execute(
    "select id, token, created_at, used_at from invite where lower(email) = ? order by id",
    (addr,)).fetchall()
if not rows:
    print("  INVITE   none -- this address was never invited from this site.")
else:
    for i, (iid, token, created, used) in enumerate(rows, 1):
        print(f"  INVITE   #{i} created {created} -- " + (f"used {used}" if used else "still open"))
        print(f"           https://gridironinvestment.com/auth/register?token={token}")
    if len(rows) > 1:
        print(f"  NOTE     {len(rows)} invites for one address (sent before the duplicate guard).")

print()
accs = c.execute(
    "select username, is_admin, created_at from user where lower(email) = ? order by id",
    (addr,)).fetchall()
if accs:
    for name, is_admin, created in accs:
        print(f"  ACCOUNT  {name}{' (admin)' if is_admin else ''} -- registered {created}")
else:
    print("  ACCOUNT  none yet -- they have not registered.")
PY

echo
echo "  Dry-run log (a hit here means the message was NOT actually sent):"
if grep -qi -- "$ADDR" logs/emails.log 2>/dev/null; then
  grep -i -- "$ADDR" logs/emails.log | tail -5 | sed 's/^/    /'
else
  echo "    none -- nothing fell back to the log for this address"
fi
REMOTE
echo
read -n1 -s -p "Press any key to close this window..."
echo
