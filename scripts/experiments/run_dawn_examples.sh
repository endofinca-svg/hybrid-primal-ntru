#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
mkdir -p results/raw_logs

# DAWN-beta-512 representative run. Ternary parameters are fixed weights.
sage -python -u run_estimator.sage \
  -n 512 -q 257 \
  -e ternary --param_e 128 \
  -s ternary --param_s 64 \
  --use_pattern \
  --shape_model zgsa \
  --cost_model MATZOV \
  --kappa_max 100 \
  --kpat_max 130 \
  --verbose \
  2>&1 | tee results/raw_logs/DAWN_beta512_zgsa_matzov.txt

# TODO: add the corresponding GSA run and the remaining DAWN parameter sets.
