# Gridiron Pools — local test site

A complete copy of the site that runs on your own machine and cannot reach or
change gridironinvestment.com. It runs on macOS and on Windows.

## Start it

**macOS** — double-click **`start-testbed.command`** in Finder.

**Windows** — double-click **`start-testbed.bat`** in Explorer.

(First run takes about a minute while it builds a Python environment; after
that it's a few seconds.)

Then open **http://127.0.0.1:8090** and log in as:

```
admin / changeme123
```

Stop it with Ctrl-C in the Terminal window that opened.

If macOS blocks the file the first time — right-click it → Open → Open. That
only happens once.

On Windows, if SmartScreen warns about the `.bat`, choose **More info → Run
anyway**. If it says Python isn't found, install it from
[python.org](https://www.python.org/downloads/windows/) and tick **"Add
python.exe to PATH"** during setup — that checkbox is what the script looks
for.

## What's loaded

Nothing but the essentials: 32 NFL teams, the Loser Pool point values, and the
one admin account. No weeks, no games, no players. You build it from the admin
side, which is the point — it exercises the Week Manager and Pool Manager the
same way a real season start does.

A reasonable first pass:

1. **Admin → Settings** — set the season's Week 1 Thursday.
2. **Admin → Week Manager → Create Weeks 1-18** — builds the whole regular
   season across all three pools at once. (Creating weeks one at a time lets
   them drift between pools, and a gap breaks the buy-back, which needs the
   following week to exist.)
3. **Admin → Week Manager** — add games to a week. One game added here
   populates all three pools.
4. **Admin → Invite Players** — or register accounts directly; the first
   account on a fresh database registers freely and becomes admin, and after
   that it's invite-only.
5. **Admin → Pool Manager → [week]** — enter scores. Scoring runs immediately.

To test the new Gridiron rules specifically:

- **Missed week**: let a Gridiron week's deadline pass with no picks from an
  entry, then load Admin → Pool Manager. That's what applies the penalties.
  The entry should show 0-5 for that week and get 8 picks the next week.
- **Two missed weeks in a row**: should total 10 losses, not 15.
- **The $100 buy-back**: Gridiron Week 2 opens buy-backs automatically when the
  week is created, so there's nothing to tick. The offer appears on the
  Gridiron pick page for any entry while Week 2 is current and still open. It
  voids Week 1 outright and grants a 10-pick catch-up slate for Week 2 -- the
  5 games the fee erased plus the 5 that week is worth -- so a bought-back
  entry ends Week 2 level on games played with everyone who never missed. (Drop Dead weeks 1-4 open automatically too; preseason
  weeks always start closed and are opened by hand from the Pool Manager.)

## Start over

Double-click **`reset-testbed.command`**. It moves the current database aside
to `testbed/pools.db.previous` and seeds a clean one.

## Why it can't touch the live site

- **Separate database.** The launcher points the app at `testbed/pools.db`
  inside this folder. The live database lives in `instance/` on the droplet and
  is never opened.
- **The wipe scripts fail closed.** `simulate_full_season.py`,
  `advance_week.py`, `seed_buyback_scenario.py` and `seed_advance_week3.py`
  each delete every week, game, entry, pick and missed-week row before they
  seed. They now refuse to run unless the database path is marked as a testbed
  one (`testbed_guard.py`). The old check only refused paths containing
  `/root/`, which is the droplet — on this Mac it would have refused nothing.
- **No API keys.** The Odds API key and SendGrid credentials live in the
  `Setting` table, which starts empty here. So it won't call The Odds API
  (no credits burned, no real lines pulled) and won't send email — messages are
  appended to `logs/emails.log` instead, which is where you can read password
  reset links and invite links during testing.
- **No network deploy path.** Nothing here pushes, pulls or syncs to the live
  server.

The red **local test site** banner across the top of every page is the visual
confirmation. It keys off the hostname being `127.0.0.1`/`localhost`, so it can
never appear on the real site and never fails to appear here.

## What's in this copy that isn't live yet

This is the `testing` branch plus the Gridiron miss/buy-back work, which is
why it's worth testing:

- **Gridiron miss accounting fixed.** A week sat out costs a flat 0-5 even when
  it lands on a makeup week. Two misses in a row now cost 10, not 15.
- **Benched entries freeze.** A benched entry's record stops at the benching
  week instead of collecting 5 more losses every week to Week 18.
- **$100 Week-2 buy-back**, replacing the old free "start over" (which could
  never actually submit — its buttons were inside a nested `<form>`).
- **Drop Dead buy-back window** moved to the week *after* the elimination, with
  a confirmation prompt.
- **Bye teams rejected** in Drop Dead and Loser rather than just hidden.
- **Standings rework**: All Weeks tab first, a "Week N W-L-T" column, no
  per-player weekly pick rows.
- **Create Weeks 1-18** in the Week Manager.

## Running the tests

```
venv/bin/python -m pytest tests/
```

34 tests covering the Gridiron miss/makeup/buy-back accounting and the spread
and over/under math. They use an in-memory database and never open
`testbed/pools.db`.

## Known rough edge

Login works at `http://127.0.0.1:8090` because browsers treat localhost as a
secure origin. It will **silently fail** if you reach this site from another
device on your network by IP, because `SESSION_COOKIE_SECURE` is hardcoded on
and the cookie is dropped over plain HTTP. That's a real quirk of the app, not
of this setup. Say the word if you want to test from your phone and I'll deal
with it.
