"""Strip the test site back to a blank season, keeping the accounts.

Deletes every week, game, pick, missed-week row and pool entry, along with
the activity log and the message board. Keeps the user accounts, the 32
teams and their Loser Pool point values -- so the site looks like the
morning after registration closed and before anyone has built a schedule.

Also releases the frozen testbed clock and any pinned current week, because
a site with no weeks in it has no business pretending it is a Tuesday in
August.

The accounts survive but their pool entries do not: an entry is a
season's-worth of state (eliminations, buy-backs, makeup allowances), not a
login. Re-joining is a click per pool per player, or ask for a seed.

Local test databases only -- see testbed_guard.py.
"""

from app import app
from models import (
    ActivityLog,
    ContactMessage,
    Entry,
    Game,
    GridironMiss,
    Invite,
    PasswordReset,
    Pick,
    Setting,
    Team,
    User,
    Week,
    db,
)
from testbed_guard import require_testbed_database

# Settings that describe a moment rather than a configuration.
TRANSIENT_SETTINGS = ("testbed_fake_now", "active_week")


def main():
    with app.app_context():
        uri = require_testbed_database(app, "wipe_to_blank.py")
        print(f"Wiping {uri}")

        before = {
            "weeks": Week.query.count(),
            "games": Game.query.count(),
            "picks": Pick.query.count(),
            "entries": Entry.query.count(),
        }

        for model in (Pick, GridironMiss, Entry, Game, Week,
                      ActivityLog, ContactMessage, Invite, PasswordReset):
            model.query.delete(synchronize_session=False)

        for key in TRANSIENT_SETTINGS:
            row = db.session.get(Setting, key)
            if row:
                db.session.delete(row)
        db.session.commit()

        print(f"  removed {before['weeks']} weeks, {before['games']} games, "
              f"{before['picks']} picks, {before['entries']} entries")
        print(f"  kept {User.query.count()} accounts and {Team.query.count()} teams")
        print("  clock released -- the site is back on real time")


if __name__ == "__main__":
    main()
