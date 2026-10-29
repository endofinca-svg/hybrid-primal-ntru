"""Shared v2 content-path policy for the Python hook and native-open audit.

Library implementation modules named _private are distinct from experiment
reference arrays.  The exact pinned NumPy modules are explicitly recorded.
"""
import ast
import json
import os
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[3]
NUMPY_PRIVATE = Path('/home/inca/miniforge3/envs/g6k/lib/python3.10/site-packages/numpy/testing/_private')
MODULE_NAMES = ('__init__', 'utils', 'extbuild')
ALLOWED_MODULE_PATHS = frozenset(str((NUMPY_PRIVATE / (name + '.py')).resolve())
                                 for name in MODULE_NAMES)
ARRAY_SUFFIXES = ('.json', '.jsonl', '.npy', '.npz', '.pkl', '.pickle', '.bin',
                  '.csv', '.txt', '.mat', '.h5', '.hdf5', '.dat')
SENSITIVE = ('private', 'truth', 'reference')
REF_TOKEN = re.compile(r'(^|[._/\\-])ref(?:erence)?(?=$|[._/\\-])', re.I)


def _inside(path, directory):
    try:
        Path(path).relative_to(directory)
        return True
    except ValueError:
        return False


def _trusted_module(real):
    if real in ALLOWED_MODULE_PATHS:
        return True
    path = Path(real)
    return (path.parent == NUMPY_PRIVATE / '__pycache__'
            and any(path.name.startswith(name + '.cpython-') for name in MODULE_NAMES)
            and path.suffix == '.pyc')


def classify_path(path, *, cwd=None):
    original = os.fsdecode(path)
    absolute = os.path.abspath(os.path.join(str(cwd or REPO), original))
    real = os.path.realpath(absolute)
    paths = (absolute, real)
    reasons = []
    module = _trusted_module(real)
    # An allowed module cannot excuse a secret-looking symlink name: both
    # names still pass repository and array checks below.
    for candidate in paths:
        lowered = candidate.lower()
        if _inside(candidate, REPO):
            relative = str(Path(candidate).relative_to(REPO)).lower()
            if any(word in relative for word in SENSITIVE):
                reasons.append('repository_private_truth_reference_content_path')
        filename = Path(candidate).name.lower()
        is_array = filename.endswith(ARRAY_SUFFIXES)
        if filename.endswith('.truth.json'):
            reasons.append('truth_json_anywhere')
        if is_array and (any(word in lowered for word in SENSITIVE) or REF_TOKEN.search(lowered)):
            reasons.append('private_truth_reference_array_anywhere')
    return {'path': original, 'absolute_path': absolute, 'realpath': real,
            'denied': bool(reasons), 'reasons': sorted(set(reasons)),
            'allowed_module': real if module and not reasons else None,
            'policy': 'repository_secret_paths_and_sensitive_arrays_v2'}


def access_check(output, *, cwd=None):
    output = Path(output)
    python_log, native_log = output / 'file_access.jsonl', output / 'file_opens.log'
    denied, native_forbidden, unresolved = [], [], []
    allowed = {}
    if python_log.exists():
        for line in python_log.read_text().splitlines():
            row = json.loads(line)
            if row.get('denied'):
                denied.append(row)
            if row.get('allowed_module'):
                allowed[row['allowed_module']] = {'path': row['path'], 'realpath': row['realpath'],
                                                 'source': 'python_audit_hook'}
    if native_log.exists():
        for line in native_log.read_text(errors='replace').splitlines():
            tokens = re.findall(r'"(?:[^"\\]|\\.)*"', line)
            if not tokens:
                continue
            try:
                name = ast.literal_eval(tokens[0])
            except (ValueError, SyntaxError):
                unresolved.append(line)
                continue
            if tokens[0] + '...' in line:
                unresolved.append(line)
                continue
            base = cwd or REPO
            if not os.path.isabs(name):
                descriptor = re.search(r'openat(?:2)?\(([^,]*),', line)
                if descriptor and 'AT_FDCWD' not in descriptor.group(1):
                    directory = re.search(r'<([^>]*)>', descriptor.group(1))
                    if not directory:
                        unresolved.append(line)
                        continue
                    base = directory.group(1)
            classified = classify_path(name, cwd=base)
            if classified['denied']:
                native_forbidden.append({'trace': line, **classified})
            if classified['allowed_module']:
                allowed.setdefault(classified['allowed_module'], {
                    'path': name, 'realpath': classified['realpath'], 'source': 'native_strace'})
    return {'schema': 'ntru-stage3-file-access-v2',
            'audit_hook_log_present': python_log.exists(), 'native_open_log_present': native_log.exists(),
            'denied_python_open_attempts': denied,
            'native_forbidden_path_open_attempts': native_forbidden,
            'unresolved_native_path_records': unresolved,
            'allowed_module_paths': [allowed[name] for name in sorted(allowed)],
            'all_checks_passed': python_log.exists() and native_log.exists()
                                 and not denied and not native_forbidden and not unresolved,
            'scope': 'current traced worker and descendants; native relative names use fixed worker cwd or -yy dirfd annotation',
            'native_symlink_limit': 'Native post-run realpath resolution cannot reconstruct a symlink removed during execution; Python hook records realpath before open.'}
