#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/setup_env.sh
# One-shot script to create the .venv, install all Stage 1 deps, and allow
# the direnv config. Run this once from the repo root:
#
#   ./scripts/setup_env.sh
#
# After that, every `cd` into the repo auto-activates the environment.
# -----------------------------------------------------------------------------

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${REPO_ROOT}/.venv"

echo ""
echo "=== Lean Omega — Environment Setup ==="
echo "Repo   : ${REPO_ROOT}"
echo "Python : $($PYTHON --version)"
echo "Venv   : ${VENV_DIR}"
echo ""

# ── 1. Create venv ─────────────────────────────────────────────────────────
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "▶ Creating .venv ..."
  "$PYTHON" -m venv "${VENV_DIR}"
  echo "  Created."
else
  echo "▶ .venv already exists — skipping creation."
fi

# ── 2. Activate ────────────────────────────────────────────────────────────
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
echo "▶ Activated: $(which python) ($(python --version))"

# ── 3. Upgrade pip + install build tools ───────────────────────────────────
# Unset corporate mirror env vars so we install from PyPI directly.
# Add your nexus credentials to .env.local if internal packages are needed.
echo "▶ Upgrading pip / setuptools / wheel ..."
PIP_EXTRA_INDEX_URL="" pip install --upgrade pip setuptools wheel -q

# ── 4. Install project in editable mode with Stage 1 + dev deps ────────────
echo "▶ Installing lean-omega (Stage 1 + dev extras) ..."
PIP_EXTRA_INDEX_URL="" pip install -e ".[dev]" -q
echo "  Done."

# ── 5. Allow direnv ────────────────────────────────────────────────────────
if command -v direnv &>/dev/null; then
  echo "▶ Running: direnv allow ..."
  direnv allow "${REPO_ROOT}"
else
  echo "  (direnv not found in PATH — skipping 'direnv allow')"
fi

# ── 6. Create .env.local if missing ────────────────────────────────────────
ENV_LOCAL="${REPO_ROOT}/.env.local"
if [[ ! -f "${ENV_LOCAL}" ]]; then
  echo "▶ Creating .env.local template ..."
  cat > "${ENV_LOCAL}" <<'EOF'
# .env.local — local secrets, never committed to git
# Uncomment and fill in as stages progress.

# Stage 4: LLM provider key
# DEEPSEEK_API_KEY=your_key_here

# Stage 6: Zep / Graphiti
# ZEP_API_KEY=your_key_here
# ZEP_API_URL=https://api.getzep.com

# Stage 7: Temporal
# TEMPORAL_HOST=localhost:7233
# TEMPORAL_NAMESPACE=default

# Stage 8: Redis
# REDIS_URL=redis://localhost:6379/0
EOF
  echo "  Created ${ENV_LOCAL}"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "  Next steps:"
echo "    1.  cd ${REPO_ROOT}   (direnv will auto-activate .venv)"
echo "    2.  python main.py --goal config/goals/example_goal.yaml --mode local"
echo "    3.  pytest tests/ -v"
echo ""
