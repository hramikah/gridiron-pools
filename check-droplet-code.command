#!/bin/bash
# ---------------------------------------------------------------------------
# READ-ONLY. Asks the live droplet what code it is actually running and prints
# it. Changes nothing: no pull, no restart, no database. Double-click it.
#
# Why: "Already up to date" from a deploy is ambiguous -- it can mean "the
# code is there" or "this checkout is pointed somewhere that will never see
# your commit". These four lines tell the two apart.
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")" || exit 1
export GIT_PAGER=cat
KEY=".deploy/droplet_key"
HOST="root@159.223.111.72"
SSHOPTS="-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
chmod 600 "$KEY" 2>/dev/null

echo "=== What this Mac has ==="
git --no-pager log --oneline -1
echo "branch: $(git rev-parse --abbrev-ref HEAD)   remote: $(git remote get-url origin)"
echo "pushed to origin? $(git --no-pager log --oneline -1 origin/$(git rev-parse --abbrev-ref HEAD) 2>/dev/null || echo 'no origin ref')"
echo
echo "=== Asking GitHub what it has ==="
git ls-remote origin "$(git rev-parse --abbrev-ref HEAD)" 2>&1 | head -2
echo

[ -f "$KEY" ] || { echo "Missing $KEY -- cannot reach the droplet."; read -n1 -s -p "Press any key..."; echo; exit 1; }

echo "=== What the droplet has ==="
ssh -i "$KEY" $SSHOPTS "$HOST" '
  cd /root/gridiron-pools || { echo "NO /root/gridiron-pools ON THE DROPLET"; exit 1; }
  echo "HEAD    : $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"
  echo "branch  : $(git rev-parse --abbrev-ref HEAD)"
  echo "remote  : $(git remote get-url origin)"
  echo "upstream: $(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo NONE)"
  echo "dirty   : $(git status --porcelain | wc -l) uncommitted file(s)"
  echo
  echo "service : $(systemctl is-active gridiron-server)"
  echo "workdir : $(systemctl show gridiron-server -p WorkingDirectory --value)"
  echo "started : $(systemctl show gridiron-server -p ActiveEnterTimestamp --value)"
  echo
  echo "does the running code have the entry counts?"
  grep -c "n_total" blueprints/admin.py templates/admin/payments.html 2>/dev/null || echo "  (files not found)"
'
echo
echo "Copy everything above and show it to Claude."
read -n1 -s -p "Press any key to close this window..."
echo
