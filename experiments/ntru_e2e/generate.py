"""Generate separate public instances and private evaluation records.

Run with ``python -m experiments.ntru_e2e.generate --output-dir DIR``.
Only files below ``DIR/public`` are inputs to the recovery program.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random

from .algebra import inverse_mod, negacyclic_mul


PUBLIC_SCHEMA = "ntru-e2e-public-v1"
TRUTH_SCHEMA = "ntru-e2e-truth-v1"


def _ternary(rng: random.Random, n: int, weight: int) -> list[int]:
    value = [0] * n
    for index in rng.sample(range(n), weight):
        value[index] = rng.choice((-1, 1))
    return value


def generate_instance(
    n: int,
    q: int,
    w_f: int,
    w_g: int,
    seed: int,
    instance_id: str,
    max_attempts: int = 128,
) -> tuple[dict, dict]:
    """Return a public record and its separate, offline-only truth record.

    Rejection is restricted to a noninvertible ``f``.  In particular, no
    pattern, tail, lattice shape, or recovery outcome influences key selection.
    """
    if n <= 0 or q <= 2:
        raise ValueError("n must be positive and q must exceed two")
    if not 0 < w_f <= n or not 0 <= w_g <= n:
        raise ValueError("require 0 < w_f <= n and 0 <= w_g <= n")
    if not 1 <= max_attempts <= 128:
        raise ValueError("max_attempts must lie between one and 128")
    rng = random.Random(seed)
    for attempts in range(1, max_attempts + 1):
        f = _ternary(rng, n, w_f)
        try:
            f_inverse = inverse_mod(f, q)
        except ValueError:
            continue
        break
    else:
        raise ValueError(f"no invertible f after {max_attempts} keygen attempts")
    g = _ternary(rng, n, w_g)
    h = [int(value) % q for value in negacyclic_mul(f_inverse, g, q)]
    public = {
        "schema": PUBLIC_SCHEMA,
        "instance_id": instance_id,
        "n": n,
        "q": q,
        "ring": "x^n+1",
        "h": h,
        "distribution": {
            "kind": "fixed_weight_ternary",
            "w_f": w_f,
            "w_g": w_g,
        },
    }
    truth = {
        "schema": TRUTH_SCHEMA,
        "instance_id": instance_id,
        "keygen_seed": seed,
        "f": f,
        "g": g,
        "keygen_attempts": attempts,
    }
    return public, truth


def generate_instances(
    output_dir: str | Path,
    *,
    n: int = 32,
    q: int = 257,
    w_f: int = 2,
    w_g: int = 4,
    seeds: list[int] | tuple[int, ...] = (1, 2, 3),
) -> list[dict[str, str]]:
    """Save reproducible instances with no truth fields in public JSON."""
    output_dir = Path(output_dir)
    public_dir = output_dir / "public"
    private_dir = output_dir / "private"
    public_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Check the whole requested set before writing any record.  A continuation
    # must preserve prior instances and their separately stored ground truth.
    for index in range(1, len(seeds) + 1):
        instance_id = f"instance_{index:02d}"
        for path in (public_dir / f"{instance_id}.json",
                     private_dir / f"{instance_id}.truth.json"):
            if path.exists():
                raise FileExistsError(f"Preserving existing instance file: {path}")
    records = []
    for index, seed in enumerate(seeds, start=1):
        instance_id = f"instance_{index:02d}"
        public, truth = generate_instance(n, q, w_f, w_g, seed, instance_id)
        public_path = public_dir / f"{instance_id}.json"
        truth_path = private_dir / f"{instance_id}.truth.json"
        public_bytes = (json.dumps(public, indent=2, sort_keys=True) + "\n").encode()
        truth["public_sha256"] = hashlib.sha256(public_bytes).hexdigest()
        public_path.write_bytes(public_bytes)
        truth_path.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
        truth_path.chmod(0o600)
        records.append({"public": str(public_path), "truth": str(truth_path)})
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--n", type=int, default=32)
    parser.add_argument("--q", type=int, default=257)
    parser.add_argument("--wf", "--w-f", dest="w_f", type=int, default=2)
    parser.add_argument("--wg", "--w-g", dest="w_g", type=int, default=4)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    args = parser.parse_args(argv)
    try:
        records = generate_instances(
            args.output_dir,
            n=args.n,
            q=args.q,
            w_f=args.w_f,
            w_g=args.w_g,
            seeds=args.seeds,
        )
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({"generated": records}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
