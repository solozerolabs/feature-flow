"""Smoke test for tools/check_hook_output_shapes.py.

Run with: python3 -m unittest discover -s tests
"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_hook_output_shapes as probe  # noqa: E402


class MainTests(unittest.TestCase):
    def test_all_hooks_report_allowed_keys(self):
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["check_hook_output_shapes.py", str(REPO_ROOT)]
        try:
            with redirect_stdout(buf):
                exit_code = probe.main()
        finally:
            sys.argv = old_argv
        output = buf.getvalue()
        self.assertEqual(exit_code, 0, output)
        self.assertIn("ok:", output)

    def test_fails_when_no_hook_emits_json(self):
        real_run = probe.subprocess.run

        def silent_run(*args, **kwargs):
            result = real_run(*args, **kwargs)
            result.stdout = ""
            return result

        probe.subprocess.run = silent_run
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["check_hook_output_shapes.py", str(REPO_ROOT)]
        try:
            with redirect_stdout(buf):
                exit_code = probe.main()
        finally:
            sys.argv = old_argv
            probe.subprocess.run = real_run
        self.assertEqual(exit_code, 1)
        self.assertIn("no hook emitted JSON", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
