import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class AccessGuard(unittest.TestCase):
    def test_exact_traced_worker_boundary_and_cleanup(self):
        from experiments.ntru_hybrid_comparison.supervisor import supervise_command
        from .run import access_check
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            argv = ['/usr/bin/strace', '-f', '-qq', '-e', 'trace=open,openat,openat2',
                    '-o', str(output/'file_opens.log'), sys.executable, '-B',
                    str(Path(__file__).with_name('audit_entry.py')), '--audit-log',
                    str(output/'file_access.jsonl'), 'json.tool', '--help']
            tick = time.monotonic()
            record = supervise_command(argv, work_deadline=tick+5, hard_deadline=tick+10,
                       stdout_path=output/'stdout.txt', stderr_path=output/'stderr.txt',
                       ownership='process_group', env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})
            self.assertEqual(record['execution_status'], 'completed')
            self.assertEqual(record['returncode'], 0, (output/'stderr.txt').read_text())
            self.assertTrue(record['cleanup_verified'])
            self.assertTrue(access_check(output)['all_checks_passed'], access_check(output))

    def test_forbidden_file_and_symlink_blocked_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            # Synthetic test content; no repository secret file is accessed.
            forbidden = stage / 'private.fixture'
            forbidden.write_text('synthetic sentinel')
            link = stage / 'innocent.fixture'
            link.symlink_to(forbidden)
            script = '''import json,sys
from experiments.ntru_hybrid_comparison.stage3_fixed_basis_diagnostics_20261028.audit_entry import install
install(sys.argv[1])
for path in sys.argv[2:]:
 try:
  open(path).read()
 except PermissionError:
  pass
 else:
  raise AssertionError("forbidden content read")
'''
            completed = subprocess.run([sys.executable, '-B', '-c', script, str(stage/'opens.jsonl'),
                                        str(forbidden), str(link)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rows = [json.loads(s) for s in (stage/'opens.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r['denied'] for r in rows))

    def test_allowed_file_read_is_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            allowed = stage/'public.fixture'
            allowed.write_text('public sentinel')
            script = '''import sys
from experiments.ntru_hybrid_comparison.stage3_fixed_basis_diagnostics_20261028.audit_entry import install
install(sys.argv[1])
assert open(sys.argv[2]).read()=="public sentinel"
'''
            completed = subprocess.run([sys.executable, '-B', '-c', script, str(stage/'opens.jsonl'),
                                        str(allowed)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rows = [json.loads(s) for s in (stage/'opens.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]['denied'])
