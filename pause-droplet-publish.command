#!/bin/bash
# ---------------------------------------------------------------------------
# Stop the LIVE site's Thursday publish job.
#
# The publisher only skips a game it can still SEE in the week. A game you
# deleted is not there any more, so the next run happily adds it back. If you
# have pruned the college slate by hand, the Thursday job will undo it unless
# this is off.
#
# Scores are untouched -- gridiron-update-scores keeps running, so results
# still fill in.
#
# ** Turn it back on with resume-droplet-publish.command, or next Thursday's
#    week will not publish itself. **
#
# The old pause-publishweek.command targets a launchd agent on this Mac from
# before the droplet existed. It does nothing to the live site.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== Pausing the live Thursday publish job ==="
echo
if [ ! -f "$KEY" ]; then
  echo "Missing $KEY -- cannot reach the droplet."
  read -n1 -s -p "Press any key to close..."; echo; exit 1
fi

ssh -i "$KEY" $SSHOPTS "$HOST" '
  echo "before:"
  systemctl is-enabled gridiron-publish-week.timer 2>&1 | sed "s/^/  enabled: /"
  systemctl is-active  gridiron-publish-week.timer 2>&1 | sed "s/^/  active:  /"
  systemctl list-timers gridiron-publish-week.timer --no-pager 2>/dev/null | sed -n "2p" | sed "s/^/  next:    /"
  echo
  systemctl stop gridiron-publish-week.timer
  systemctl disable gridiron-publish-week.timer 2>&1 | sed "s/^/  /"
  echo
  echo "after:"
  systemctl is-enabled gridiron-publish-week.timer 2>&1 | sed "s/^/  enabled: /"
  systemctl is-active  gridiron-publish-week.timer 2>&1 | sed "s/^/  active:  /"
  echo
  echo "still running (scores keep filling in):"
  systemctl is-active gridiron-update-scores.timer 2>&1 | sed "s/^/  gridiron-update-scores.timer: /"
'
echo
echo "*******************************************************************"
echo "  The Thursday publish job is OFF. Week 2 will NOT appear on its"
echo "  own next Thursday until you run resume-droplet-publish.command."
echo "*******************************************************************"
echo
read -n1 -s -p "Press any key to close this window..."
echo
