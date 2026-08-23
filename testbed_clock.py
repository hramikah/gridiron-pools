"""Move or release the testbed's frozen clock.

    python testbed_clock.py              # back to real time
    python testbed_clock.py 19:30        # same day, half past seven
    python testbed_clock.py 2026-08-22T12:30

Only a testbed database has a clock to move: helpers.now_eastern() reads the
`testbed_fake_now` setting solely when GRIDIRON_DATABASE_URI marks the
database as one, so nothing here can reach the live site.

Handy for the buy-back, which shuts at 7:00 PM Thursday -- seed the pool at
4:00 PM, look at the offer, then jump to 19:30 and watch it go while the
week's picks stay open until Saturday noon.
"""

import sys
from datetime import datetime

from app import app
from helpers import TESTBED_CLOCK_SETTING, set_setting
from models import Setting, db
from testbed_guard import require_testbed_database


def resolve(arg, current):
    """A bare HH:MM keeps whatever day the clock is already frozen on."""
    if ":" in arg and "-" not in arg and "T" not in arg:
        hour, minute = (int(part) for part in arg.split(":")[:2])
        base = current or datetime.now()
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return datetime.fromisoformat(arg)


def main():
    with app.app_context():
        require_testbed_database(app, "testbed_clock.py")
        row = db.session.get(Setting, TESTBED_CLOCK_SETTING)
        current = None
        if row and row.value:
            try:
                current = datetime.fromisoformat(row.value)
            except ValueError:
                pass

        arg = sys.argv[1] if len(sys.argv) > 1 else None
        if arg in (None, "real", "clear", "off", "now"):
            if row:
                db.session.delete(row)
                db.session.commit()
            print("Clock released -- the test site is back on real time.")
            return

        moment = resolve(arg, current)
        set_setting(TESTBED_CLOCK_SETTING, moment.isoformat())
        print(f"Clock frozen at {moment:%A %d %B %Y, %I:%M %p} Eastern.")
        print("Refresh the browser; nothing needs restarting.")


if __name__ == "__main__":
    main()
