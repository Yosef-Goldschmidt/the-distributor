"""Ensure unittest discovery executes the isolated end-to-end pipeline test."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PipelineDiscoveryTest(unittest.TestCase):
    def test_isolated_pipeline_and_api_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "test_pipeline.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pipeline OK", result.stdout)
        self.assertIn("api contract OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
