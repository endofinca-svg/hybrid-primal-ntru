import json
from pathlib import Path
import tempfile
import time
import unittest

from .common import Journal
from .run import window


class DeadlineRecords(unittest.TestCase):
    def test_global_and_local_deadlines(self):
        self.assertEqual(window(200, 600, 0)['work_deadline'], 795)
        self.assertEqual(window(4700, 600, 0)['work_deadline'], 4795)
        self.assertEqual(window(4700, 600, 0)['hard_deadline'], 4800)

    def test_unfinished_stage_stays_null(self):
        with tempfile.TemporaryDirectory() as output:
            journal = Journal(output, {'work_deadline': time.monotonic()+10})
            with self.assertRaises(RuntimeError):
                with journal.stage('numeric'):
                    raise RuntimeError('simulated interrupted backend')
            record = json.loads((Path(output)/'result.json').read_text())
            self.assertIsNone(record['timings']['numeric'])
            self.assertEqual(record['current_stage'], 'numeric')

    def test_expired_stage_never_starts(self):
        with tempfile.TemporaryDirectory() as output:
            journal = Journal(output, {'work_deadline': time.monotonic()-1})
            with self.assertRaises(TimeoutError):
                with journal.stage('numeric'):
                    self.fail('must not execute')
            self.assertEqual(journal.result['timings'], {})
