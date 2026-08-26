#!/bin/bash
# ---------------------------------------------------------------------------
# Turn off the weekly picks-recap job on the droplet.
#
# The recap email was removed from the site, so email_weekly_picks.py no longer
# exists. Its systemd timer still fires every five minutes and would fail every
# time. This stops and disables the timer and the service, and shows what is
# left running afterwards.
#
# It touches nothing else: the weekly odds publish and the score checks are
# left exactly as they are.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
chmod 600 "$KEY" 2>/dev/null

echo "=== Turning off the picks-recap job ==="
echo

ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes "$HOST" '
  set -e
  systemctl stop gridiron-email-picks.timer 2>/dev/null || true
  systemctl disable gridiron-email-picks.timer 2>/dev/null || true
  systemctl stop gridiron-email-picks.service 2>/dev/null || true
  # Kept on disk rather than deleted, so it can be put back if it is ever wanted.
  for f in /etc/systemd/system/gridiron-email-picks.timer /etc/systemd/system/gridiron-email-picks.service; do
    [ -f "$f" ] && mv "$f" "$f.disabled" && echo "moved $(basename $f) aside"
  done
  systemctl daemon-reload
  systemctl reset-failed 2>/dev/null || true
  echo
  echo "Timers still scheduled:"
  systemctl list-timers --all --no-pager | grep -i "gridiron\|NEXT" || echo "(none)"
'
RC=$?
echo
if [ $RC -eq 0 ]; then
  echo "DONE. Only the odds publish and the score checks should be listed above."
else
  echo "SOMETHING FAILED (exit $RC). Copy the text above and show it to Claude."
fi
read -n1 -s -p "Press any key to close this window..."
echo
