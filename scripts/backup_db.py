#!/usr/bin/env python3
"""Nightly database backup.

Copies every *.db in instance/ into instance/nightly/ using SQLite's own
backup API, which is safe to run while the site is live -- unlike cp, it
cannot capture a half-written transaction. Prunes copies older than KEEP_DAYS.

Run from cron:
    30 7 * * * cd /root/gridiron-pools && venv/bin/python3 scripts/backup_db.py
"""
import os
import sqlite3
import time
from datetime import datetime

KEEP_DAYS = 21
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "instance")
DEST = os.path.join(SRC, "nightly")

os.makedirs(DEST, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

for name in sorted(os.listdir(SRC)):
    if not name.endswith(".db"):
        continue
    src = os.path.join(SRC, name)
    out = os.path.join(DEST, f"{name}.{stamp}")
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        target = sqlite3.connect(out)
        with target:
            source.backup(target)
        target.close()
        source.close()
        print(f"backed up {name} -> {os.path.basename(out)} "
              f"({os.path.getsize(out)} bytes)")
    except Exception as exc:
        print(f"FAILED {name}: {exc}")

cutoff = time.time() - KEEP_DAYS * 86400
for name in os.listdir(DEST):
    path = os.path.join(DEST, name)
    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
        os.remove(path)
        print(f"pruned {name}")
