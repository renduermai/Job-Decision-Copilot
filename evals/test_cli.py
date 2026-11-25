import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts" / "run.py"


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(RUN), *args], cwd=Path(__file__).resolve().parent,
                              capture_output=True, text=True)

    def test_help_from_different_cwd(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("standalone", result.stdout)
        self.assertIn("rerate", result.stdout)

    def test_version(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1.4.8", result.stdout)

    def test_missing_required_arguments_fail(self):
        result = self.run_cli("prepare")
        self.assertNotEqual(result.returncode, 0)

    def test_rebuild_and_reuse_are_rejected_together(self):
        result = self.run_cli("prepare", "--input", ".", "--profile", "missing.json", "--output", "out",
                              "--rebuild", "--reuse-from", "old")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
