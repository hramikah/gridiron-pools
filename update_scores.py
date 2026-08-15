"""Scheduled job: pull final scores from The Odds API and finalize any games
that have completed. Idempotent -- safe to run as often as you like; games
already marked final are skipped, so a manual admin correction is never
overwritten.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402
from score_fetcher import update_scores  # noqa: E402

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
