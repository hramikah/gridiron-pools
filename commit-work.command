#!/bin/bash
# Commit whatever is currently changed and push it to GitHub.
# The commit message is read from .commit-message.txt next to this file.
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

# The message lives in .commit-message.txt so each session commits something
# that actually describes its work. Three commits in a row once shared one
# stale hard-coded message because it did not.
MSG=".commit-message.txt"
if [ ! -s "$MSG" ]; then
  echo "No .commit-message.txt found -- write one first, or tell Claude."
  read -n1 -s -p "Press any key..."; echo; exit 1
fi
echo "Commit message:"; echo; sed 's/^/    /' "$MSG"; echo

git commit -F "$MSG" || {
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
