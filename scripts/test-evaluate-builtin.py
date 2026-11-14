#!/usr/bin/env python3
"""組み込みselectorとfrontmatter pointerを混同しない独立期待値を確認する。"""
from pathlib import Path
import unittest

import evaluate_builtin as evaluation
import evaluate_frontmatter as frontmatter


class BuiltinOracleTest(unittest.TestCase):
    def setUp(self):
        self.documents = evaluation.fixture_documents()
        self.root = Path("/synthetic")

    def test_same_basename_and_metadata_namespace_are_distinct(self):
        paths = lambda filters: [doc["path"] for doc in evaluation.select_documents(self.documents, filters, self.root)]
        self.assertEqual(paths([("file:/name", "same.md")]), ["one/same.md", "two/same.md"])
        self.assertEqual(paths([("/file/name", "fake.md")]), ["one/same.md"])
        self.assertEqual(paths([("file:/name", "fake.md")]), [])
        self.assertEqual(paths([("file:/name", "same.md"), ("/status", "draft")]), ["one/same.md"])

    def test_path_values_and_name_spelling_are_exact(self):
        paths = lambda filters: [doc["path"] for doc in evaluation.select_documents(self.documents, filters, self.root)]
        self.assertEqual(paths([("file:/path", "/synthetic/one/same.md")]), ["one/same.md"])
        self.assertEqual(paths([("file:/path", "one/same.md")]), [])
        self.assertEqual(paths([("file:/path", "/synthetic/one/../one/same.md")]), [])
        self.assertEqual(paths([("file:/name", "Guide.md")]), ["upper/Guide.md"])
        self.assertEqual(paths([("file:/name", "Ｇuide.md")]), ["width/Ｇuide.md"])
        for value in (None, True, 1, 1.0):
            self.assertEqual(paths([("file:/name", value)]), [])

    def test_unknown_selectors_fail_even_without_documents(self):
        with self.assertRaises(ValueError):
            evaluation.select_documents([], [("file:/unknown", "value")], self.root)
        with self.assertRaises(ValueError):
            evaluation.select_documents([], [("file:/name", [])], self.root)

    def test_all_handwritten_cases_match_independent_paths(self):
        for mode in ("literal", "normalized"):
            for case in evaluation.fixture_cases(self.root):
                selected = evaluation.select_documents(self.documents, case["filters"], self.root)
                _, actual = frontmatter.original_output(selected, case["terms"], [], mode, self.root, frontmatter=case["frontmatter"])
                expected = case["expected"][mode] if type(case["expected"]) is dict else case["expected"]
                self.assertEqual(actual, expected, case["id"])

    def test_schema_keeps_builtin_definitions_out_of_fm_fields(self):
        schema = frontmatter.expected_schema(self.documents)
        self.assertEqual(len(self.documents), 10)
        self.assertEqual(schema["builtin_fields"], frontmatter.expected_builtin_fields(10))
        pointers = [field["pointer"] for field in schema["fields"]]
        self.assertIn("/file/name", pointers)
        self.assertNotIn("file:/name", pointers)


if __name__ == "__main__":
    unittest.main()
