"""Exercise rebuild safety using disposable Git checkouts, without loading G6K."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from experiments.ntru_e2e.bootstrap import apply_recorded_patch, create_record_directory


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.command_runner("init", ["git", "init", "--quiet"], self.source)
        self.file = self.source / "kernel.txt"
        self.original = "normalized target\nquantization = 128\nunchanged\n"
        self.patched = self.original.replace("128", "1048576")
        self.file.write_text(self.original)
        self.command_runner("add", ["git", "add", "kernel.txt"], self.source)
        self.command_runner("commit", ["git", "-c", "user.name=NTRU bootstrap test",
                            "-c", "user.email=bootstrap@example.invalid", "commit", "--quiet", "-m", "fixture"],
                 self.source)
        self.file.write_text(self.patched)
        self.patch = self.root / "upstream.patch"
        self.patch.write_text(self.command_runner("diff", ["git", "diff", "--binary"], self.source))
        self.file.write_text(self.original)

    def command_runner(self, label, command, cwd=None, timeout=None, env_update=None):
        return subprocess.check_output(command, cwd=cwd, timeout=timeout, text=True,
                                       env=dict(os.environ, **(env_update or {})), stderr=subprocess.PIPE)

    def test_patch_reconstructs_checkout_and_rerun_preserves_real_index(self):
        original_index = (self.source / ".git" / "index").read_bytes()
        original_patch = self.patch.read_bytes()
        self.assertEqual(apply_recorded_patch(self.source, self.patch, self.command_runner), "applied")
        self.assertEqual(self.file.read_text(), self.patched)
        self.assertEqual(apply_recorded_patch(self.source, self.patch, self.command_runner), "already_applied")
        self.assertEqual((self.source / ".git" / "index").read_bytes(), original_index)
        self.assertEqual(self.patch.read_bytes(), original_patch)

    def test_extra_edit_in_same_patched_file_is_rejected(self):
        apply_recorded_patch(self.source, self.patch, self.command_runner)
        changed = self.patched.replace("unchanged", "preserve my local work")
        self.file.write_text(changed)
        with self.assertRaisesRegex(SystemExit, "tracked edits"):
            apply_recorded_patch(self.source, self.patch, self.command_runner)
        self.assertEqual(self.file.read_text(), changed)

    def test_unrecorded_edits_are_preserved_before_patch_application(self):
        changed = self.original + "independent edit\n"
        self.file.write_text(changed)
        with self.assertRaisesRegex(SystemExit, "tracked edits"):
            apply_recorded_patch(self.source, self.patch, self.command_runner)
        self.assertEqual(self.file.read_text(), changed)

    def test_empty_patch_accepts_pristine_source_and_rejects_edits(self):
        self.patch.write_text("")
        self.assertEqual(apply_recorded_patch(self.source, self.patch, self.command_runner), "not_required")
        self.file.write_text(self.patched)
        with self.assertRaisesRegex(SystemExit, "recorded patch is empty"):
            apply_recorded_patch(self.source, self.patch, self.command_runner)
        self.assertEqual(self.file.read_text(), self.patched)

    def test_repeated_builds_keep_previous_evidence(self):
        evidence = self.root / "provenance"
        evidence.mkdir()
        old = evidence / "environment.json"
        old.write_text("original evidence\n")
        first = create_record_directory(evidence)
        saved = first / "build.stdout.txt"
        saved.write_text("first rebuild\n")
        second = create_record_directory(evidence)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, evidence)
        self.assertEqual(second.parent, evidence)
        self.assertEqual(saved.read_text(), "first rebuild\n")
        self.assertEqual(old.read_text(), "original evidence\n")


if __name__ == "__main__":
    unittest.main()
