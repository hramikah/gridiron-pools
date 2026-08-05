"""Production entrypoint: serves the app on the local network (and, once
Tailscale is set up, to any device signed into your tailnet) using waitress
-- a real multi-threaded WSGI server, unlike Flask's single-threaded debug
server in app.py. No debugger, safe to leave reachable by other devices.

Run with:
    source venv/bin/activate && python3 serve.py
or via start_gridiron_pools.sh.
"""

import logging

from waitress import serve

from app import app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    serve(app, host="0.0.0.0", port=8090, threads=8)
