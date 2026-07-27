#!/usr/bin/env bash
set -e
VERSION="$1"
[ -z "$VERSION" ] && { echo "Usage: release.sh vX.Y.Z"; exit 1; }

git checkout main
git merge --ff-only dev
git tag -a "$VERSION" -m "$VERSION"
git push origin main --tags
echo "🎉 Released $VERSION"
