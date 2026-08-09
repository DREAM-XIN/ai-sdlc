#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/prune_merged_branches.sh [--apply] [--base <branch>] [--remote <remote>]

Lists stale remote work branches whose tip is already an ancestor of the base
branch. By default this is a dry run. Deletion requires both --apply and:

  CONFIRM_DELETE_MERGED_BRANCHES=yes

Long-lived main/release/bootstrap branches are never selected automatically.
Only known short-lived AI-SDLC work prefixes are eligible.
EOF
}

apply=false
base=main
remote=origin

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
      ;;
    --base)
      base="${2:?missing value for --base}"
      shift 2
      ;;
    --remote)
      remote="${2:?missing value for --remote}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "must run inside a Git repository" >&2
  exit 2
fi

git fetch "$remote" --prune
base_ref="refs/remotes/${remote}/${base}"
if ! git show-ref --verify --quiet "$base_ref"; then
  echo "base ref not found: $base_ref" >&2
  exit 2
fi

eligible_prefix='^(agent|chore|docs|dogfood|feat|feature|fix|gh-aw|recovery|test)/'

candidates=()
while IFS= read -r ref; do
  branch="${ref#refs/remotes/${remote}/}"
  [[ "$branch" == "HEAD" || "$branch" == "$base" ]] && continue
  [[ "$branch" =~ $eligible_prefix ]] || continue

  # Fail safe: only a branch whose current remote tip is already reachable from
  # the base branch is considered automatically deletable. Squash/rebase-only
  # historical branches intentionally remain for manual review.
  if git merge-base --is-ancestor "$ref" "$base_ref"; then
    candidates+=("$branch")
  fi
done < <(git for-each-ref --format='%(refname)' "refs/remotes/${remote}/")

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "No automatically deletable merged work branches found."
  exit 0
fi

printf 'Merged work branches eligible for deletion (%d):\n' "${#candidates[@]}"
printf '  %s\n' "${candidates[@]}"

if [[ "$apply" != true ]]; then
  cat <<'EOF'

Dry run only. Re-run with:
  CONFIRM_DELETE_MERGED_BRANCHES=yes scripts/prune_merged_branches.sh --apply
EOF
  exit 0
fi

if [[ "${CONFIRM_DELETE_MERGED_BRANCHES:-}" != "yes" ]]; then
  echo "refusing deletion: set CONFIRM_DELETE_MERGED_BRANCHES=yes" >&2
  exit 2
fi

for branch in "${candidates[@]}"; do
  echo "Deleting ${remote}/${branch}"
  git push "$remote" --delete "$branch"
done

echo "Deleted ${#candidates[@]} merged work branches."
