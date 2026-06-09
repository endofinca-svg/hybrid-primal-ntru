#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
mkdir -p results/raw_logs

# BAT-1024 representative run.
sage -python -u run_estimator.sage \
  -n 1024 -q 769 \
  -e gaussian --param_e 0.659 \
  -s gaussian --param_s 0.659 \
  --use_pattern \
  --shape_model zgsa \
  --cost_model MATZOV \
  --kappa_max 100 \
  --kpat_max 130 \
  --verbose \
  2>&1 | tee results/raw_logs/BAT1024_zgsa_matzov.txt

# TODO: add GSA and the remaining cost models and BAT parameter sets.
