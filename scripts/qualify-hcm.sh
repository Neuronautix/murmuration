#!/usr/bin/env sh
# Manual/offline cross-repository qualification against the pinned private
# OntoHistoCurator commit in constraints/ontohistoria-qualification.txt.
# Needs your own read access to Neuronautix/OntoHistoCurator (git credentials).
# Installs it only into .venv-qualification; see qualification/README.md.
set -eu
PYTHON="${PYTHON:-python3}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-qualification"
"$PYTHON" -m venv "$VENV"
PY="$VENV/bin/python"
"$PY" -m pip install -r "$ROOT/constraints/ontohistoria-qualification.txt"
"$PY" -m pip install -r "$ROOT/constraints/test-langchain.txt"
"$PY" -m pip install pytest
cd "$ROOT"
RESEARCH_SWARM_REQUIRE_QUALIFICATION=1 "$PY" -m pytest -q -rs qualification
