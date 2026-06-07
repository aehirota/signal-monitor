#!/usr/bin/env bash
# Wrapper invoked by launchd. Activates the venv, loads .env, runs the agent.
# launchd does NOT inherit the user's shell env, so we source .env explicitly.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd)"

cd "$PROJECT_DIR"

# Activate venv.
# shellcheck source=/dev/null
source "$PROJECT_DIR/.venv/bin/activate"

# Load env vars (RESEND_API_KEY, EXA_API_KEY, etc.) from .env.
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +a
fi

exec python run.py
