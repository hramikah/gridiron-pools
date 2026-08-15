"""Weekly job: pull fresh lines from The Odds API and publish/update this
week's games. Meant to run every Thursday 08:00 Eastern via launchd, but
it's idempotent -- safe to run manually or more than once.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402
from publisher import publish_week  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

if __name__ == "__main__":
    try:
        summary = publish_week(app)
        logging.info(
            "Published %s: %s created, %s already published (lines frozen, unchanged)",
            summary["week_label"],
            summary["created"],
            summary["already_published"],
        )
        if summary["unmatched"]:
            logging.warning(
                "Unmatched NFL team names (check against the Team table): %s",
                summary["unmatched"],
            )
    except Exception:
        logging.exception("Failed to publish week")
        sys.exit(1)
