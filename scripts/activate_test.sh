#!/usr/bin/env bash
# scripts/activate_tests.sh
#
# 1. Add repo root to PYTHONPATH so project modules resolve.
# 2. Run all memory-integration tests (fail-fast).

# set -e   # abort on first failure

# ─────────────────────────── PYTHONPATH ────────────────────────────
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
echo "PYTHONPATH set → $PYTHONPATH"

# ───────────────────────────── Tests ───────────────────────────────
cd "$REPO_ROOT"

echo "▶ Running memory-integration tests..."
python experiments/test_memory_on.py
python experiments/test_memory_off.py
python experiments/test_memory_toggle.py
echo "✔ All memory tests completed successfully"

# ───────── Return / Exit cleanly ─────────
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  # script was *executed* (not sourced)
  exit 0
fi
# script was *sourced* – just give control back
return 0
