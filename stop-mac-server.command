#!/bin/bash
# Retire this Mac as a web server. The DigitalOcean droplet takes over.
# Stops the Gridiron app and the Cloudflare tunnel, and stops them starting
# again at login. Nothing is deleted - undo by re-enabling the same jobs.
cd "$(dirname "$0")" || exit 1
echo "=== Retiring the Mac as the Gridiron server ==="
echo

echo "--- launchd jobs found ---"
launchctl list | grep -iE 'gridiron|cloudflared' || echo "(none listed)"
echo

for LABEL in $(launchctl list | grep -iE 'gridiron|cloudflared' | awk '{print $3}'); do
  echo "Stopping $LABEL ..."
  launchctl bootout gui/$(id -u)/$LABEL 2>/dev/null || launchctl remove "$LABEL" 2>/dev/null
  for D in "$HOME/Library/LaunchAgents" "/Library/LaunchAgents" "/Library/LaunchDaemons"; do
    [ -f "$D/$LABEL.plist" ] && { launchctl unload -w "$D/$LABEL.plist" 2>/dev/null; echo "  disabled $D/$LABEL.plist"; }
  done
done

sleep 3
PID=$(lsof -ti tcp:8090 -sTCP:LISTEN 2>/dev/null | head -1)
[ -n "$PID" ] && { echo "Still listening on 8090 (pid $PID) - stopping it."; kill "$PID" 2>/dev/null; sleep 3; }

echo
echo "--- RESULT ---"
if lsof -ti tcp:8090 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "PORT 8090: still in use - tell Claude."
else
  echo "PORT 8090: free (the Mac is no longer serving the site)"
fi
if pgrep -x cloudflared >/dev/null 2>&1; then
  echo "TUNNEL:    cloudflared still running - tell Claude."
else
  echo "TUNNEL:    stopped"
fi
echo
echo "The site now runs only on the droplet: https://gridironinvestment.com"
read -n1 -s -p "Press any key to close this window..."
echo
