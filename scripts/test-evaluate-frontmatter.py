#!/usr/bin/env python3
"""schemaと型付きwhereの独立oracle、および境界fixtureの数え方を確認する。"""
import copy
import json
from pathlib import Path
import unittest

import evaluate_frontmatter as evaluation


class FrontmatterOracleTest(unittest.TestCase):
    def test_schema_mixed_types_missing_null_and_empty_array(self):
        schema = evaluation.expected_schema(evaluation.fixture_documents())
        self.assertEqual(schema["document_count"], 12)
        self.assertEqual(schema["frontmatter_count"], 10)
        self.assertEqual(schema["builtin_fields"], evaluation.expected_builtin_fields(12))
        fields = {item["pointer"]: item for item in schema["fields"]}
        self.assertEqual(fields["/count"], {"pointer": "/count", "types": ["float", "integer", "string"], "present": 4, "missing": 8, "null_count": 0, "element_types": []})
        self.assertEqual(fields["/nullable"]["null_count"], 2)
        self.assertEqual(fields["/nullable"]["missing"], 9)
        self.assertEqual(fields["/tags"]["types"], ["array", "string"])
        self.assertEqual(fields["/tags"]["element_types"], ["boolean", "float", "integer", "null", "string"])
        self.assertEqual(fields["/empty_list"]["element_types"], [])
        self.assertIn("/a~1b/~0key", fields)
        self.assertNotIn("", fields)
        self.assertEqual([item["pointer"] for item in schema["fields"]], sorted(fields, key=lambda key: key.encode()))

    def test_empty_schema_keeps_builtin_string_definitions(self):
        self.assertEqual(evaluation.expected_schema([]), {"document_count": 0, "frontmatter_count": 0, "fields": [], "builtin_fields": [
            {"selector": "file:/name", "types": ["string"], "present": 0, "missing": 0, "null_count": 0, "element_types": []},
            {"selector": "file:/path", "types": ["string"], "present": 0, "missing": 0, "null_count": 0, "element_types": []},
        ]})

    def test_object_and_nested_arrays_are_not_unsupported_fixtures(self):
        cases = {case["id"] for case in evaluation.invalid_documents()}
        self.assertNotIn("array-object", cases)
        self.assertNotIn("array-array", cases)
        self.assertIn("duplicate-nested", cases)
        self.assertIn("tag", cases)

    def test_typed_equality_does_not_use_python_numeric_coercion(self):
        self.assertFalse(evaluation.exact_scalar(1, 1.0))
        self.assertFalse(evaluation.exact_scalar(True, 1))
        self.assertFalse(evaluation.exact_scalar(evaluation.MISSING, None))
        self.assertTrue(evaluation.exact_scalar(None, None))
        documents = evaluation.fixture_documents()
        matches = lambda value: [doc["path"] for doc in documents if evaluation.where_matches(doc["metadata"], [("/tags", value)])]
        self.assertEqual(matches(1), ["a.md", "g.md"])
        self.assertEqual(matches(1.0), ["a.md", "h.md"])
        self.assertEqual(matches(True), ["i.md"])
        self.assertEqual(matches(None), ["a.md", "j.md"])

    def test_pointer_escapes_and_missing(self):
        metadata = {"a/b": {"~key": "value"}, "": "empty-key", "=": "equal"}
        self.assertEqual(evaluation.resolve(metadata, "/a~1b/~0key"), "value")
        self.assertEqual(evaluation.resolve(metadata, "/"), "empty-key")
        self.assertEqual(evaluation.resolve(metadata, "/="), "equal")
        self.assertIs(evaluation.resolve(metadata, "/a/b"), evaluation.MISSING)
        with self.assertRaises(ValueError):
            evaluation.resolve(metadata, "/a~2b")

    def test_manual_cases_agree_with_typed_metadata_and_original_lines(self):
        documents = evaluation.fixture_documents()
        for mode in ("literal", "normalized"):
            for case in evaluation.fixture_cases():
                _, actual = evaluation.original_output(documents, case["terms"], case["filters"], mode, Path("/synthetic"))
                expected = case["expected"][mode] if type(case["expected"]) is dict else case["expected"]
                self.assertEqual(actual, expected, case["id"])
        b = next(doc for doc in documents if doc["path"] == "b.md")
        output, _ = evaluation.original_output([b], ["ロ"], [], "normalized", Path("/synthetic"))
        original_line = b["raw"].splitlines().index("BodyNeedle 前㌔後") + 1
        self.assertEqual(output.decode(), f"/synthetic/b.md:{original_line}: BodyNeedle 前㌔後 \n")

    def test_raw_postings_are_a_superset_but_transform_only_eligible_bodies(self):
        documents = [evaluation.document("a.md", "header: target\nid: 1\n", "absent\n", {"header": "target", "id": 1}),
                     evaluation.document("b.md", "header: target\nid: 2\n", "target\n", {"header": "target", "id": 2})]
        case = {"terms": ["target"], "filters": [("/id", 1)], "frontmatter": True}
        hits, counts = evaluation.expected_performance(documents, case, evaluation.CONFIGURATIONS[1], "search")
        self.assertEqual(hits, [])
        self.assertEqual(counts["candidate_count"], 2)
        self.assertEqual(counts["documents_verified"], 1)
        self.assertEqual(counts["normalized_document_count"], 1)
        self.assertEqual(counts["normalized_input_bytes"], len("absent\n".encode()))

    def test_resource_fixtures_are_exact_boundaries(self):
        cases = {case["id"]: case for case in evaluation.resource_cases()}

        def nodes(value):
            if type(value) is dict:
                return 1 + sum(1 + nodes(child) for child in value.values())
            if type(value) is list:
                return 1 + sum(map(nodes, value))
            return 1

        def depth(value, current=1):
            children = value.values() if type(value) is dict else value if type(value) is list else []
            return max([current, *[depth(child, current + 1) for child in children]])

        for maximum in (4096, 4097):
            self.assertEqual(nodes(cases[f"nodes-{maximum}"]["documents"][0]["metadata"]), maximum)
            key = next(iter(cases[f"pointer-{maximum}"]["documents"][0]["metadata"]))
            self.assertEqual(len(("/" + evaluation.escape(key)).encode()), maximum)
        for maximum in (16, 17):
            self.assertEqual(depth(cases[f"depth-{maximum}"]["documents"][0]["metadata"]), maximum)
        for extra in (0, 1):
            doc = cases[f"header-{extra}"]["documents"][0]
            self.assertEqual(doc["body_offset"] - len("---\n---\n"), 256 * 1024 + extra)
            self.assertEqual(len(evaluation.expected_schema(cases[f"schema-paths-{4096 + extra}"]["documents"])["fields"]), 4096 + extra)

    def test_public_projection_drops_environment_and_diagnostics(self):
        raw = {"status": "complete", "configuration": dict(size=6, seed=1, index_repeats=1, warmups=0, repeats=1),
               "binary_sha256": dict(plain="a" * 64, profile="b" * 64, baseline=None),
               "quality": dict(passed=True, schema_passed=True, search_cases=1, invalid_document_cases=1, invalid_filter_cases=1,
                               resource_cases=[], raw_compatibility_cases=1, index_identity=True,
                               schema=dict(document_count=0, frontmatter_count=0, fields=[])),
               "corpus": dict(corpus_sha256="c" * 64, total_bytes=1), "binary_unchanged": True, "indices": [], "results": [],
               "schema_cli": {"samples": [{"elapsed_ms": 1, "peak_rss_bytes": 1, "command": ["/Users/private"]}]},
               "environment": {"hostname": "private-host"}, "failure": "private-diagnostic"}
        original = copy.deepcopy(raw)
        self.assertNotIn("private", json.dumps(evaluation.project(raw)))
        self.assertEqual(raw, original)


if __name__ == "__main__":
    unittest.main()
