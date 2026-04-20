#!/usr/bin/env bash
# Usage: ./scripts/open_pr.sh "PR title" "PR body"
# Opens a PR from the current branch to main and enables auto-merge.
# Requires: gh CLI authenticated, branch already pushed to origin.

set -euo pipefail

TITLE="${1:-}"
BODY="${2:-}"

if [[ -z "$TITLE" ]]; then
  echo "Usage: $0 \"PR title\" \"PR body\"" >&2
  exit 1
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "Opening PR: $TITLE ($BRANCH -> main)"
PR_URL=$(gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "$TITLE" \
  --body "$BODY")

echo "PR created: $PR_URL"

echo "Enabling auto-merge..."
gh pr merge "$PR_URL" --auto --merge

echo "Done. Will merge automatically when CI passes."
