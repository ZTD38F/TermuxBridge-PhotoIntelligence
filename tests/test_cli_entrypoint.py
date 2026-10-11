"""Prevent Android launcher regressions that break direct execution in Termux.
Author: @dvkrum
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PhotoCliEntryPointTests(unittest.TestCase):
    def test_cli_uses_termux_interpreter(self):
        cli = ROOT / "photo-intelligence"
        self.assertEqual(
            cli.read_text(encoding="utf-8").splitlines()[0],
            "#!/data/data/com.termux/files/usr/bin/python3",
        )


if __name__ == "__main__":
    unittest.main()
