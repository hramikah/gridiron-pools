"""The check every destructive local script runs before it touches anything.

The simulators and scenario seeds all delete every Week, Game, Entry, Pick
and GridironMiss row before they seed. Their original guard refused only
when the database path contained "/root/" -- which is where the droplet's
checkout happens to live. On any other machine, including a laptop holding
a copy of the live database, that check refused nothing at all.

This one works the opposite way round: refuse everything unless the database
is explicitly a testbed one. start-testbed.command points
GRIDIRON_DATABASE_URI at <repo>/testbed/pools.db, so the marker is present
exactly when a script is aimed at a throwaway database, and absent every
other time -- including the default instance/pools.db.

Fails closed. A script that forgets to call this is the only way past it.
"""

TESTBED_MARKER = "testbed"

_REFUSAL = """
{script} refuses to run.

It deletes every week, game, entry, pick and missed-week row before seeding,
so it will only touch a database whose path is marked as a testbed one.

  configured database: {uri}
  required: the path must contain "{marker}"

Start the test site with ./start-testbed.command (which sets
GRIDIRON_DATABASE_URI for you), then run this script from the same shell --
or set it yourself:

  export GRIDIRON_DATABASE_URI="sqlite:///$(pwd)/testbed/pools.db"

If you meant to run this against the live site: don't. Take a backup first
and do it by hand.
"""


def require_testbed_database(app, script_name=None):
    """Abort unless this app is pointed at a testbed database.

    Returns the URI so a caller can log what it's about to write to.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if TESTBED_MARKER not in uri.lower():
        raise SystemExit(
            _REFUSAL.format(
                script=script_name or "This script",
                uri=uri or "(none configured)",
                marker=TESTBED_MARKER,
            )
        )
    return uri
