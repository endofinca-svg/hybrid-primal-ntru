# Code Organization

## Current estimator

- `run_estimator.sage` is the unified command-line entry point.
- `hybrid_estimator/*.sage` contains the modular estimator implementation.

The module directory was renamed from `hybrid estimator/` to `hybrid_estimator/`. The `load(...)` paths in `run_estimator.sage` were updated accordingly; estimator algorithms were not changed.

## Current dependencies

- `batchCVP.py`
- `proba_utils.sage`
- `utils.sage`

## Archived code

- `archive/legacy_estimators/` contains the old monolithic estimator.
- `archive/scheme_specific_old/` contains old DAWN, BAT, and NEV scheme-specific estimators.
- `archive/generated_sage_py/` contains Python files generated from Sage sources.
- `archive/old_figures/` contains historical figures and draft plots.

## Scripts

- `scripts/experiments/` contains reproducible commands based on `run_estimator.sage`.
- `scripts/plots/` contains historical plotting scripts. Some retain project-root-relative input paths.

All future scheme estimates should use `run_estimator.sage`.
