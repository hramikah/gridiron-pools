"""Scheduled job: pull final scores from The Odds API and finalize any games
that have completed, then settle any week whose pick deadline has passed.
Idempotent -- safe to run as often as you like; games already marked final are
skipped, so a manual admin correction is never overwritten, and a week's misses
are only ever processed once.

Settling the weeks here is what stops missed-pick processing depending on a
player happening to load a page: this job runs every two hours whether anyone
visits or not. It has to come *after* the scores, since an elimination follows
from a final result.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402
from score_fetcher import update_scores  # noqa: E402
from scoring import process_due_weeks  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if __name__ == "__main__":
    try:
        summary = update_scores(app)
    except Exception:
        logging.exception("Failed to update scores")
        sys.exit(1)

    logging.info(
        "Scores: %s finalized, %s already final, %s still pending",
        summary["finalized"],
        summary["already_final"],
        summary["pending"],
    )
    for line in summary.get("details", []):
        logging.info("  finalized %s", line)

    # A failure here must not look like a scoring failure, and must not stop
    # the next run: the weeks stay unprocessed and are retried in two hours.
    try:
        with app.app_context():
            season_year = app.config["CURRENT_SEASON"]
            settled = process_due_weeks(season_year)
        if settled:
            for week in settled:
                logging.info("  settled %s week %s", week.pool, week.number)
        else:
            logging.info("Weeks: nothing owed processing")
    except Exception:
        logging.exception("Failed to settle past-deadline weeks")
        sys.exit(1)
