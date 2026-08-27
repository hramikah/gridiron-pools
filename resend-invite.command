#!/bin/bash
# ---------------------------------------------------------------------------
# Get Gridiron Pools registration links, to paste into your own e-mail.
# Run this on the Mac -- it is the only machine with the droplet key.
#
# Enter ONE address, or SEVERAL separated by spaces or commas.
#
# For each one it shows what the site already knows (account yet? invite
# already there? did an earlier message fall into the dry-run log instead of
# being sent?), then prints the link.
#
# Each link is real and live in the database as soon as it is printed. If you
# only want to copy them, answer N at the prompt -- nothing is e-mailed.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null
[ -f "$KEY" ] || { echo "Missing $KEY."; read -n1 -s -p "Press any key..."; echo; exit 1; }

if [ "$#" -gt 0 ]; then
  RAW="$*"
else
  echo "E-mail address, or several separated by spaces or commas:"
  read -r RAW
fi
# Commas are a convenience for pasting a list; the shell splits on spaces.
ADDRS=$(echo "$RAW" | tr ',;' '  ')
set -- $ADDRS
[ "$#" -gt 0 ] || { echo "Nothing entered."; read -n1 -s -p "Press any key..."; echo; exit 1; }
echo

ssh -i "$KEY" $SSHOPTS "$HOST" 'cat > /tmp/resend_invite.py' <<'PYEOF'
import os, sqlite3, sys

os.chdir("/root/gridiron-pools")
sys.path.insert(0, "/root/gridiron-pools")

addr = sys.argv[1].strip()
do_send = len(sys.argv) > 2 and sys.argv[2] == "send"
low = addr.lower()

con = sqlite3.connect("instance/pools.db")
print("Address:", addr)
print()

accs = con.execute("select username, is_admin, created_at from user where lower(email)=? order by id",
                   (low,)).fetchall()
if accs:
    for name, is_admin, created in accs:
        print("  ACCOUNT  %s%s -- registered %s" % (name, " (admin)" if is_admin else "", created))
    print()
    print("  This address ALREADY HAS AN ACCOUNT. They do not need an invite --")
    print("  they need to log in, or use Forgot Password. Nothing was sent.")
    sys.exit(0)
print("  ACCOUNT  none yet -- they have not registered.")

rows = con.execute("select id, token, created_at, used_at from invite where lower(email)=? order by id",
                   (low,)).fetchall()
link = None
for i, (iid, token, created, used) in enumerate(rows, 1):
    print("  INVITE   #%d created %s -- %s" % (i, created, ("USED %s" % used) if used else "still open"))
    if not used:
        link = "https://gridironinvestment.com/auth/register?token=%s" % token
if not rows:
    print("  INVITE   none -- this address was never invited from this site.")

print()
print("  Dry-run log (a hit here means an earlier message was NOT actually sent):")
hits = []
try:
    with open("logs/emails.log") as f:
        hits = [l.rstrip() for l in f if low in l.lower()]
except IOError:
    pass
print("\n".join("    " + h for h in hits[-5:]) or "    none -- nothing fell back to the log")
print()

from app import create_app
from models import Invite, db
import secrets

app = create_app()
with app.app_context():
    inv = Invite.query.filter(db.func.lower(Invite.email) == low, Invite.used_at.is_(None)).first()
    created_new = False
    if inv is None:
        # Persist it even when we are only looking up. A token that is not in
        # the database is a dead link, and the whole point of printing it is
        # so it can be pasted into a message by hand.
        inv = Invite(email=addr, token=secrets.token_urlsafe(32))
        db.session.add(inv)
        db.session.commit()
        created_new = True
    link = "https://gridironinvestment.com/auth/register?token=%s" % inv.token

    print("  " + "=" * 68)
    print("  REGISTRATION LINK -- copy this line%s:"
          % (" (newly created)" if created_new else " (their existing invite)"))
    print()
    print("  " + link)
    print()
    print("  " + "=" * 68)
    print("  Live in the database now, single-use, and specific to this address.")
    print("  Paste it into your own e-mail and it will work.")
    print()

    if not do_send:
        print("  (Nothing was sent. Re-run and answer y to send the e-mail.)")
        sys.exit(0)

    import mailer
    from helpers import get_setting
    key = get_setting("sendgrid_api_key")
    frm = get_setting("sendgrid_from_email")
    if not key or not frm:
        print("  *** SendGrid is not configured on the live site -- nothing can be sent.")
        sys.exit(1)
    print("  Sending from:", frm)
    subject = "You are invited to Gridiron Pools"
    body = (
        "Hi,\n\n"
        "You are invited to join Gridiron Pools -- our NFL pick'em pools: "
        "Drop Dead Pool, Loser Pool, and Gridiron Investments.\n\n"
        "Register here: %s\n\n"
        "This link is just for you -- head there to create your account and get started. "
        "See you in the pool!\n\n"
        "-- Gridiron Pools" % link
    )
    try:
        mailer._sendgrid_request(addr, subject, body, key, frm)
        print("  SENT OK -- SendGrid accepted the message for", addr)
        print()
        print("  Accepted is not the same as delivered. Check SendGrid's Activity")
        print("  Feed for this address to see delivered / bounced / blocked /")
        print("  dropped. Hotmail in particular often files new senders in Junk.")
    except Exception as e:
        print("  *** SEND FAILED:", type(e).__name__, e)
        resp = getattr(e, "response", None)
        if resp is not None:
            print("  *** SendGrid said:", resp.status_code, resp.text[:500])
        sys.exit(1)
PYEOF

for A in "$@"; do
  echo "================================================================"
  echo "=== $A"
  echo "================================================================"
  echo
  ssh -i "$KEY" $SSHOPTS "$HOST" "cd /root/gridiron-pools && venv/bin/python3 /tmp/resend_invite.py $(printf '%q' "$A") 2>&1"
  echo
done

echo "Copy the links above if you want to send them yourself."
echo
read -p "Also send them from the site as e-mails? [y/N] " YN
echo
if [ "$YN" = "y" ] || [ "$YN" = "Y" ]; then
  for A in "$@"; do
    echo "--- e-mailing $A"
    ssh -i "$KEY" $SSHOPTS "$HOST" "cd /root/gridiron-pools && venv/bin/python3 /tmp/resend_invite.py $(printf '%q' "$A") send 2>&1"
    echo
  done
else
  echo "Nothing e-mailed. The links above are still valid."
fi
ssh -i "$KEY" $SSHOPTS "$HOST" 'rm -f /tmp/resend_invite.py' 2>/dev/null
echo
read -n1 -s -p "Press any key to close this window..."
echo
