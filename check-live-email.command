#!/bin/bash
# ---------------------------------------------------------------------------
# Is the LIVE site actually able to send e-mail, and did anything go out?
#
# Reads only -- changes nothing. It reports what the droplet has configured
# and what its e-mail log says. The key is shown masked, never in full.
#
# The thing worth knowing: with no key configured, mailer.py falls back to
# writing the message into logs/emails.log and reporting success. A silent
# failure looks exactly like a send, so this is how you tell them apart.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null
[ -f "$KEY" ] || { echo "Missing $KEY."; read -n1 -s -p "Press any key..."; echo; exit 1; }

echo "=== Live e-mail check ==="
echo
ssh -i "$KEY" $SSHOPTS "$HOST" '
cd /root/gridiron-pools
venv/bin/python3 - <<PY
import sqlite3, os
db = "instance/pools.db"
c = sqlite3.connect(db)
s = dict(c.execute("select key, value from setting"))

def show(key, label):
    v = (s.get(key) or "").strip()
    if not v:
        print(f"  MISSING  {label}")
    elif "key" in key:
        print(f"  set      {label}: {v[:6]}...{v[-4:]} ({len(v)} chars)")
    else:
        print(f"  set      {label}: {v}")

print("Settings on the live database:")
show("sendgrid_api_key", "SendGrid API key")
show("sendgrid_from_email", "From address")
show("site_url", "App link")
print()
print("Roster:", c.execute("select count(*) from user").fetchone()[0], "accounts,",
      c.execute("select count(*) from entry").fetchone()[0], "entries")
inv = c.execute("select count(*), sum(used_at is not null) from invite").fetchone()
print("Invites:", inv[0], "sent,", inv[1] or 0, "used,", inv[0] - (inv[1] or 0), "still open")
PY
echo
echo "Last 12 lines of logs/emails.log (a DRY-RUN line here means it was NOT sent):"
tail -12 logs/emails.log 2>/dev/null || echo "  (no emails.log -- nothing has fallen back to dry-run)"
echo
echo "Size of emails.log: $(du -h logs/emails.log 2>/dev/null | cut -f1 || echo none)"
'
echo
read -n1 -s -p "Press any key to close this window..."
echo
