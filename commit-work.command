#!/bin/bash
# Commit the 2026-08-23 work and push it to GitHub.
# Skips the *.bak* safety copies. Double-click to run.
cd "$(dirname "$0")" || exit 1
echo "=== Committing Gridiron work ==="
echo

# Clear a stale lock left by a crashed git process (harmless if absent).
if [ -f .git/index.lock ] && [ -z "$(pgrep -x git)" ]; then
  echo "Clearing a stale .git/index.lock ..."
  rm -f .git/index.lock
fi

grep -qxF '*.bak*' .gitignore 2>/dev/null || echo '*.bak*' >> .gitignore

git add -A -- ':!*.bak*'
git add .gitignore

if git diff --cached --quiet; then
  echo "Nothing to commit - already up to date."
  read -n1 -s -p "Press any key to close..."; echo; exit 0
fi

echo "About to commit:"
git diff --cached --name-status
echo

git commit -m "08-23 session: Thursday demo seeder with college games, site-wide
simulation banner, rewritten login notice, buy-back billing on the Payments
page, Change-pick modal scroll fix, 'Locked - Kickoff too soon.' wording,
restart-site.command case fix, demo/real database switch scripts,
publishweek pause and resume launchers. Ignore *.bak* safety copies." || {
  echo "COMMIT FAILED."; read -n1 -s -p "Press any key..."; echo; exit 1; }

echo
echo "Pushing to GitHub..."
if git push origin main; then
  echo
  echo "DONE - pushed to origin/main."
else
  echo
  echo "Commit succeeded but the PUSH FAILED (probably a GitHub key issue)."
  echo "Your work is safe in git locally. Tell Claude."
fi
echo
read -n1 -s -p "Press any key to close this window..."
echo
