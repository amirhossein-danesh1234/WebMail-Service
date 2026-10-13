#!/usr/bin/env sh
# Fetch upstream code into ignored directories for inspection; no vendoring.
set -eu
cd "$(dirname "$0")/.."
mkdir -p .upstream
fetch() {
  name="$1" url="$2" tag="$3" expected="$4"
  if [ -e ".upstream/$name/.git" ]; then
    echo "Source already exists: .upstream/$name; remove it to re-fetch" >&2
    return 1
  fi
  git clone --quiet --depth 1 --branch "$tag" "$url" ".upstream/$name"
  actual="$(git -C ".upstream/$name" rev-parse HEAD)"
  if [ "$actual" != "$expected" ]; then
    echo "COMMIT MISMATCH for $name ($actual != $expected)" >&2
    exit 1
  fi
  printf '%s verified at %s\n' "$name" "$actual"
}
fetch stalwart https://github.com/stalwartlabs/stalwart.git v0.16.26 \
  18f4e9505027d790fd7e9fdad7f922be6719101d
fetch bulwark https://github.com/bulwarkmail/webmail.git 1.14.2 \
  5f2d26f2e1902b3010415f478615765299c6fd31
