"""Publish the week that starts this coming Thursday, without waiting for it.

    venv/bin/python3 scripts/publish_this_weekend.py            # what would happen
    venv/bin/python3 scripts/publish_this_weekend.py --apply    # do it

A week runs Thursday to Wednesday, so the publisher run on a Wednesday would
pick up the week that is about to *end* -- one whose Saturday-noon deadline is
already behind us, arriving locked and unpickable. This aims it at the coming
Thursday instead, so the week it creates is the one people are about to play,
with its deadline this Saturday at noon.

Everything else is the ordinary publish: it pulls NFL (the preseason feed
while the season has not started) and college from The Odds API, creates the
week in all three pools, and imports every game whose kickoff falls inside the
window, carrying spreads and over/unders for Gridiron.

Games already published keep their lines -- a second run adds what is new and
never moves an existing number.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from helpers import get_setting, now_eastern  # noqa: E402
from publisher import publish_week, week_window  # noqa: E402


def coming_thursday(ref=None):
    """Today if today is Thursday, otherwise the next one."""
    ref = ref or now_eastern()
    return ref + timedelta(days=(3 - ref.weekday()) % 7)


def main(apply=False):
    with app.app_context():
        season_start_str = get_setting("season_start_thursday")
        if not season_start_str:
            print("No season start date set (Admin > Settings). Nothing can be published.")
            return 1
        season_start = datetime.fromisoformat(season_start_str).date()
        target = coming_thursday()
        number, start, end, deadline, is_preseason = week_window(season_start, reference=target)

    print(f"\nAiming at {target:%A %d %B %Y}\n")
    print(f"  week          {'Preseason Week ' + str(number - 100) if is_preseason else 'Week ' + str(number)}")
    print(f"  games from    {start:%a %d %b} to {end:%a %d %b}")
    print(f"  pick deadline {deadline:%A %d %B, %I:%M %p} Eastern")
    print(f"  NFL feed      {'preseason' if is_preseason else 'regular season'}")

    if not apply:
        print("\nDry run -- pass --apply to pull the lines and create it.")
        return 0

    print("\nPulling from The Odds API...\n")
    summary = publish_week(app, reference=target)
    print(f"  {summary['week_label']}: {summary['created']} games added, "
          f"{summary['already_published']} already there")
    if summary.get("unmatched"):
        print(f"  unmatched team names: {', '.join(sorted(summary['unmatched']))}")
    if not summary["created"] and not summary["already_published"]:
        print("\n  Nothing came back. Either the feed has no games in this window yet,")
        print("  or the API plan does not include the preseason feed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
