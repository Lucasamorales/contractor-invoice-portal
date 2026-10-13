#!/usr/bin/env bash
# Runs every suite with coverage and writes one LCOV report for the whole repo.
#
#   scripts/coverage.sh
#
# Output: coverage/lcov.info, with paths relative to the repo root. It holds the
# Python app (every file under app/, imported by a test or not) and the browser
# scripts in app/static. The vendored htmx.min.js is not ours and is not in it.
#
# The integration tests need a Postgres to talk to and skip without one, which
# would understate the figure, so this refuses to run unless
# PORTAL_TEST_DATABASE_URL is set. docs/setup.md §2.1 has the container.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -z "${PORTAL_TEST_DATABASE_URL:-}" ]; then
  echo "PORTAL_TEST_DATABASE_URL is not set; the integration tests would skip." >&2
  exit 2
fi

mkdir -p coverage
rm -f coverage/python.lcov coverage/js.lcov coverage/lcov.info

# pytest-cov is fetched for this run only, so the project's own dependency
# list and lock file stay exactly what production installs from.
uv run --frozen --with pytest-cov pytest \
  --cov=app --cov-report=lcov:coverage/python.lcov --cov-report=term

[ -d node_modules ] || npm ci
npm run --silent coverage

# One file, forward slashes, whatever machine produced the parts.
cat coverage/python.lcov coverage/js.lcov | tr '\\' '/' > coverage/lcov.info
rm -f coverage/python.lcov coverage/js.lcov

files=$(grep -c '^SF:' coverage/lcov.info)
echo "coverage/lcov.info written: ${files} files"
