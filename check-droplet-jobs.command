#!/bin/bash
# ---------------------------------------------------------------------------
# Ask the live droplet what is scheduled to run, and when it last ran.
#
# The weekly line publish and the score pulls only happen automatically if
# something on the droplet is scheduled to run them. On the Mac those were
# launchd jobs; they were switched off when the site moved. This reports what
# actually exists there now. It changes nothing.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
chmod 600 "$KEY" 2>/dev/null

echo "=== What the droplet has scheduled ==="
echo
ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes "$HOST" '
  echo "--- root crontab ---"
  crontab -l 2>/dev/null || echo "(no crontab for root)"
  echo
  echo "--- systemd timers ---"
  systemctl list-timers --all --no-pager | grep -i "gridiron\|publish\|scores\|NEXT" || echo "(no gridiron timers)"
  echo
  echo "--- gridiron services ---"
  systemctl list-units --type=service --all --no-pager | grep -i gridiron || true
  echo
  echo "--- last publish / score runs (if they log) ---"
  ls -l /root/gridiron-pools/logs/ 2>/dev/null | tail -10 || echo "(no logs directory)"
  echo
  echo "--- email dry-run log (if this is growing, mail is NOT being delivered) ---"
  ls -l /root/gridiron-pools/logs/emails.log 2>/dev/null || echo "(no emails.log -- good sign)"
'
echo
read -n1 -s -p "Press any key to close this window..."
echo
