#!/bin/bash

# Cut a release.
#
# The published version is derived from the git tag by hatch-vcs at build time (see
# packages/python/pyproject.toml [tool.hatch.version]). There is no version field to bump and no
# "prep" PR: you just tag the commit you want to release, and the release-pypi workflow builds
# voice-prices==<tag> and publishes it to PyPI.
#
# This helper tags the current commit and creates a GitHub release (which pushes the tag and
# triggers the workflow).
#
# It refuses to tag a commit that is not on main's published history. A tag on a side branch
# still publishes to PyPI, but it points at a commit nobody can `git checkout v<version>` from
# main, and the only fix once the release exists is to delete the tag and retag. Pass --force to
# override deliberately (for example, tagging a hotfix branch that will never merge).

set -e

FORCE=0
VERSION=""

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -*) echo "error: unknown option '$arg'" >&2; exit 1 ;;
    *)
      if [ -n "$VERSION" ]; then
        echo "error: unexpected extra argument '$arg'" >&2
        exit 1
      fi
      VERSION="$arg"
      ;;
  esac
done

if [ -z "$VERSION" ]; then
  echo "PyPI latest version: $(curl -s https://pypi.org/pypi/voice-prices/json | jq -r '.info.version')"
  echo "Latest git tag:      $(git describe --tags --abbrev=0 2>/dev/null || echo 'none')"
  echo ""
  echo "Usage: $0 <new-version> [--force]   e.g. $0 0.0.10"
  echo "Tags the current commit as v<version> and creates a release, which publishes to PyPI."
  echo ""
  echo "  --force   tag even when HEAD is not on main's published history"
  exit 1
fi

# Strip leading "v" prefix if present
VERSION="${VERSION#v}"
TAG="v$VERSION"

if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree is not clean; commit or stash changes before releasing." >&2
  exit 1
fi

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists locally (pointing at $(git rev-parse --short "$TAG"))." >&2
  echo "       Pick a new version, or delete the tag first if the release was never published." >&2
  exit 1
fi

# The check that matters: is the commit we are about to tag reachable from the published main?
# v0.8.0 was first cut from a side branch whose content was identical to main but whose commit
# was not an ancestor of it, which needed a delete-and-retag to fix.
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
HEAD_SHA="$(git rev-parse HEAD)"

if git rev-parse -q --verify refs/remotes/origin/main >/dev/null; then
  git fetch --quiet origin main || echo "warning: could not fetch origin/main; checking against the local copy." >&2
  if ! git merge-base --is-ancestor "$HEAD_SHA" origin/main; then
    echo "error: HEAD ($(git rev-parse --short HEAD)) is not an ancestor of origin/main." >&2
    echo "       You are on '$BRANCH'. Tagging here publishes a version that main cannot reach," >&2
    echo "       so \`git checkout $TAG\` from a fresh clone lands off-history." >&2
    echo "" >&2
    if [ "$BRANCH" != "main" ]; then
      echo "       Merge the branch first, then run this from main." >&2
    else
      echo "       Push main first (or pull, if origin/main has moved ahead)." >&2
    fi
    echo "       Pass --force to tag anyway." >&2
    [ "$FORCE" = "1" ] || exit 1
    echo "warning: --force given; tagging off-history anyway." >&2
  fi
elif [ "$BRANCH" != "main" ]; then
  # No origin/main to compare against, so fall back to the branch name.
  echo "error: you are on '$BRANCH', not 'main', and origin/main is not available to check against." >&2
  echo "       Pass --force to tag anyway." >&2
  [ "$FORCE" = "1" ] || exit 1
  echo "warning: --force given; tagging from '$BRANCH' anyway." >&2
fi

echo "Tagging $(git rev-parse --short HEAD) as $TAG and creating a release..."
gh release create "$TAG" --target "$HEAD_SHA" --generate-notes

echo ""
echo "Done. The release-pypi workflow will build voice-prices==$VERSION from $TAG and publish to PyPI."
echo "Confirm it actually uploaded (a green tick is not enough; v0.4.0 was tagged but never published):"
echo "  gh run list --workflow=release-pypi --limit 1"
