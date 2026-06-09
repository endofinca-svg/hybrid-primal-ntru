#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
mkdir -p results/raw_logs

# NEV-1024 representative run.
sage -python -u run_estimator.sage \
  -n 1024 -q 769 \
  -e binomial --param_e 1 \
  -s binomial --param_s 1 \
  --use_pattern \
  --shape_model zgsa \
  --cost_model MATZOV \
  --kappa_max 100 \
  --kpat_max 40 \
  --verbose \
  2>&1 | tee results/raw_logs/NEV1024_zgsa_matzov.txt

# TODO: add GSA and the remaining cost models and NEV parameter sets.
