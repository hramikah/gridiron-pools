#!/bin/bash
# ---------------------------------------------------------------------------
# Set a password on the LOCAL TEST SITE only (testbed/pools.db).
#
# It refuses to touch instance/pools.db, so it can never change a real
# account. Use it when you cannot remember what you set on the testbed.
#
# You type the password here; it is not shown on screen and is stored the same
# hashed way the site stores every password.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
DB="testbed/pools.db"

echo "=== Test site password ==="
echo "Database: $DB   (the live site and instance/pools.db are not touched)"
echo

[ -f "$DB" ] || { echo "No $DB yet -- run start-testbed.command once first."
                  read -n1 -s -p "Press any key..."; echo; exit 1; }

PY=./venv/bin/python3
[ -x "$PY" ] || PY=python3

echo "Accounts on the test site:"
"$PY" - "$DB" <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
for name, admin in c.execute("select username, is_admin from user order by is_admin desc, username"):
    print("   %-24s%s" % (name, "  (admin)" if admin else ""))
PYEOF
echo

read -p "Username to set a password for: " NAME
[ -n "$NAME" ] || { echo "Nothing entered."; read -n1 -s -p "Press any key..."; echo; exit 1; }
read -s -p "New password: " PW1; echo
read -s -p "Type it again: " PW2; echo
[ "$PW1" = "$PW2" ] || { echo "They did not match. Nothing changed."
                         read -n1 -s -p "Press any key..."; echo; exit 1; }
[ -n "$PW1" ] || { echo "Empty password. Nothing changed."
                   read -n1 -s -p "Press any key..."; echo; exit 1; }
echo

NAME="$NAME" PW="$PW1" "$PY" - "$DB" <<'PYEOF'
import os, sqlite3, sys
from werkzeug.security import generate_password_hash
db = sys.argv[1]
if "testbed" not in db.lower():
    print("Refusing to write to a database that is not the testbed."); raise SystemExit(1)
name, pw = os.environ["NAME"], os.environ["PW"]
c = sqlite3.connect(db)
row = c.execute("select id from user where username = ?", (name,)).fetchone()
if not row:
    print("No account called %r on the test site. Nothing changed." % name); raise SystemExit(1)
c.execute("update user set password_hash = ? where id = ?",
          (generate_password_hash(pw, method="pbkdf2:sha256"), row[0]))
c.commit()
print("Password set for %s on the test site." % name)
PYEOF

echo
echo "Log in at http://127.0.0.1:8090/auth/login"
read -n1 -s -p "Press any key to close this window..."
echo
