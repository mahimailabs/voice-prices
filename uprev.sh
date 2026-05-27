#!/bin/bash

set -e

if [ "$#" -ne 1 ]; then
  echo "Current version:     `uvx --from=toml-cli toml get --toml-path=packages/python/pyproject.toml project.version`"
  echo "PyPI latest version: `curl -s https://pypi.org/pypi/voice-prices/json | jq -r '.info.version'`"
  echo "Usage: $0 <new-version>"
  exit 1
fi

# Strip leading "v" prefix if present
VERSION="${1#v}"

echo "setting Python package version to $VERSION"
uvx --from=toml-cli toml set --toml-path=packages/python/pyproject.toml project.version $VERSION
make sync

git checkout -b "release/$VERSION"
echo "Switched to branch 'release/$VERSION', next run:"
echo ""
echo "git commit -am 'Prep $VERSION release' && gh pr create -f"
