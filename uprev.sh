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

set -e

if [ "$#" -ne 1 ]; then
  echo "PyPI latest version: $(curl -s https://pypi.org/pypi/voice-prices/json | jq -r '.info.version')"
  echo "Latest git tag:      $(git describe --tags --abbrev=0 2>/dev/null || echo 'none')"
  echo ""
  echo "Usage: $0 <new-version>   e.g. $0 0.0.10"
  echo "Tags the current commit as v<version> and creates a release, which publishes to PyPI."
  exit 1
fi

# Strip leading "v" prefix if present
VERSION="${1#v}"
TAG="v$VERSION"

if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree is not clean; commit or stash changes before releasing." >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "warning: you are on '$BRANCH', not 'main'. Releases are normally cut from main." >&2
fi

echo "Tagging $(git rev-parse --short HEAD) as $TAG and creating a release..."
gh release create "$TAG" --target "$(git rev-parse HEAD)" --generate-notes

echo ""
echo "Done. The release-pypi workflow will build voice-prices==$VERSION from $TAG and publish to PyPI."
