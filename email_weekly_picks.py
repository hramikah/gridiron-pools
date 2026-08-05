"""Weekly job: once a week's pick deadline has passed, email every player
a recap of their own locked-in picks for that week. Idempotent (Week.picks_emailed
guards against double-sends) -- safe to run manually or on a schedule.
Meant to run shortly after the standard Saturday-noon deadline via launchd,
mirroring publish_week.py's Thursday-8am job.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402
from helpers import deadline_passed  # noqa: E402
from models import Week  # noqa: E402
from notifications import email_week_picks  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if __name__ == "__main__":
    with app.app_context():
        season_year = app.config["CURRENT_SEASON"]
        weeks = Week.query.filter_by(season_year=season_year).all()
        for week in weeks:
            if week.picks_emailed or not deadline_passed(week):
                continue
            try:
                count = email_week_picks(week)
                logging.info("Emailed Week %s picks recap to %s player(s).", week.number, count)
            except Exception:
                logging.exception("Failed to email picks for Week %s", week.number)
