#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Create the environment first: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt' >&2
  exit 1
fi
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
# CPU-only is the safe Linux default; native GPU integration is separately configured.
export STUDIO_CPU_ONLY="${STUDIO_CPU_ONLY:-true}"
exec nice -n 10 .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port "${STUDIO_PORT:-8790}" --no-access-log
