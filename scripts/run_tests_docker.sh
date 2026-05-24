#!/usr/bin/env bash
# Run the test suite inside the docker-compose `test-runner` container.
#
# Why this exists: the Windows host runs into POSIX-specific test
# failures (file_permissions tests, /mnt/c paths in acp_images,
# tilde-expansion in cron_workdir) that are noise — they're not real
# bugs, just host-environment leakage. Running the suite inside a
# Linux container against the dedicated postgres-test instance gives
# us clean signal that matches CI.
#
# Usage:
#     scripts/run_tests_docker.sh                           # full suite
#     scripts/run_tests_docker.sh tests/substrate/          # subset
#     scripts/run_tests_docker.sh tests/foo.py -- -v        # path + pytest args
#     scripts/run_tests_docker.sh -- -k 'something'         # pytest args only
#
# Args before `--` are passed as discovery roots to the parallel
# runner. Args after `--` are passed through to each per-file pytest
# invocation.
#
# First run will build the test-runner image (~3 min). Subsequent runs
# reuse the cached image; only the source is re-mounted.

set -euo pipefail

# ── Bring up the test PG ──────────────────────────────────────────────
# Idempotent. The image is pulled (not built) so this is fast even on
# a cold machine.
docker compose --profile test up -d postgres-test

# Wait for PG to be ready. The healthcheck in docker-compose.yml is
# `pg_isready`, which compose tracks via `depends_on.condition`. The
# test-runner service has that dep, but we belt-and-suspenders here
# in case someone invokes the runner manually.
echo "Waiting for postgres-test to be healthy..."
until docker compose --profile test ps postgres-test --format json 2>/dev/null \
        | grep -q '"Health":"healthy"'; do
    sleep 2
done

# ── Build the runner if needed ───────────────────────────────────────
# Compose's `run` builds on demand, but doing it explicitly makes the
# first-run timing visible to the user.
docker compose --profile test build test-runner

# ── Pass-through arg parsing ─────────────────────────────────────────
# Mirror scripts/run_tests.sh: positional paths before `--`, pytest
# args after.
PATHS=()
PYTEST_ARGS=()
saw_sep=0
for arg in "$@"; do
    if [[ $saw_sep -eq 1 ]]; then
        PYTEST_ARGS+=("$arg")
    elif [[ "$arg" == "--" ]]; then
        saw_sep=1
    else
        PATHS+=("$arg")
    fi
done

# ── Run ──────────────────────────────────────────────────────────────
# `run --rm` so the container is removed when the suite exits. The
# parallel runner discovers under PATHS (or tests/ by default).
exec docker compose --profile test run --rm test-runner \
    python scripts/run_tests_parallel.py "${PATHS[@]+${PATHS[@]}}" "${PYTEST_ARGS[@]+${PYTEST_ARGS[@]}}"
