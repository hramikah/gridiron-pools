"""Read-only: show what the FBS filter would keep and drop from the live feed.

    venv/bin/python3 scripts/check_fbs_filter.py

Pulls the college odds feed and sorts this week's games into the ones the
publisher would publish and the ones it would skip. Writes nothing -- no week,
no game, no database change. It spends a few Odds API credits.

Run it once before turning the Thursday timer back on, and any time a school
looks like it went missing from the board: a real FBS team showing up in the
"not on the FBS list" section means fbs.py needs its name.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from fbs import FBS_TEAMS, is_fbs, unrecognized  # noqa: E402
from helpers import get_setting  # noqa: E402
from publisher import _parse_commence, fetch_odds, week_window  # noqa: E402


def main():
    with app.app_context():
        api_key = get_setting("odds_api_key")
        season_start_str = get_setting("season_start_thursday")
        if not api_key:
            print("No Odds API key configured (Admin > Settings).")
            return 1
        if not season_start_str:
            print("No season start date configured (Admin > Settings).")
            return 1
        season_start = datetime.fromisoformat(season_start_str).date()

    number, start, end, _deadline, is_preseason = week_window(season_start)
    label = f"Preseason Week {number - 100}" if is_preseason else f"Week {number}"
    print(f"\n{label}: college games from {start:%a %d %b} to {end:%a %d %b}")
    print(f"FBS list carries {len(FBS_TEAMS)} teams\n")

    events = fetch_odds("college", api_key)
    kept, dropped, offenders = [], [], set()
    for event in events:
        kickoff = _parse_commence(event["commence_time"])
        if not (start <= kickoff <= end):
            continue
        away, home = event["away_team"], event["home_team"]
        line = f"{kickoff:%a %d %b %I:%M %p}  {away} @ {home}"
        if is_fbs(away) and is_fbs(home):
            kept.append(line)
        else:
            dropped.append(line)
            offenders.update(unrecognized([away, home]))

    print(f"--- WOULD PUBLISH ({len(kept)}) ---")
    for line in sorted(kept):
        print("  " + line)
    print(f"\n--- WOULD SKIP ({len(dropped)}) ---")
    for line in sorted(dropped):
        print("  " + line)

    print(f"\n--- NOT ON THE FBS LIST ({len(offenders)}) ---")
    for name in sorted(offenders):
        print("  " + name)
    print("\nThese should all be FCS schools. If a real FBS team is in that")
    print("list, add its name to FBS_TEAMS (or ALIASES) in fbs.py.")
    print("\nNothing was written.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
