# Stage 3 Git package

This package preserves the fixed-basis coverage, stopping-condition, and Slicer
iteration diagnosis recorded in `REPORT.md`. It includes the saved matrices,
10,400 raw target telemetry records, experiment results, command and provenance
records, build and execution logs, failure and timeout evidence, source patches,
and all 321 files bound by `artifact_manifest.json`. Uploading this package does
not run new numerical experiments or change the recorded results.

The full local `diagnostic_g6k/` and `diagnostic_g6k.build_failed_*/` source/build
copies, compiled libraries, and caches are excluded from Git. The two ephemeral
guard-test paths `preflight_access_v2/guard_denial/synthetic_private/` and
`preflight_access_v2/guard_denial/alias.dat` are also excluded; the guard-test logs
are retained. These exclusions leave the local files untouched. The unrelated
repository-root `main.pdf` is outside this package.

Reconstruction inputs are retained in `build_diagnostic.py`,
`diagnostic_backend.patch`, and `backend_original/`. The existing baseline
bootstrap is `../../ntru_e2e/bootstrap.py`; the pinned upstream G6K revision is
`bd72687b2ebbaff16fb692259f83b327bbcd9586`. The upstream license is included as
`backend_original/UPSTREAM_LICENSE`. Runtime dependencies and toolchain details
are recorded in the provenance evidence. Absolute paths in `sources.json` and
other runtime records describe the historical machine and must be adapted on
another machine. Native binaries are not shipped, and rebuilding with a different
toolchain is not expected to reproduce their binary hashes.

The manifest and original experimental evidence remain unchanged. The packaging
files `.gitignore`, `GIT_PACKAGE.md`, and `backend_original/UPSTREAM_LICENSE` are
additional distribution metadata and are not part of the frozen manifest.
