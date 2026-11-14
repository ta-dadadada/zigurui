#!/usr/bin/env python3
"""診断の独立検査が内容漏えい・偽行・文脈違いを拒否することを確認する。"""
import json
from pathlib import Path
import unittest

import evaluate_frontmatter_diagnostics as evaluation


def diagnostic(path, reason="InvalidFrontmatter", context="frontmatter"):
    quoted = json.dumps(str(path), ensure_ascii=False).replace("\x7f", "\\u007f")
    return f"zigurui: {context}: {reason}: file={quoted}\n".encode()


class DiagnosticOracleTest(unittest.TestCase):
    def test_quoted_control_unicode_and_backslash_path_roundtrips(self):
        path = Path('/synthetic/問題"\\\n\r\t\x1b\x7f.md')
        evaluation.check_diagnostic(1, b"", diagnostic(path), path, "InvalidFrontmatter")

    def test_raw_control_or_extra_line_is_rejected(self):
        path = Path("/synthetic/bad.md")
        for value in (b"\r", b"\t", b"\x1b", b"\x7f", b"\nforged"):
            with self.assertRaises(ValueError):
                evaluation.check_diagnostic(1, b"", diagnostic(path).replace(b"bad.md", b"bad" + value + b".md"), path, "InvalidFrontmatter")

    def test_partial_stdout_wrong_exit_or_value_leak_is_rejected(self):
        path = Path("/synthetic/bad.md")
        with self.assertRaises(ValueError):
            evaluation.check_diagnostic(1, b"partial result", diagnostic(path), path, "InvalidFrontmatter")
        with self.assertRaises(ValueError):
            evaluation.check_diagnostic(2, b"", diagnostic(path), path, "InvalidFrontmatter")
        for marker in (evaluation.HEADER_MARKER, evaluation.BODY_MARKER):
            with self.assertRaises(ValueError):
                evaluation.check_diagnostic(1, b"", diagnostic(path) + marker.encode(), path, "InvalidFrontmatter")

    def test_schema_aggregation_is_a_separate_context(self):
        path = Path("/synthetic/context.md")
        stderr = diagnostic(path, "FrontmatterTooLarge", "schema_aggregation")
        evaluation.check_diagnostic(1, b"", stderr, path, "FrontmatterTooLarge", "schema_aggregation")
        with self.assertRaises(ValueError):
            evaluation.check_diagnostic(1, b"", stderr, path, "FrontmatterTooLarge")

    def test_generic_vendor_text_or_wrong_file_is_rejected(self):
        path = Path("/synthetic/bad.md")
        for stderr in (b"zigurui: InvalidFrontmatter\n", diagnostic(path) + b"did not find expected token\n", diagnostic(Path("/synthetic/other.md"))):
            with self.assertRaises(ValueError):
                evaluation.check_diagnostic(1, b"", stderr, path, "InvalidFrontmatter")


if __name__ == "__main__":
    unittest.main()
